"""Authoritative state store. All reads/writes go through SQLite here."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any, Iterator

from .db import Database
from .errors import DecisionError, NotFoundError, ValidationError
from .lifecycle import ensure_transition, is_node_terminal, is_workflow_terminal
from .models import (
    AgentRun,
    AgentRunStatus,
    Checkpoint,
    DecisionStatus,
    ExecutionEvent,
    HumanDecision,
    Node,
    NodeStatus,
    RetryRecord,
    Stage,
    Workflow,
    WorkflowStatus,
    new_id,
    utcnow,
)
from .rows import (
    dump as _dump,
    load as _load,
    to_agent_run as _agent_run,
    to_checkpoint as _checkpoint,
    to_decision as _decision,
    to_event as _event,
    to_node as _node,
    to_retry as _retry,
    to_stage as _stage,
    to_workflow as _workflow,
)


class StateStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---------------------------------------------------------------- helpers
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.db.transaction() as conn:
            yield conn

    def _add_event(
        self,
        conn: sqlite3.Connection,
        workflow_id: str,
        node_id: str | None,
        event_type: str,
        payload: dict[str, Any] | None = None,
        actor: str = "core",
    ) -> None:
        conn.execute(
            "INSERT INTO execution_events(workflow_id, node_id, event_type, payload, actor, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (workflow_id, node_id, event_type, _dump(payload or {}), actor, utcnow()),
        )

    def _workflow_row(self, conn: sqlite3.Connection, workflow_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"workflow not found: {workflow_id}")
        return row

    def _node_row(self, conn: sqlite3.Connection, node_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM workflow_nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"node not found: {node_id}")
        return row

    # -------------------------------------------------------------- workflows
    def create_workflow(self, name: str, goal: str, context: dict[str, Any] | None = None) -> Workflow:
        if not name or not name.strip():
            raise ValidationError("workflow name is required")
        now = utcnow()
        workflow = Workflow(
            id=new_id("wf"),
            name=name.strip(),
            goal=goal,
            status=WorkflowStatus.CREATED,
            context=context or {},
            created_at=now,
            updated_at=now,
        )
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO workflows(id, name, goal, status, context, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    workflow.id,
                    workflow.name,
                    workflow.goal,
                    workflow.status.value,
                    _dump(workflow.context),
                    workflow.created_at,
                    workflow.updated_at,
                ),
            )
            self._add_event(conn, workflow.id, None, "workflow.created", {"name": workflow.name})
        return workflow

    def get_workflow(self, workflow_id: str) -> Workflow:
        with self.db.read() as conn:
            return _workflow(self._workflow_row(conn, workflow_id))

    def list_workflows(self, status: WorkflowStatus | None = None) -> list[Workflow]:
        with self.db.read() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM workflows WHERE status = ? ORDER BY created_at, id",
                    (status.value,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM workflows ORDER BY created_at, id").fetchall()
        return [_workflow(row) for row in rows]

    def list_active_workflows(self) -> list[Workflow]:
        terminal = [s.value for s in WorkflowStatus if is_workflow_terminal(s)]
        placeholders = ",".join("?" for _ in terminal)
        with self.db.read() as conn:
            rows = conn.execute(
                f"SELECT * FROM workflows WHERE status NOT IN ({placeholders}) ORDER BY created_at, id",
                terminal,
            ).fetchall()
        return [_workflow(row) for row in rows]

    def update_workflow_status(
        self,
        workflow_id: str,
        target: WorkflowStatus | str,
        reason: str = "",
        *,
        error: str | None = None,
        event_type: str = "workflow.status_changed",
        actor: str = "core",
    ) -> Workflow:
        target = WorkflowStatus(target)
        with self.db.transaction() as conn:
            row = self._workflow_row(conn, workflow_id)
            current = WorkflowStatus(row["status"])
            if current is target:
                return _workflow(row)
            ensure_transition("workflow", current, target)
            now = utcnow()
            started_at = row["started_at"]
            if started_at is None and target is WorkflowStatus.RUNNING:
                started_at = now
            finished_at = row["finished_at"]
            if is_workflow_terminal(target):
                finished_at = now
            conn.execute(
                "UPDATE workflows SET status = ?, updated_at = ?, started_at = ?, finished_at = ?, error = ?"
                " WHERE id = ?",
                (target.value, now, started_at, finished_at, error, workflow_id),
            )
            self._add_event(
                conn,
                workflow_id,
                None,
                event_type,
                {"from": current.value, "to": target.value, "reason": reason},
                actor,
            )
            return _workflow(self._workflow_row(conn, workflow_id))

    # ---------------------------------------------------------------- stages
    def create_stage(self, workflow_id: str, name: str, sequence: int = 0) -> Stage:
        stage = Stage(
            id=new_id("stg"),
            workflow_id=workflow_id,
            name=name,
            sequence=sequence,
            created_at=utcnow(),
        )
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO workflow_stages(id, workflow_id, name, sequence, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (stage.id, stage.workflow_id, stage.name, stage.sequence, stage.status, stage.created_at),
            )
            self._add_event(conn, workflow_id, None, "stage.created", {"stage_id": stage.id, "name": name})
        return stage

    def list_stages(self, workflow_id: str) -> list[Stage]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_stages WHERE workflow_id = ? ORDER BY sequence, created_at",
                (workflow_id,),
            ).fetchall()
        return [_stage(row) for row in rows]

    # ----------------------------------------------------------------- nodes
    def create_nodes(self, workflow_id: str, specs: list[dict[str, Any]], stage_id: str | None = None) -> list[Node]:
        if not specs:
            raise ValidationError("at least one node is required")
        now = utcnow()
        created: list[Node] = []
        with self.db.transaction() as conn:
            self._workflow_row(conn, workflow_id)
            for spec in specs:
                node_id = spec.get("id") or new_id("node")
                node = Node(
                    id=node_id,
                    workflow_id=workflow_id,
                    stage_id=spec.get("stage_id", stage_id),
                    name=spec.get("name") or node_id,
                    status=NodeStatus.PENDING,
                    dependencies=list(spec.get("dependencies") or []),
                    execution_config=dict(spec.get("execution_config") or {}),
                    retry_policy=dict(spec.get("retry_policy") or {}),
                    input=dict(spec.get("input") or {}),
                    required=bool(spec.get("required", True)),
                    idempotency_key=spec.get("idempotency_key"),
                    created_at=now,
                    updated_at=now,
                )
                conn.execute(
                    "INSERT INTO workflow_nodes(id, workflow_id, stage_id, name, status, dependencies,"
                    " execution_config, retry_policy, input, required, idempotency_key, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        node.id,
                        node.workflow_id,
                        node.stage_id,
                        node.name,
                        node.status.value,
                        _dump(node.dependencies),
                        _dump(node.execution_config),
                        _dump(node.retry_policy),
                        _dump(node.input),
                        int(node.required),
                        node.idempotency_key,
                        node.created_at,
                        node.updated_at,
                    ),
                )
                created.append(node)
            self._add_event(conn, workflow_id, None, "nodes.created", {"node_ids": [n.id for n in created]})
        return created

    def get_node(self, node_id: str) -> Node:
        with self.db.read() as conn:
            return _node(self._node_row(conn, node_id))

    def list_nodes(self, workflow_id: str, status: NodeStatus | None = None) -> list[Node]:
        with self.db.read() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM workflow_nodes WHERE workflow_id = ? AND status = ? ORDER BY created_at, id",
                    (workflow_id, status.value),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM workflow_nodes WHERE workflow_id = ? ORDER BY created_at, id",
                    (workflow_id,),
                ).fetchall()
        return [_node(row) for row in rows]

    def update_node_status(
        self,
        node_id: str,
        target: NodeStatus | str,
        reason: str = "",
        *,
        error: str | None = None,
        output: dict[str, Any] | None = None,
        event_type: str = "node.status_changed",
        actor: str = "core",
    ) -> Node:
        target = NodeStatus(target)
        with self.db.transaction() as conn:
            row = self._node_row(conn, node_id)
            current = NodeStatus(row["status"])
            if current is target:
                return _node(row)
            ensure_transition("node", current, target)
            now = utcnow()
            started_at = row["started_at"]
            if started_at is None and target is NodeStatus.RUNNING:
                started_at = now
            finished_at = now if is_node_terminal(target) else row["finished_at"]
            conn.execute(
                "UPDATE workflow_nodes SET status = ?, updated_at = ?, started_at = ?, finished_at = ?,"
                " error = ?, output = ? WHERE id = ?",
                (
                    target.value,
                    now,
                    started_at,
                    finished_at,
                    error,
                    _dump(output) if output is not None else row["output"],
                    node_id,
                ),
            )
            self._add_event(
                conn,
                row["workflow_id"],
                node_id,
                event_type,
                {"from": current.value, "to": target.value, "reason": reason},
                actor,
            )
            return _node(self._node_row(conn, node_id))

    def claim_node(self, node_id: str, actor: str = "core") -> Node | None:
        """Atomically move READY -> RUNNING. None means it was already claimed."""
        with self.db.transaction() as conn:
            row = self._node_row(conn, node_id)
            if NodeStatus(row["status"]) is not NodeStatus.READY:
                return None
            now = utcnow()
            cursor = conn.execute(
                "UPDATE workflow_nodes SET status = ?, updated_at = ?, started_at = ?"
                " WHERE id = ? AND status = ?",
                (NodeStatus.RUNNING.value, now, row["started_at"] or now, node_id, NodeStatus.READY.value),
            )
            if cursor.rowcount != 1:
                return None
            self._add_event(conn, row["workflow_id"], node_id, "node.claimed", {}, actor)
            return _node(self._node_row(conn, node_id))

    def increment_attempts(self, node_id: str) -> int:
        with self.db.transaction() as conn:
            self._node_row(conn, node_id)
            conn.execute(
                "UPDATE workflow_nodes SET attempts = attempts + 1, updated_at = ? WHERE id = ?",
                (utcnow(), node_id),
            )
            row = conn.execute("SELECT attempts FROM workflow_nodes WHERE id = ?", (node_id,)).fetchone()
            return int(row["attempts"])

    # ------------------------------------------------------------ agent runs
    def create_agent_run(
        self,
        workflow_id: str,
        node_id: str,
        adapter: str,
        attempt: int,
        *,
        prompt: str = "",
        workdir: str = "",
        timeout_seconds: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRun:
        now = utcnow()
        run = AgentRun(
            id=new_id("run"),
            workflow_id=workflow_id,
            node_id=node_id,
            adapter=adapter,
            status=AgentRunStatus.RUNNING,
            attempt=attempt,
            prompt=prompt,
            workdir=workdir,
            timeout_seconds=timeout_seconds,
            started_at=now,
            heartbeat_at=now,
            metadata=metadata or {},
        )
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO agent_runs(id, workflow_id, node_id, adapter, status, attempt, prompt, workdir,"
                " timeout_seconds, started_at, heartbeat_at, metadata)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id,
                    run.workflow_id,
                    run.node_id,
                    run.adapter,
                    run.status.value,
                    run.attempt,
                    run.prompt,
                    run.workdir,
                    run.timeout_seconds,
                    run.started_at,
                    run.heartbeat_at,
                    _dump(run.metadata),
                ),
            )
            self._add_event(conn, workflow_id, node_id, "agent.started", {"run_id": run.id, "attempt": attempt})
        return run

    def update_agent_run(
        self,
        run_id: str,
        target: AgentRunStatus | str,
        *,
        exit_code: int | None = None,
        output: str | None = None,
        error: str | None = None,
        pid: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentRun:
        target = AgentRunStatus(target)
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"agent run not found: {run_id}")
            current = AgentRunStatus(row["status"])
            if current is target:
                return _agent_run(row)
            ensure_transition("agent_run", current, target)
            now = utcnow()
            terminal = target not in (AgentRunStatus.CREATED, AgentRunStatus.RUNNING)
            conn.execute(
                "UPDATE agent_runs SET status = ?, exit_code = ?, output = ?, error = ?, pid = ?,"
                " metadata = ?, heartbeat_at = ?, finished_at = ? WHERE id = ?",
                (
                    target.value,
                    exit_code if exit_code is not None else row["exit_code"],
                    output if output is not None else row["output"],
                    error,
                    pid if pid is not None else row["pid"],
                    _dump(metadata) if metadata is not None else row["metadata"],
                    now,
                    now if terminal else row["finished_at"],
                    run_id,
                ),
            )
            self._add_event(
                conn,
                row["workflow_id"],
                row["node_id"],
                "agent.status_changed",
                {"run_id": run_id, "from": current.value, "to": target.value},
            )
            row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
            return _agent_run(row)

    def heartbeat_agent_run(self, run_id: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE agent_runs SET heartbeat_at = ? WHERE id = ?", (utcnow(), run_id))

    def set_agent_run_process(
        self, run_id: str, pid: int, process_start_time: str | None
    ) -> bool:
        """Persist the process identity of a live run. Returns False if the run
        is no longer RUNNING (for example, already cancelled or interrupted)."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"agent run not found: {run_id}")
            if AgentRunStatus(row["status"]) is not AgentRunStatus.RUNNING:
                return False
            now = utcnow()
            conn.execute(
                "UPDATE agent_runs SET pid = ?, process_start_time = ?, heartbeat_at = ?"
                " WHERE id = ?",
                (pid, process_start_time, now, run_id),
            )
            self._add_event(
                conn,
                row["workflow_id"],
                row["node_id"],
                "agent.process_started",
                {"run_id": run_id, "pid": pid, "process_start_time": process_start_time},
            )
            return True

    def get_agent_run(self, run_id: str) -> AgentRun:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"agent run not found: {run_id}")
        return _agent_run(row)

    def list_agent_runs(
        self,
        workflow_id: str | None = None,
        node_id: str | None = None,
        status: AgentRunStatus | None = None,
    ) -> list[AgentRun]:
        clauses, params = [], []
        if workflow_id:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if node_id:
            clauses.append("node_id = ?")
            params.append(node_id)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.read() as conn:
            rows = conn.execute(
                f"SELECT * FROM agent_runs {where} ORDER BY started_at, id", params
            ).fetchall()
        return [_agent_run(row) for row in rows]

    # ---------------------------------------------------------------- events
    def append_event(
        self,
        workflow_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        node_id: str | None = None,
        actor: str = "core",
    ) -> None:
        with self.db.transaction() as conn:
            self._add_event(conn, workflow_id, node_id, event_type, payload, actor)

    def list_events(self, workflow_id: str, limit: int = 200) -> list[ExecutionEvent]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_events WHERE workflow_id = ? ORDER BY id DESC LIMIT ?",
                (workflow_id, limit),
            ).fetchall()
        return [_event(row) for row in reversed(rows)]

    # ------------------------------------------------------------- decisions
    def create_decision(
        self,
        workflow_id: str,
        node_id: str | None,
        decision_type: str,
        question: str,
        *,
        context: dict[str, Any] | None = None,
        options: list[dict[str, Any]] | None = None,
        expires_at: str | None = None,
    ) -> HumanDecision:
        if not question.strip():
            raise ValidationError("decision question is required")
        decision = HumanDecision(
            id=new_id("dec"),
            workflow_id=workflow_id,
            node_id=node_id,
            decision_type=decision_type,
            question=question,
            status=DecisionStatus.PENDING,
            context=context or {},
            options=options or [],
            created_at=utcnow(),
            expires_at=expires_at,
        )
        try:
            with self.db.transaction() as conn:
                conn.execute(
                    "INSERT INTO human_decisions(id, workflow_id, node_id, decision_type, question, context,"
                    " options, status, expires_at, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        decision.id,
                        workflow_id,
                        node_id,
                        decision_type,
                        question,
                        _dump(decision.context),
                        _dump(decision.options),
                        decision.status.value,
                        decision.expires_at,
                        decision.created_at,
                    ),
                )
                self._add_event(
                    conn,
                    workflow_id,
                    node_id,
                    "human_decision.requested",
                    {"decision_id": decision.id, "question": question},
                )
        except sqlite3.IntegrityError as exc:
            raise DecisionError(f"a pending decision already exists for node {node_id}") from exc
        return decision

    def get_decision(self, decision_id: str) -> HumanDecision:
        with self.db.read() as conn:
            row = conn.execute("SELECT * FROM human_decisions WHERE id = ?", (decision_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"decision not found: {decision_id}")
        return _decision(row)

    def list_decisions(
        self, workflow_id: str, status: DecisionStatus | None = None
    ) -> list[HumanDecision]:
        with self.db.read() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM human_decisions WHERE workflow_id = ? AND status = ? ORDER BY created_at, id",
                    (workflow_id, status.value),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM human_decisions WHERE workflow_id = ? ORDER BY created_at, id",
                    (workflow_id,),
                ).fetchall()
        return [_decision(row) for row in rows]

    def find_pending_decision(self, node_id: str) -> HumanDecision | None:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM human_decisions WHERE node_id = ? AND status = ? ORDER BY created_at DESC LIMIT 1",
                (node_id, DecisionStatus.PENDING.value),
            ).fetchone()
        return _decision(row) if row else None

    def latest_decision_for_node(self, node_id: str) -> HumanDecision | None:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM human_decisions WHERE node_id = ? ORDER BY created_at DESC LIMIT 1",
                (node_id,),
            ).fetchone()
        return _decision(row) if row else None

    def finalize_decision(
        self,
        decision_id: str,
        target: DecisionStatus | str,
        *,
        selected_option: str | None = None,
        payload: dict[str, Any] | None = None,
        resolved_by: str | None = None,
        note: str | None = None,
        event_type: str | None = None,
    ) -> HumanDecision:
        target = DecisionStatus(target)
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM human_decisions WHERE id = ?", (decision_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"decision not found: {decision_id}")
            current = DecisionStatus(row["status"])
            ensure_transition("decision", current, target)
            now = utcnow()
            conn.execute(
                "UPDATE human_decisions SET status = ?, selected_option = ?, decision_payload = ?,"
                " resolved_at = ?, resolved_by = ?, note = ? WHERE id = ?",
                (
                    target.value,
                    selected_option,
                    _dump(payload or {}),
                    now,
                    resolved_by,
                    note,
                    decision_id,
                ),
            )
            self._add_event(
                conn,
                row["workflow_id"],
                row["node_id"],
                event_type or f"human_decision.{target.value.lower()}",
                {
                    "decision_id": decision_id,
                    "selected_option": selected_option,
                    "resolved_by": resolved_by,
                },
            )
            row = conn.execute("SELECT * FROM human_decisions WHERE id = ?", (decision_id,)).fetchone()
            return _decision(row)

    # ------------------------------------------------------------- checkpoints
    def build_state_snapshot(self, conn: sqlite3.Connection, workflow_id: str) -> dict[str, Any]:
        workflow = _workflow(self._workflow_row(conn, workflow_id))
        nodes = [
            _node(row)
            for row in conn.execute(
                "SELECT * FROM workflow_nodes WHERE workflow_id = ? ORDER BY created_at, id", (workflow_id,)
            )
        ]
        stages = [
            _stage(row)
            for row in conn.execute(
                "SELECT * FROM workflow_stages WHERE workflow_id = ? ORDER BY sequence, created_at",
                (workflow_id,),
            )
        ]
        decisions = [
            _decision(row)
            for row in conn.execute(
                "SELECT * FROM human_decisions WHERE workflow_id = ? ORDER BY created_at, id", (workflow_id,)
            )
        ]
        runs = [
            _agent_run(row)
            for row in conn.execute(
                "SELECT * FROM agent_runs WHERE workflow_id = ? ORDER BY started_at, id", (workflow_id,)
            )
        ]
        events = [
            _event(row)
            for row in reversed(
                conn.execute(
                    "SELECT * FROM execution_events WHERE workflow_id = ? ORDER BY id DESC LIMIT 50",
                    (workflow_id,),
                ).fetchall()
            )
        ]
        return {
            "workflow": asdict(workflow),
            "stages": [asdict(s) for s in stages],
            "nodes": [asdict(n) for n in nodes],
            "decisions": [asdict(d) for d in decisions],
            "agent_runs": [asdict(r) for r in runs],
            "recent_events": [asdict(e) for e in events],
        }

    def insert_checkpoint(
        self,
        conn: sqlite3.Connection,
        workflow_id: str,
        node_id: str | None,
        reason: str,
        snapshot: dict[str, Any],
        context: dict[str, Any],
    ) -> Checkpoint:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS seq FROM checkpoints WHERE workflow_id = ?",
            (workflow_id,),
        ).fetchone()
        checkpoint = Checkpoint(
            id=new_id("ckpt"),
            workflow_id=workflow_id,
            node_id=node_id,
            reason=reason,
            sequence=int(row["seq"]) + 1,
            state_snapshot=snapshot,
            context=context,
            created_at=utcnow(),
        )
        conn.execute(
            "INSERT INTO checkpoints(id, workflow_id, node_id, reason, sequence, state_snapshot, context, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                checkpoint.id,
                workflow_id,
                node_id,
                reason,
                checkpoint.sequence,
                _dump(checkpoint.state_snapshot),
                _dump(checkpoint.context),
                checkpoint.created_at,
            ),
        )
        self._add_event(
            conn,
            workflow_id,
            node_id,
            "checkpoint.saved",
            {"checkpoint_id": checkpoint.id, "reason": reason, "sequence": checkpoint.sequence},
        )
        return checkpoint

    def get_latest_checkpoint(self, workflow_id: str) -> Checkpoint | None:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE workflow_id = ? ORDER BY sequence DESC LIMIT 1",
                (workflow_id,),
            ).fetchone()
        return _checkpoint(row) if row else None

    def list_checkpoints(self, workflow_id: str) -> list[Checkpoint]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE workflow_id = ? ORDER BY sequence", (workflow_id,)
            ).fetchall()
        return [_checkpoint(row) for row in rows]

    # --------------------------------------------------------- retry records
    def record_retry(
        self,
        workflow_id: str,
        node_id: str,
        attempt: int,
        reason: str,
        *,
        agent_run_id: str | None = None,
        next_retry_at: str | None = None,
    ) -> RetryRecord:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO retry_records(workflow_id, node_id, agent_run_id, attempt, reason,"
                " next_retry_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (workflow_id, node_id, agent_run_id, attempt, reason, next_retry_at, utcnow()),
            )
            row = conn.execute(
                "SELECT * FROM retry_records WHERE node_id = ? AND attempt = ?", (node_id, attempt)
            ).fetchone()
        return _retry(row)

    def list_retries(self, workflow_id: str) -> list[RetryRecord]:
        with self.db.read() as conn:
            rows = conn.execute(
                "SELECT * FROM retry_records WHERE workflow_id = ? ORDER BY id", (workflow_id,)
            ).fetchall()
        return [_retry(row) for row in rows]

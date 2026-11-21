"""Workflow/Plan binding, node definition and transactional change primitives."""

from __future__ import annotations

import sqlite3
from typing import Any

from .errors import ValidationError
from .lifecycle import ensure_transition, is_node_terminal, is_workflow_terminal
from .models import Node, NodeStatus, Workflow, WorkflowStatus, new_id, utcnow
from .rows import dump as _dump, to_node as _node, to_workflow as _workflow


class PlanWorkflowMixin:
    # ------------------------------------------------------------- workflows
    def _insert_workflow_in_conn(
        self,
        conn: sqlite3.Connection,
        name: str,
        goal: str,
        context: dict[str, Any] | None = None,
        *,
        plan_id: str | None = None,
        plan_snapshot_id: str | None = None,
        plan_version: int | None = None,
    ) -> Workflow:
        if not name or not name.strip():
            raise ValidationError("workflow name is required")
        now = utcnow()
        workflow = Workflow(
            id=new_id("wf"),
            name=name.strip(),
            goal=goal,
            status=WorkflowStatus.CREATED,
            context=context or {},
            plan_id=plan_id,
            plan_snapshot_id=plan_snapshot_id,
            plan_version=plan_version,
            created_at=now,
            updated_at=now,
        )
        conn.execute(
            "INSERT INTO workflows(id, name, goal, status, context, plan_id,"
            " plan_snapshot_id, plan_version, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                workflow.id,
                workflow.name,
                workflow.goal,
                workflow.status.value,
                _dump(workflow.context),
                workflow.plan_id,
                workflow.plan_snapshot_id,
                workflow.plan_version,
                workflow.created_at,
                workflow.updated_at,
            ),
        )
        self._add_event(
            conn,
            workflow.id,
            None,
            "workflow.created",
            {
                "name": workflow.name,
                "plan_id": plan_id,
                "plan_snapshot_id": plan_snapshot_id,
                "plan_version": plan_version,
            },
        )
        return workflow

    def create_workflow(
        self,
        name: str,
        goal: str,
        context: dict[str, Any] | None = None,
        *,
        plan_id: str | None = None,
        plan_snapshot_id: str | None = None,
        plan_version: int | None = None,
    ) -> Workflow:
        with self.db.transaction() as conn:
            return self._insert_workflow_in_conn(
                conn,
                name,
                goal,
                context,
                plan_id=plan_id,
                plan_snapshot_id=plan_snapshot_id,
                plan_version=plan_version,
            )

    def create_workflow_with_nodes(
        self,
        name: str,
        goal: str,
        nodes: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
        *,
        plan_id: str | None = None,
        plan_snapshot_id: str | None = None,
        plan_version: int | None = None,
    ) -> Workflow:
        """Workflow, stage and nodes are one transaction: a partially created
        workflow can never be observed by a concurrent creator."""
        with self.db.transaction() as conn:
            workflow = self._insert_workflow_in_conn(
                conn,
                name,
                goal,
                context,
                plan_id=plan_id,
                plan_snapshot_id=plan_snapshot_id,
                plan_version=plan_version,
            )
            stage_id = new_id("stg")
            conn.execute(
                "INSERT INTO workflow_stages(id, workflow_id, name, sequence, status, created_at)"
                " VALUES (?, ?, ?, 0, 'PENDING', ?)",
                (stage_id, workflow.id, "main", utcnow()),
            )
            self._add_event(
                conn, workflow.id, None, "stage.created", {"stage_id": stage_id, "name": "main"}
            )
            self.create_nodes_in_conn(conn, workflow.id, nodes, stage_id=stage_id)
            return _workflow(self._workflow_row(conn, workflow.id))

    def find_workflow_by_snapshot(self, snapshot_id: str) -> Workflow | None:
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT * FROM workflows WHERE plan_snapshot_id = ?", (snapshot_id,)
            ).fetchone()
        return _workflow(row) if row else None

    def update_workflow_plan_snapshot_in_conn(
        self, conn: sqlite3.Connection, workflow_id: str, snapshot_id: str, version: int
    ) -> Workflow:
        row = self._workflow_row(conn, workflow_id)
        if is_workflow_terminal(WorkflowStatus(row["status"])):
            raise ValidationError("cannot rebind a terminal workflow")
        conn.execute(
            "UPDATE workflows SET plan_snapshot_id = ?, plan_version = ?, updated_at = ?"
            " WHERE id = ?",
            (snapshot_id, version, utcnow(), workflow_id),
        )
        self._add_event(
            conn,
            workflow_id,
            None,
            "workflow.plan_snapshot_rebound",
            {"plan_snapshot_id": snapshot_id, "plan_version": version},
        )
        return _workflow(self._workflow_row(conn, workflow_id))

    def update_workflow_plan_snapshot(
        self, workflow_id: str, snapshot_id: str, version: int
    ) -> Workflow:
        with self.db.transaction() as conn:
            return self.update_workflow_plan_snapshot_in_conn(
                conn, workflow_id, snapshot_id, version
            )

    # ----------------------------------------------------------------- nodes
    def create_nodes_in_conn(
        self,
        conn: sqlite3.Connection,
        workflow_id: str,
        specs: list[dict[str, Any]],
        stage_id: str | None = None,
        *,
        actor: str = "core",
    ) -> list[Node]:
        if not specs:
            raise ValidationError("at least one node is required")
        now = utcnow()
        self._workflow_row(conn, workflow_id)
        node_ids: list[str] = []
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
            node_ids.append(node.id)
        self._add_event(
            conn, workflow_id, None, "nodes.created", {"node_ids": node_ids}, actor
        )
        return [_node(self._node_row(conn, node_id)) for node_id in node_ids]

    def create_nodes(
        self, workflow_id: str, specs: list[dict[str, Any]], stage_id: str | None = None
    ) -> list[Node]:
        with self.db.transaction() as conn:
            return self.create_nodes_in_conn(conn, workflow_id, specs, stage_id=stage_id)

    def set_node_required_in_conn(
        self, conn: sqlite3.Connection, node_id: str, required: bool
    ) -> Node:
        row = self._node_row(conn, node_id)
        conn.execute(
            "UPDATE workflow_nodes SET required = ?, updated_at = ? WHERE id = ?",
            (int(required), utcnow(), node_id),
        )
        self._add_event(
            conn,
            row["workflow_id"],
            node_id,
            "node.requirement_changed",
            {"required": bool(required)},
        )
        return _node(self._node_row(conn, node_id))

    def set_node_required(self, node_id: str, required: bool) -> Node:
        with self.db.transaction() as conn:
            return self.set_node_required_in_conn(conn, node_id, required)

    def update_node_status_in_conn(
        self,
        conn: sqlite3.Connection,
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
        with self.db.transaction() as conn:
            return self.update_node_status_in_conn(
                conn,
                node_id,
                target,
                reason,
                error=error,
                output=output,
                event_type=event_type,
                actor=actor,
            )

    def update_node_definition_in_conn(
        self,
        conn: sqlite3.Connection,
        node_id: str,
        *,
        name: str | None = None,
        dependencies: list[str] | None = None,
        execution_config: dict[str, Any] | None = None,
        retry_policy: dict[str, Any] | None = None,
        input: dict[str, Any] | None = None,
        required: bool | None = None,
    ) -> Node:
        row = self._node_row(conn, node_id)
        status = NodeStatus(row["status"])
        if status in (
            NodeStatus.RUNNING,
            NodeStatus.SUCCEEDED,
            NodeStatus.FAILED,
            NodeStatus.SKIPPED,
            NodeStatus.CANCELLED,
        ):
            raise ValidationError(
                f"node {node_id} is {status.value}; definition cannot be changed"
            )
        conn.execute(
            "UPDATE workflow_nodes SET name = ?, dependencies = ?, execution_config = ?,"
            " retry_policy = ?, input = ?, required = ?, updated_at = ? WHERE id = ?",
            (
                name if name is not None else row["name"],
                _dump(dependencies) if dependencies is not None else row["dependencies"],
                _dump(execution_config)
                if execution_config is not None
                else row["execution_config"],
                _dump(retry_policy) if retry_policy is not None else row["retry_policy"],
                _dump(input) if input is not None else row["input"],
                int(required) if required is not None else row["required"],
                utcnow(),
                node_id,
            ),
        )
        self._add_event(
            conn,
            row["workflow_id"],
            node_id,
            "node.definition_updated",
            {"status": status.value},
        )
        return _node(self._node_row(conn, node_id))

    def update_node_definition(
        self,
        node_id: str,
        *,
        name: str | None = None,
        dependencies: list[str] | None = None,
        execution_config: dict[str, Any] | None = None,
        retry_policy: dict[str, Any] | None = None,
        input: dict[str, Any] | None = None,
        required: bool | None = None,
    ) -> Node:
        with self.db.transaction() as conn:
            return self.update_node_definition_in_conn(
                conn,
                node_id,
                name=name,
                dependencies=dependencies,
                execution_config=execution_config,
                retry_policy=retry_policy,
                input=input,
                required=required,
            )

"""Workflow engine orchestration: lifecycle + DAG + agents + verify + WCP."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .agents.base import AgentAdapter, AgentResult
from .agents.manager import AgentExecutionManager
from .config import DevFlowConfig
from .continuation import CheckpointManager
from .db import Database
from .errors import DevFlowError, InvalidTransition, ValidationError
from .hitl import HumanDecisionManager
from .lifecycle import is_workflow_terminal
from .models import (
    AgentRunStatus,
    DecisionStatus,
    Node,
    NodeStatus,
    RetryRecord,
    Workflow,
    WorkflowStatus,
    parse_time,
)
from .recovery import RecoveryManager, RetryPolicy
from .scheduler import DAGScheduler
from .state import StateStore
from .verifier import CompletionVerifier, DefaultVerifier, Verifier

logger = logging.getLogger(__name__)

_MAX_BACKOFF_WAIT = 60.0
_MAX_PROMPT_CONTEXT = 8000


class WorkflowEngine:
    def __init__(
        self,
        config: DevFlowConfig,
        adapter: AgentAdapter,
        verifier: Verifier | None = None,
    ) -> None:
        self.config = config
        self.db = Database(config.db_path)
        self.db.migrate()
        self.store = StateStore(self.db)
        self.scheduler = DAGScheduler(self.store)
        self.verifier = verifier or DefaultVerifier()
        self.completion = CompletionVerifier()
        self.adapter = adapter
        self.agents = AgentExecutionManager(self.store, adapter)
        self.decisions = HumanDecisionManager(self.store)
        self.checkpoints = CheckpointManager(self.store)
        self.recovery = RecoveryManager(
            self.store,
            self.scheduler,
            self.verifier,
            grace_seconds=config.recovery_grace,
        )

    # ------------------------------------------------------------ lifecycle
    def create_workflow(
        self,
        name: str,
        goal: str,
        nodes: list[dict[str, Any]],
        *,
        context: dict[str, Any] | None = None,
    ) -> Workflow:
        if not nodes:
            raise ValidationError("workflow needs at least one node")
        self.scheduler.validate(nodes)
        workflow = self.store.create_workflow(name, goal, context)
        stage = self.store.create_stage(workflow.id, "main", 0)
        self.store.create_nodes(workflow.id, nodes, stage_id=stage.id)
        return self.store.get_workflow(workflow.id)

    def start(self, workflow_id: str) -> Workflow:
        workflow = self.store.get_workflow(workflow_id)
        if is_workflow_terminal(workflow.status):
            raise ValidationError(f"workflow {workflow_id} is already {workflow.status.value}")
        if not self.store.list_nodes(workflow_id):
            raise ValidationError("workflow has no nodes")
        if workflow.status is WorkflowStatus.CREATED:
            self.store.update_workflow_status(
                workflow_id, WorkflowStatus.INITIALIZING, "start requested"
            )
        workflow = self.store.get_workflow(workflow_id)
        if workflow.status is WorkflowStatus.INITIALIZING:
            self.store.update_workflow_status(
                workflow_id, WorkflowStatus.RUNNING, "initialized"
            )
        self.checkpoints.save(workflow_id, "started")
        return self.run(workflow_id)

    def resume(self, workflow_id: str) -> Workflow:
        workflow = self.store.get_workflow(workflow_id)
        if is_workflow_terminal(workflow.status):
            return workflow
        if workflow.status is WorkflowStatus.CREATED:
            return self.start(workflow_id)
        self.recovery.recover_workflow(workflow_id)
        return self.run(workflow_id)

    def recover_all(self):
        return self.recovery.scan()

    def cancel(self, workflow_id: str, reason: str = "cancelled by user") -> Workflow:
        workflow = self.store.get_workflow(workflow_id)
        if is_workflow_terminal(workflow.status):
            return workflow
        self._close_pending_decisions(workflow_id, reason)
        self._cancel_open_nodes(workflow_id, reason)
        workflow = self.store.get_workflow(workflow_id)
        if workflow.status is not WorkflowStatus.CANCELLED:
            self.store.update_workflow_status(
                workflow_id, WorkflowStatus.CANCELLED, reason, event_type="workflow.cancelled"
            )
        self.checkpoints.save(workflow_id, "cancelled")
        return self.store.get_workflow(workflow_id)

    # ----------------------------------------------------------------- run
    def run(self, workflow_id: str, max_iterations: int | None = None) -> Workflow:
        if max_iterations is None:
            # One wave per DAG layer; the bound scales with the DAG instead of
            # failing long but perfectly healthy chains.
            max_iterations = max(100, len(self.store.list_nodes(workflow_id)) * 2 + 10)
        for _ in range(max_iterations):
            workflow = self.store.get_workflow(workflow_id)
            if is_workflow_terminal(workflow.status):
                return workflow

            self.decisions.expire_overdue(workflow_id)
            if self.store.list_nodes(workflow_id, NodeStatus.RUNNING):
                outcome = self.recovery.recover_workflow(workflow_id)
                workflow = self.store.get_workflow(workflow_id)
                if is_workflow_terminal(workflow.status):
                    return workflow
                if outcome.action in ("waiting_human", "active"):
                    return workflow

            ready = self.scheduler.refresh(workflow_id)
            workflow = self.store.get_workflow(workflow_id)
            if ready:
                executable, wait = self._partition_backoff(workflow_id, ready)
                if not executable and wait > 0:
                    self._ensure_running(workflow_id, workflow)
                    logger.info("workflow %s waiting %.1fs for retry backoff", workflow_id, wait)
                    time.sleep(min(wait, _MAX_BACKOFF_WAIT))
                    continue
                self._ensure_running(workflow_id, workflow)
                self._execute_batch(workflow_id, executable)
                self.checkpoints.save(workflow_id, "batch_completed")
                continue

            pending = self.decisions.pending(workflow_id)
            nodes = self.store.list_nodes(workflow_id)
            report = self.completion.evaluate(workflow, nodes, len(pending))
            if report.passed:
                self._finish(workflow_id, WorkflowStatus.COMPLETED, report.summary)
                self.checkpoints.save(workflow_id, "completed")
                return self.store.get_workflow(workflow_id)

            if self.scheduler.has_failed_required(workflow_id):
                self._finish(workflow_id, WorkflowStatus.FAILED, "a required node failed")
                self.checkpoints.save(workflow_id, "failed")
                return self.store.get_workflow(workflow_id)

            if pending:
                if workflow.status is not WorkflowStatus.WAITING_HUMAN:
                    self._ensure_running(workflow_id, workflow)
                    self.store.update_workflow_status(
                        workflow_id,
                        WorkflowStatus.WAITING_HUMAN,
                        "awaiting human decision",
                        event_type="workflow.waiting_human",
                    )
                self.checkpoints.save(workflow_id, "waiting_human")
                return self.store.get_workflow(workflow_id)

            self._finish(
                workflow_id,
                WorkflowStatus.FAILED,
                "no schedulable work (blocked or inconsistent node state)",
            )
            self.checkpoints.save(workflow_id, "failed")
            return self.store.get_workflow(workflow_id)

        self.store.append_event(
            workflow_id,
            "workflow.iteration_limit",
            {"max_iterations": max_iterations},
        )
        self.checkpoints.save(workflow_id, "iteration_limit")
        raise DevFlowError(f"workflow {workflow_id} exceeded {max_iterations} iterations")

    def _ensure_running(self, workflow_id: str, workflow: Workflow) -> None:
        if workflow.status is WorkflowStatus.RUNNING:
            return
        if workflow.status is WorkflowStatus.CREATED:
            self.store.update_workflow_status(
                workflow_id, WorkflowStatus.INITIALIZING, "auto-initialize"
            )
            workflow = self.store.get_workflow(workflow_id)
        if workflow.status in (
            WorkflowStatus.INITIALIZING,
            WorkflowStatus.WAITING,
            WorkflowStatus.WAITING_HUMAN,
            WorkflowStatus.RECOVERING,
        ):
            self.store.update_workflow_status(
                workflow_id, WorkflowStatus.RUNNING, "scheduling work"
            )

    def _finish(self, workflow_id: str, target: WorkflowStatus, reason: str) -> None:
        workflow = self.store.get_workflow(workflow_id)
        if workflow.status is target:
            return
        if target in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED):
            # A terminal workflow must not keep pending decisions or nodes that
            # can still be mutated or executed.
            self._close_pending_decisions(workflow_id, reason)
            self._cancel_open_nodes(workflow_id, reason)
            workflow = self.store.get_workflow(workflow_id)
        if workflow.status is WorkflowStatus.CREATED:
            self._ensure_running(workflow_id, workflow)
            workflow = self.store.get_workflow(workflow_id)
        if workflow.status is WorkflowStatus.WAITING_HUMAN and target is WorkflowStatus.COMPLETED:
            self._ensure_running(workflow_id, workflow)
            workflow = self.store.get_workflow(workflow_id)
        if workflow.status is not target:
            self.store.update_workflow_status(
                workflow_id,
                target,
                reason,
                error=reason if target is WorkflowStatus.FAILED else None,
                event_type=f"workflow.{target.value.lower()}",
            )

    def _close_pending_decisions(self, workflow_id: str, reason: str) -> None:
        for decision in self.store.list_decisions(workflow_id, DecisionStatus.PENDING):
            try:
                self.store.finalize_decision(
                    decision.id,
                    DecisionStatus.CANCELLED,
                    resolved_by="core",
                    note=reason,
                )
            except InvalidTransition:
                continue  # another writer resolved/cancelled it first

    def _cancel_open_nodes(self, workflow_id: str, reason: str) -> None:
        for node in self.store.list_nodes(workflow_id):
            if node.status in (
                NodeStatus.PENDING,
                NodeStatus.READY,
                NodeStatus.RUNNING,
                NodeStatus.WAITING_HUMAN,
                NodeStatus.BLOCKED,
            ):
                try:
                    self.store.update_node_status(
                        node.id, NodeStatus.CANCELLED, reason, event_type="node.cancelled"
                    )
                except InvalidTransition:
                    continue

    # ------------------------------------------------------------ execution
    def _execute_batch(self, workflow_id: str, ready: list[Node]) -> None:
        claimed: list[Node] = []
        for node in ready:
            node = self.store.claim_node(node.id)
            if node is not None:
                claimed.append(node)
        if not claimed:
            return
        workers = max(1, min(self.config.max_parallel, len(claimed)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self._execute_node, workflow_id, node.id): node.id for node in claimed
            }
            for future in as_completed(futures):
                node_id = futures[future]
                try:
                    future.result()
                except Exception:
                    # Leave recovery in charge; it distinguishes interruption
                    # from failure and never blindly re-runs SUCCEEDED nodes.
                    logger.exception("node %s raised during execution", node_id)

    def _execute_node(self, workflow_id: str, node_id: str) -> None:
        node = self.store.get_node(node_id)
        workflow = self.store.get_workflow(workflow_id)

        gate = node.execution_config.get("human_decision")
        if isinstance(gate, dict):
            latest = self.store.latest_decision_for_node(node_id)
            if latest is None:
                try:
                    self.decisions.request(workflow_id, node_id, gate)
                except ValidationError as exc:
                    self.store.update_node_status(
                        node_id,
                        NodeStatus.FAILED,
                        "invalid human decision specification",
                        error=str(exc),
                        event_type="node.failed",
                    )
                return
            if latest.status is DecisionStatus.PENDING:
                if node.status is not NodeStatus.WAITING_HUMAN:
                    self.store.update_node_status(
                        node_id, NodeStatus.WAITING_HUMAN, "waiting for human decision"
                    )
                return
            if latest.status is not DecisionStatus.RESOLVED:
                self.store.update_node_status(
                    node_id,
                    NodeStatus.FAILED,
                    "decision not approved",
                    error=f"human decision {latest.status.value.lower()}",
                    event_type="node.failed",
                )
                return

        attempt = self.store.increment_attempts(node_id)
        context = self.checkpoints.build_context(workflow_id)
        context["workdir"] = str(self.config.workdir)
        decision = self.store.latest_decision_for_node(node_id)
        if decision is not None and decision.status is DecisionStatus.RESOLVED:
            context["approved_decision"] = {
                "decision_id": decision.id,
                "selected_option": decision.selected_option,
                "payload": decision.decision_payload,
                "resolved_by": decision.resolved_by,
            }

        prompt = self._build_prompt(workflow, node, context)
        raw_timeout = node.execution_config.get("timeout_seconds")
        timeout = float(raw_timeout) if raw_timeout is not None else self.config.agent_timeout
        run = self.agents.execute(
            workflow_id,
            node,
            prompt,
            attempt,
            workdir=str(self.config.workdir),
            timeout_seconds=timeout,
            context=context,
        )

        if run.status is AgentRunStatus.INTERRUPTED:
            self.store.append_event(
                workflow_id,
                "node.interrupted",
                {"agent_run_id": run.id, "error": run.error},
                node_id=node_id,
            )
            return  # node stays RUNNING; recovery owns the next decision

        output: dict[str, Any] = {
            "agent_run_id": run.id,
            "attempt": attempt,
            "exit_code": run.exit_code,
            "output": run.output,
        }
        if node.execution_config.get("artifacts"):
            output["artifacts"] = list(node.execution_config["artifacts"])

        if run.status is AgentRunStatus.SUCCEEDED:
            result = AgentResult(
                success=True,
                output=run.output,
                exit_code=run.exit_code,
                error=run.error,
            )
            report = self.verifier.verify(workflow, node, result, context)
            output["verification"] = report.as_dict()
            output["summary"] = report.summary[:500]
            if report.passed:
                self.store.update_node_status(
                    node_id,
                    NodeStatus.SUCCEEDED,
                    "verification passed",
                    output=output,
                    event_type="node.succeeded",
                )
                return
            self._handle_failure(
                workflow_id, node_id, run, f"verification failed: {report.summary}", output
            )
            return

        if run.status is AgentRunStatus.TIMEOUT:
            reason = f"agent timeout: {run.error or ''}".strip()
        else:
            reason = f"agent failed: {run.error or run.status.value}".strip()
        output["summary"] = reason[:500]
        self._handle_failure(workflow_id, node_id, run, reason, output)

    def _handle_failure(self, workflow_id: str, node_id: str, run, reason: str, output: dict) -> None:
        node = self.store.get_node(node_id)
        try:
            policy = (
                RetryPolicy.from_dict(node.retry_policy)
                if node.retry_policy
                else self.recovery.default_retry_policy
            )
        except (TypeError, ValueError) as exc:
            self.store.update_node_status(
                node_id,
                NodeStatus.FAILED,
                "invalid retry policy",
                error=f"invalid retry_policy: {exc}",
                output=output,
                event_type="node.failed",
            )
            return
        if node.attempts <= policy.max_retries:
            delay = policy.delay_for(max(1, node.attempts))
            next_at = (
                datetime.now(timezone.utc) + timedelta(seconds=delay)
            ).isoformat(timespec="seconds")
            self.store.record_retry(
                workflow_id,
                node_id,
                node.attempts + 1,
                reason,
                agent_run_id=getattr(run, "id", None),
                next_retry_at=next_at,
            )
            self.store.update_node_status(
                node_id,
                NodeStatus.READY,
                f"retry scheduled after attempt {node.attempts}",
                error=reason,
                output=output,
                event_type="node.retry_scheduled",
            )
            return
        self.store.update_node_status(
            node_id,
            NodeStatus.FAILED,
            "retries exhausted",
            error=reason,
            output=output,
            event_type="node.failed",
        )

    def _partition_backoff(self, workflow_id: str, ready: list[Node]) -> tuple[list[Node], float]:
        latest: dict[str, RetryRecord] = {}
        for record in self.store.list_retries(workflow_id):
            latest[record.node_id] = record
        now = datetime.now(timezone.utc)
        executable: list[Node] = []
        waits: list[float] = []
        for node in ready:
            next_at = parse_time(latest[node.id].next_retry_at) if node.id in latest else None
            if next_at is not None and next_at > now:
                waits.append((next_at - now).total_seconds())
            else:
                executable.append(node)
        return executable, (min(waits) if waits else 0.0)

    def _build_prompt(self, workflow: Workflow, node: Node, context: dict[str, Any]) -> str:
        custom = node.execution_config.get("prompt")
        if custom:
            return str(custom)
        context_json = json.dumps(context, ensure_ascii=False, default=str, indent=2)
        if len(context_json) > _MAX_PROMPT_CONTEXT:
            context_json = context_json[:_MAX_PROMPT_CONTEXT] + "\n... (truncated)"
        node_input = json.dumps(node.input, ensure_ascii=False, default=str, indent=2)
        return (
            f"Workflow goal: {workflow.goal}\n"
            f"Node: {node.name} ({node.id})\n"
            f"Node input:\n{node_input}\n\n"
            "Continuation context (authoritative state is persisted in SQLite):\n"
            f"{context_json}\n\n"
            "Execute this node and report the result. Do not declare the workflow "
            "complete yourself; the engine verifies completion."
        )

    # ---------------------------------------------------------------- views
    def status(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.store.get_workflow(workflow_id)
        return {
            "workflow": asdict(workflow),
            "node_counts": self.scheduler.counts(workflow_id),
            "pending_decisions": [asdict(d) for d in self.decisions.pending(workflow_id)],
            "latest_checkpoint": (
                asdict(self.checkpoints.latest(workflow_id))
                if self.checkpoints.latest(workflow_id)
                else None
            ),
        }

    def inspect(self, workflow_id: str, event_limit: int = 100) -> dict[str, Any]:
        workflow = self.store.get_workflow(workflow_id)
        return {
            "workflow": asdict(workflow),
            "stages": [asdict(s) for s in self.store.list_stages(workflow_id)],
            "nodes": [asdict(n) for n in self.store.list_nodes(workflow_id)],
            "agent_runs": [asdict(r) for r in self.store.list_agent_runs(workflow_id)],
            "decisions": [asdict(d) for d in self.store.list_decisions(workflow_id)],
            "retries": [asdict(r) for r in self.store.list_retries(workflow_id)],
            "checkpoints": [asdict(c) for c in self.store.list_checkpoints(workflow_id)],
            "events": [asdict(e) for e in self.store.list_events(workflow_id, event_limit)],
        }

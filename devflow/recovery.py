"""Interruption detection and recovery. Interruption is not failure."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .agents.base import AgentResult
from .continuation import CheckpointManager
from .errors import InvalidTransition
from .models import (
    AgentRun,
    AgentRunStatus,
    DecisionStatus,
    Node,
    NodeStatus,
    WorkflowStatus,
    parse_time,
)
from .platform_proc import process_alive
from .scheduler import DAGScheduler
from .state import StateStore
from .verifier import Verifier

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 2
    base_delay_seconds: float = 1.0
    multiplier: float = 2.0
    max_delay_seconds: float = 60.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RetryPolicy":
        data = data or {}
        return cls(
            max_retries=int(data.get("max_retries", 2)),
            base_delay_seconds=float(data.get("base_delay_seconds", data.get("base_delay", 1.0))),
            multiplier=float(data.get("multiplier", 2.0)),
            max_delay_seconds=float(data.get("max_delay_seconds", data.get("max_delay", 60.0))),
        )

    def delay_for(self, retry_number: int) -> float:
        """Exponential backoff for the n-th retry (1-based)."""
        if retry_number <= 0:
            return 0.0
        return min(self.max_delay_seconds, self.base_delay_seconds * (self.multiplier ** (retry_number - 1)))


@dataclass
class RecoveryResult:
    workflow_id: str
    action: str
    details: dict[str, Any] = field(default_factory=dict)


class RecoveryManager:
    def __init__(
        self,
        store: StateStore,
        scheduler: DAGScheduler,
        verifier: Verifier,
        *,
        grace_seconds: float = 30.0,
        default_retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.store = store
        self.scheduler = scheduler
        self.verifier = verifier
        self.grace_seconds = grace_seconds
        self.default_retry_policy = default_retry_policy or RetryPolicy()
        self.checkpoints = CheckpointManager(store)

    def scan(self) -> list[RecoveryResult]:
        results: list[RecoveryResult] = []
        for workflow in self.store.list_active_workflows():
            try:
                results.append(self.recover_workflow(workflow.id))
            except Exception as exc:  # one broken workflow must not hide the others
                logger.exception("recovery failed for workflow %s", workflow.id)
                self.store.append_event(
                    workflow.id,
                    "recovery.error",
                    {"error": f"{type(exc).__name__}: {exc}"},
                    actor="recovery",
                )
                results.append(
                    RecoveryResult(workflow.id, "error", {"error": f"{type(exc).__name__}: {exc}"})
                )
        return results

    def recover_workflow(self, workflow_id: str) -> RecoveryResult:
        workflow = self.store.get_workflow(workflow_id)
        if workflow.status in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        ):
            return RecoveryResult(workflow_id, "skipped", {"reason": "terminal"})

        self.store.append_event(workflow_id, "recovery.scan", {}, actor="recovery")
        retried: list[str] = []
        failed: list[str] = []
        completed: list[str] = []
        active: list[str] = []

        for node in self.store.list_nodes(workflow_id, NodeStatus.RUNNING):
            runs = self.store.list_agent_runs(workflow_id=workflow_id, node_id=node.id)
            run = runs[-1] if runs else None

            if run is not None and run.status is AgentRunStatus.RUNNING:
                if run.pid and run.process_start_time:
                    if process_alive(run.pid, run.process_start_time):
                        active.append(node.id)
                        continue
                    reason = "agent process exited or pid was reused"
                elif not self._is_stale(run):
                    active.append(node.id)
                    continue
                else:
                    reason = "stale or unresponsive agent run"
                try:
                    run = self.store.update_agent_run(
                        run.id, AgentRunStatus.INTERRUPTED, error=reason
                    )
                    self.store.append_event(
                        workflow_id,
                        "node.interrupted",
                        {"agent_run_id": run.id, "reason": reason},
                        node_id=node.id,
                        actor="recovery",
                    )
                except InvalidTransition:
                    # The manager committed a terminal result between our read
                    # and this write; use the authoritative row.
                    run = self.store.get_agent_run(run.id)
                    if run.status is AgentRunStatus.RUNNING:
                        active.append(node.id)
                        continue

            if run is not None and run.status is AgentRunStatus.SUCCEEDED:
                # Crash after the agent finished but before verification persisted.
                report = self.verifier.verify(
                    workflow,
                    node,
                    AgentResult(
                        success=True,
                        output=run.output,
                        exit_code=run.exit_code,
                        error=run.error,
                    ),
                    {"workdir": run.workdir},
                )
                if report.passed:
                    self.store.update_node_status(
                        node.id,
                        NodeStatus.SUCCEEDED,
                        "recovered after agent success",
                        output={
                            "agent_run_id": run.id,
                            "exit_code": run.exit_code,
                            "output": run.output,
                            "summary": (report.summary or "")[:500],
                            "verification": report.as_dict(),
                        },
                        event_type="node.succeeded",
                        actor="recovery",
                    )
                    completed.append(node.id)
                    continue

            outcome = self._retry_or_fail(node, run, workflow_id)
            if outcome == "retry":
                retried.append(node.id)
            elif outcome == "fail":
                failed.append(node.id)

        self._reconcile_waiting_nodes(workflow_id)

        if active:
            return RecoveryResult(
                workflow_id,
                "active",
                {"active_nodes": active, "retried": retried, "failed": failed},
            )

        pending = self.store.list_decisions(workflow_id, DecisionStatus.PENDING)
        workflow = self.store.get_workflow(workflow_id)
        if pending:
            if workflow.status in (
                WorkflowStatus.RUNNING,
                WorkflowStatus.WAITING,
                WorkflowStatus.RECOVERING,
            ):
                workflow = self.store.update_workflow_status(
                    workflow_id,
                    WorkflowStatus.WAITING_HUMAN,
                    "pending decisions during recovery",
                    event_type="workflow.waiting_human",
                    actor="recovery",
                )
            self.checkpoints.save(workflow_id, "recovery_waiting_human")
            return RecoveryResult(
                workflow_id,
                "waiting_human",
                {"pending_decisions": [d.id for d in pending], "retried": retried, "failed": failed},
            )

        if workflow.status is WorkflowStatus.WAITING_HUMAN:
            workflow = self.store.update_workflow_status(
                workflow_id,
                WorkflowStatus.RUNNING,
                "no pending decisions after recovery",
                event_type="workflow.resumed",
                actor="recovery",
            )
        elif workflow.status is WorkflowStatus.INITIALIZING:
            workflow = self.store.update_workflow_status(
                workflow_id,
                WorkflowStatus.RUNNING,
                "recovered initialization",
                event_type="workflow.recovered",
                actor="recovery",
            )
        elif workflow.status is WorkflowStatus.RECOVERING:
            workflow = self.store.update_workflow_status(
                workflow_id,
                WorkflowStatus.RUNNING,
                "recovery complete",
                event_type="workflow.recovered",
                actor="recovery",
            )
        elif workflow.status in (WorkflowStatus.RUNNING, WorkflowStatus.WAITING):
            try:
                workflow = self.store.update_workflow_status(
                    workflow_id,
                    WorkflowStatus.RECOVERING,
                    "recovery scan",
                    event_type="workflow.recovery_started",
                    actor="recovery",
                )
                workflow = self.store.update_workflow_status(
                    workflow_id,
                    WorkflowStatus.RUNNING,
                    "recovery complete",
                    event_type="workflow.recovered",
                    actor="recovery",
                )
            except InvalidTransition:
                workflow = self.store.get_workflow(workflow_id)
                if workflow.status in (
                    WorkflowStatus.COMPLETED,
                    WorkflowStatus.FAILED,
                    WorkflowStatus.CANCELLED,
                ):
                    return RecoveryResult(
                        workflow_id, "skipped", {"reason": "workflow became terminal"}
                    )

        checkpoint = self.checkpoints.save(workflow_id, "recovery")
        return RecoveryResult(
            workflow_id,
            "resumed",
            {
                "retried": retried,
                "failed": failed,
                "completed": completed,
                "checkpoint": checkpoint.id,
                "workflow_status": workflow.status.value,
            },
        )

    def _retry_or_fail(
        self, node: Node, run: AgentRun | None, workflow_id: str
    ) -> str:
        node = self.store.get_node(node.id)
        if node.status is not NodeStatus.RUNNING:
            return "skipped"
        try:
            policy = (
                RetryPolicy.from_dict(node.retry_policy)
                if node.retry_policy
                else self.default_retry_policy
            )
        except (TypeError, ValueError) as exc:
            self.store.update_node_status(
                node.id,
                NodeStatus.FAILED,
                "invalid retry policy",
                error=f"invalid retry_policy: {exc}",
                event_type="node.failed",
                actor="recovery",
            )
            return "fail"
        attempts = node.attempts or (run.attempt if run else 0)
        reason = f"agent run {run.status.value.lower()}" if run else "node was running without an agent run"
        if attempts <= policy.max_retries:
            delay = policy.delay_for(max(1, attempts))
            next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
            self.store.record_retry(
                workflow_id,
                node.id,
                attempts + 1,
                reason,
                agent_run_id=run.id if run else None,
                next_retry_at=next_at,
            )
            self.store.update_node_status(
                node.id,
                NodeStatus.READY,
                f"recovery retry ({reason})",
                event_type="node.retry_scheduled",
                actor="recovery",
            )
            return "retry"
        error = f"retry exhausted after {attempts} attempt(s): {reason}"
        self.store.update_node_status(
            node.id,
            NodeStatus.FAILED,
            "recovery retries exhausted",
            error=error,
            event_type="node.failed",
            actor="recovery",
        )
        return "fail"

    def _reconcile_waiting_nodes(self, workflow_id: str) -> None:
        pending = self.store.list_decisions(workflow_id, DecisionStatus.PENDING)
        pending_nodes = {d.node_id for d in pending}
        for node in self.store.list_nodes(workflow_id, NodeStatus.WAITING_HUMAN):
            if node.id in pending_nodes:
                continue
            latest = self.store.latest_decision_for_node(node.id)
            if latest is None:
                continue
            try:
                if latest.status is DecisionStatus.RESOLVED:
                    self.store.update_node_status(
                        node.id,
                        NodeStatus.READY,
                        "recovered resolved decision",
                        event_type="node.decision_resolved",
                        actor="recovery",
                    )
                elif latest.status in (DecisionStatus.TIMEOUT, DecisionStatus.CANCELLED):
                    self.store.update_node_status(
                        node.id,
                        NodeStatus.FAILED,
                        f"decision {latest.status.value.lower()}",
                        error=f"human decision {latest.status.value.lower()}",
                        event_type="node.failed",
                        actor="recovery",
                    )
            except InvalidTransition:
                continue  # another writer already reconciled this node

    def find_orphans(self, workflow_id: str | None = None) -> list[AgentRun]:
        """RUNNING runs whose recorded process is gone or whose PID was reused."""
        runs = self.store.list_agent_runs(
            workflow_id=workflow_id, status=AgentRunStatus.RUNNING
        )
        return [
            run
            for run in runs
            if run.pid
            and run.process_start_time
            and not process_alive(run.pid, run.process_start_time)
        ]

    def _is_stale(self, run: AgentRun) -> bool:
        # With a timeout, the deadline is timeout + grace. Without one,
        # heartbeat freshness is the only liveness signal, so grace is the
        # window. A live unbounded run is kept fresh by its heartbeat thread.
        limit = (run.timeout_seconds or 0.0) + self.grace_seconds
        last = parse_time(run.heartbeat_at or run.started_at)
        if last is None:
            return True
        if limit <= 0:
            return True
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed > limit

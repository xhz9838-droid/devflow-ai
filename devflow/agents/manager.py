"""Agent Run lifecycle. Workflow, node and agent run stay separate."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..errors import AgentExecutionError, AgentInterrupted, AgentTimeout, InvalidTransition
from ..models import AgentRun, AgentRunStatus, Node
from ..platform_proc import terminate_process_tree
from ..state import StateStore
from .base import AgentAdapter, AgentRequest

logger = logging.getLogger(__name__)


class AgentExecutionManager:
    def __init__(self, store: StateStore, adapter: AgentAdapter) -> None:
        self.store = store
        self.adapter = adapter

    def execute(
        self,
        workflow_id: str,
        node: Node,
        prompt: str,
        attempt: int,
        *,
        workdir: str,
        timeout_seconds: float,
        context: dict[str, Any] | None = None,
    ) -> AgentRun:
        run = self.store.create_agent_run(
            workflow_id,
            node.id,
            self.adapter.name,
            attempt,
            prompt=prompt,
            workdir=workdir,
            timeout_seconds=timeout_seconds,
            metadata={"node_name": node.name},
        )
        request = AgentRequest(
            workflow_id=workflow_id,
            node_id=node.id,
            agent_run_id=run.id,
            prompt=prompt,
            workdir=workdir,
            timeout_seconds=timeout_seconds,
            context=context or {},
            metadata=node.execution_config.get("agent_metadata") or {},
            on_process_started=lambda pid, started: self.store.set_agent_run_process(
                run.id, pid, started
            ),
        )

        started = time.monotonic()
        stop_heartbeat = threading.Event()
        interval = (
            5.0
            if not timeout_seconds or timeout_seconds <= 0
            else max(0.1, min(5.0, timeout_seconds / 4.0))
        )

        def heartbeat() -> None:
            while not stop_heartbeat.wait(interval):
                try:
                    self.store.heartbeat_agent_run(run.id)
                except Exception:
                    # A transient lock must not disable liveness for the rest
                    # of a long run; keep trying until the run ends.
                    logger.exception("heartbeat failed for agent run %s", run.id)

        beat = threading.Thread(target=heartbeat, name=f"heartbeat-{run.id}", daemon=True)
        beat.start()
        self.store.heartbeat_agent_run(run.id)
        try:
            try:
                result = self.adapter.execute(request)
            except AgentTimeout as exc:
                logger.warning("agent run %s timed out: %s", run.id, exc)
                return self.store.update_agent_run(run.id, AgentRunStatus.TIMEOUT, error=str(exc))
            except AgentInterrupted as exc:
                logger.warning("agent run %s interrupted: %s", run.id, exc)
                return self.store.update_agent_run(run.id, AgentRunStatus.INTERRUPTED, error=str(exc))
            except AgentExecutionError as exc:
                logger.warning("agent run %s failed: %s", run.id, exc)
                return self.store.update_agent_run(run.id, AgentRunStatus.FAILED, error=str(exc))
            except Exception as exc:  # adapter crash must not kill the engine
                logger.exception("adapter %s crashed on run %s", self.adapter.name, run.id)
                return self.store.update_agent_run(
                    run.id, AgentRunStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
                )

            elapsed = time.monotonic() - started
            if timeout_seconds and elapsed > timeout_seconds:
                return self.store.update_agent_run(
                    run.id,
                    AgentRunStatus.TIMEOUT,
                    output=result.output,
                    exit_code=result.exit_code,
                    error=f"elapsed {elapsed:.1f}s exceeded timeout {timeout_seconds:.1f}s",
                    metadata=result.metadata,
                )

            status = AgentRunStatus.SUCCEEDED if result.success else AgentRunStatus.FAILED
            try:
                return self.store.update_agent_run(
                    run.id,
                    status,
                    output=result.output,
                    exit_code=result.exit_code,
                    error=result.error,
                    metadata=result.metadata,
                )
            except InvalidTransition:
                # Another recovery pass declared this run interrupted while it
                # was still finishing. Keep the audit trail instead of losing it.
                self.store.append_event(
                    workflow_id,
                    "agent.late_result",
                    {
                        "run_id": run.id,
                        "status": status.value,
                        "exit_code": result.exit_code,
                        "error": result.error,
                    },
                    node_id=node.id,
                )
                return self.store.get_agent_run(run.id)
        finally:
            stop_heartbeat.set()
            beat.join(timeout=2.0)

    def terminate(self, workflow_id: str, *, force: bool = True) -> list[str]:
        """Terminate live agent processes for a workflow. Identity-checked, so
        a reused PID is never killed."""
        terminated: list[str] = []
        for run in self.store.list_agent_runs(
            workflow_id=workflow_id, status=AgentRunStatus.RUNNING
        ):
            if not run.pid:
                continue
            if not run.process_start_time:
                self.store.append_event(
                    workflow_id,
                    "agent.terminate_skipped",
                    {"run_id": run.id, "pid": run.pid, "reason": "no process identity"},
                    node_id=run.node_id,
                )
                continue
            accepted = terminate_process_tree(
                run.pid, run.process_start_time, force=force
            )
            self.store.append_event(
                workflow_id,
                "agent.terminate_requested",
                {"run_id": run.id, "pid": run.pid, "force": force, "accepted": accepted},
                node_id=run.node_id,
            )
            if not accepted:
                continue
            try:
                self.store.update_agent_run(
                    run.id, AgentRunStatus.CANCELLED, error="terminated by engine"
                )
            except InvalidTransition:
                pass
            terminated.append(run.id)
        return terminated

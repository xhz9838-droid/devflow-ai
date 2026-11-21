"""Workflow Continuation Protocol: checkpoints and handoff context."""

from __future__ import annotations

import json
from typing import Any

from .errors import CheckpointError
from .models import Checkpoint, NodeStatus
from .state import StateStore


def _result_text(output: dict[str, Any] | None) -> str:
    """Extract the agent's meaningful result from a structured CLI payload."""
    if not output:
        return ""
    raw = output.get("output") or ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("result"), str):
            return parsed["result"]
    except (TypeError, ValueError):
        pass
    return str(raw)


def build_continuation_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    workflow = snapshot["workflow"]
    nodes: list[dict[str, Any]] = snapshot["nodes"]
    decisions: list[dict[str, Any]] = snapshot["decisions"]
    events: list[dict[str, Any]] = snapshot.get("recent_events", [])
    stages: list[dict[str, Any]] = snapshot.get("stages", [])

    completed = [n for n in nodes if n["status"] == NodeStatus.SUCCEEDED.value]
    running = [n for n in nodes if n["status"] == NodeStatus.RUNNING.value]
    waiting_human = [n for n in nodes if n["status"] == NodeStatus.WAITING_HUMAN.value]
    ready = [n for n in nodes if n["status"] == NodeStatus.READY.value]
    pending = [n for n in nodes if n["status"] == NodeStatus.PENDING.value]
    blocked = [n for n in nodes if n["status"] == NodeStatus.BLOCKED.value]
    failed = [n for n in nodes if n["status"] == NodeStatus.FAILED.value]
    open_decisions = [d for d in decisions if d["status"] == "PENDING"]

    current = next(iter(running + waiting_human), None)
    stage_by_id = {s["id"]: s["name"] for s in stages}

    side_effects = [
        {"node_id": n["id"], "idempotency_key": n["idempotency_key"]}
        for n in completed
        if n.get("idempotency_key")
    ]
    deliverables: list[Any] = []
    for node in completed:
        output = node.get("output") or {}
        deliverables.extend(output.get("artifacts") or [])

    next_actions: list[str] = []
    if open_decisions:
        next_actions.extend(f"resolve decision {d['id']} for node {d.get('node_id')}" for d in open_decisions)
    if running:
        next_actions.extend(f"wait for or recover node {n['id']}" for n in running)
    next_actions.extend(f"execute ready node {n['id']}" for n in ready)
    if failed:
        next_actions.extend(f"retry or fail node {n['id']}" for n in failed)
    if not next_actions:
        next_actions.append(
            "workflow terminal" if workflow["status"] in {"COMPLETED", "FAILED", "CANCELLED"} else "request recovery"
        )

    unresolved: list[str] = []
    for node in failed:
        unresolved.append(f"{node['id']}: {node.get('error') or 'failed'}")
    for decision in open_decisions:
        unresolved.append(f"decision {decision['id']}: {decision['question']}")

    return {
        "workflow_id": workflow["id"],
        "goal": workflow["goal"],
        "workflow_status": workflow["status"],
        "current_stage": stage_by_id.get(current.get("stage_id")) if current else None,
        "current_node": current["id"] if current else None,
        "progress": {
            "total": len(nodes),
            "completed": len(completed),
            "running": len(running),
            "waiting_human": len(waiting_human),
            "ready": len(ready),
            "pending": len(pending),
            "blocked": len(blocked),
            "failed": len(failed),
        },
        "completed_nodes": [
            {
                "id": n["id"],
                "name": n["name"],
                "summary": (n.get("output") or {}).get("summary"),
                "verification_passed": (
                    ((n.get("output") or {}).get("verification") or {}).get("passed")
                ),
                "artifacts": (n.get("output") or {}).get("artifacts") or [],
                "output": _result_text(n.get("output"))[:4000],
                "idempotency_key": n.get("idempotency_key"),
            }
            for n in completed
        ],
        "running_nodes": [{"id": n["id"], "name": n["name"]} for n in running],
        "ready_nodes": [{"id": n["id"], "name": n["name"]} for n in ready],
        "pending_nodes": [{"id": n["id"], "name": n["name"]} for n in pending],
        "blocked_nodes": [{"id": n["id"], "name": n["name"]} for n in blocked],
        "open_decisions": [
            {
                "id": d["id"],
                "node_id": d.get("node_id"),
                "question": d["question"],
                "options": d["options"],
                "expires_at": d.get("expires_at"),
            }
            for d in open_decisions
        ],
        "deliverables": deliverables,
        "side_effects_completed": side_effects,
        "unresolved_issues": unresolved,
        "next_actions": next_actions,
        "workflow_context": workflow.get("context") or {},
        "last_event": events[-1] if events else None,
        "checkpoint_source": "sqlite",
    }


class CheckpointManager:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def save(self, workflow_id: str, reason: str, node_id: str | None = None) -> Checkpoint:
        """Snapshot and checkpoint row are written in one transaction. A failure
        raises; the caller must not treat an unsaved checkpoint as saved."""
        try:
            with self.store.transaction() as conn:
                snapshot = self.store.build_state_snapshot(conn, workflow_id)
                context = build_continuation_context(snapshot)
                return self.store.insert_checkpoint(
                    conn, workflow_id, node_id, reason, snapshot, context
                )
        except Exception as exc:
            if isinstance(exc, CheckpointError):
                raise
            raise CheckpointError(f"failed to save checkpoint for {workflow_id}: {exc}") from exc

    def build_context(self, workflow_id: str) -> dict[str, Any]:
        with self.store.db.read() as conn:
            snapshot = self.store.build_state_snapshot(conn, workflow_id)
        return build_continuation_context(snapshot)

    def latest(self, workflow_id: str) -> Checkpoint | None:
        return self.store.get_latest_checkpoint(workflow_id)

    def handoff_context(self, workflow_id: str) -> dict[str, Any]:
        """Latest persisted checkpoint if any, otherwise current DB state.
        The DB is always the authority; the checkpoint is a handoff artifact."""
        latest = self.latest(workflow_id)
        if latest is not None:
            return latest.context
        return self.build_context(workflow_id)

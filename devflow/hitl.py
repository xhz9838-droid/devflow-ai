"""Human-in-the-loop decisions. Persisted, validated, auditable."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .errors import DecisionError, InvalidTransition, ValidationError
from .lifecycle import is_workflow_terminal
from .models import DecisionStatus, HumanDecision, NodeStatus, WorkflowStatus, parse_time
from .state import StateStore


def _normalize_options(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValidationError("decision options must be a list")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for option in raw:
        if isinstance(option, str):
            option_id, label = option, option
        elif isinstance(option, dict) and option.get("id") is not None:
            option_id = str(option["id"])
            label = str(option.get("label", option_id))
        else:
            raise ValidationError(f"invalid decision option: {option!r}")
        if option_id in seen:
            raise ValidationError(f"duplicate decision option id: {option_id}")
        seen.add(option_id)
        result.append({"id": option_id, "label": label})
    return result


class HumanDecisionManager:
    def __init__(self, store: StateStore) -> None:
        self.store = store

    def request(
        self,
        workflow_id: str,
        node_id: str | None,
        spec: dict[str, Any],
    ) -> HumanDecision:
        question = str(spec.get("question") or spec.get("title") or "").strip()
        if not question:
            raise ValidationError("human_decision.question is required")
        options = _normalize_options(spec.get("options"))
        recommended = spec.get("recommended")
        if recommended is not None and options:
            if str(recommended) not in {option["id"] for option in options}:
                raise ValidationError("recommended must match one of the option ids")
        context = spec.get("context") or {}
        if isinstance(context, str):
            context = {"summary": context}
        elif not isinstance(context, dict):
            context = {"value": context}
        context = dict(context)
        for key in ("reason", "recommended", "risk"):
            if spec.get(key) is not None:
                context[key] = spec.get(key)
        if node_id:
            existing = self.store.find_pending_decision(node_id)
            if existing is not None:
                node = self.store.get_node(node_id)
                if node.status is NodeStatus.RUNNING:
                    self.store.update_node_status(
                        node_id,
                        NodeStatus.WAITING_HUMAN,
                        "waiting for existing human decision",
                        event_type="node.waiting_human",
                    )
                return existing
        expires_at = None
        timeout_seconds = spec.get("timeout_seconds")
        if timeout_seconds is not None:
            expires = datetime.now(timezone.utc) + timedelta(seconds=float(timeout_seconds))
            expires_at = expires.isoformat(timespec="seconds")

        decision = self.store.create_decision(
            workflow_id,
            node_id,
            str(spec.get("decision_type") or "approval"),
            question,
            context=context,
            options=options,
            expires_at=expires_at,
        )
        if node_id:
            node = self.store.get_node(node_id)
            if node.status is not NodeStatus.WAITING_HUMAN:
                try:
                    self.store.update_node_status(
                        node_id,
                        NodeStatus.WAITING_HUMAN,
                        "waiting for human decision",
                        event_type="node.waiting_human",
                    )
                except Exception:
                    self.store.finalize_decision(
                        decision.id,
                        DecisionStatus.CANCELLED,
                        note="node could not enter WAITING_HUMAN",
                    )
                    raise
        return decision

    def resolve(
        self,
        decision_id: str,
        selected_option: str | None = None,
        payload: dict[str, Any] | None = None,
        *,
        resolved_by: str = "cli",
        note: str | None = None,
    ) -> HumanDecision:
        decision = self.store.get_decision(decision_id)
        if decision.status is not DecisionStatus.PENDING:
            raise DecisionError(f"decision {decision_id} is {decision.status.value}, not PENDING")
        workflow = self.store.get_workflow(decision.workflow_id)
        if is_workflow_terminal(workflow.status):
            raise DecisionError(f"workflow {workflow.id} is {workflow.status.value}; decision is closed")
        option_ids = [str(o["id"]) for o in decision.options]
        if option_ids:
            if selected_option is None:
                raise DecisionError("selected_option is required for this decision")
            if selected_option not in option_ids:
                raise DecisionError(
                    f"invalid option {selected_option!r}; expected one of {option_ids}"
                )
        elif selected_option is not None:
            raise DecisionError("this decision does not accept options; pass payload instead")

        try:
            resolved = self.store.finalize_decision(
                decision_id,
                DecisionStatus.RESOLVED,
                selected_option=selected_option,
                payload=payload or {},
                resolved_by=resolved_by,
                note=note,
            )
        except InvalidTransition as exc:
            raise DecisionError(f"decision {decision_id} is no longer PENDING") from exc
        self._resume_node(decision.node_id)
        self._resume_workflow(decision.workflow_id)
        return resolved

    def cancel(
        self, decision_id: str, *, resolved_by: str = "cli", note: str | None = None
    ) -> HumanDecision:
        decision = self.store.get_decision(decision_id)
        if decision.status is not DecisionStatus.PENDING:
            raise DecisionError(f"decision {decision_id} is {decision.status.value}, not PENDING")
        workflow = self.store.get_workflow(decision.workflow_id)
        if is_workflow_terminal(workflow.status):
            raise DecisionError(f"workflow {workflow.id} is {workflow.status.value}; decision is closed")
        try:
            cancelled = self.store.finalize_decision(
                decision_id,
                DecisionStatus.CANCELLED,
                resolved_by=resolved_by,
                note=note,
            )
        except InvalidTransition as exc:
            raise DecisionError(f"decision {decision_id} is no longer PENDING") from exc
        if decision.node_id:
            node = self.store.get_node(decision.node_id)
            if node.status is NodeStatus.WAITING_HUMAN:
                self.store.update_node_status(
                    decision.node_id,
                    NodeStatus.FAILED,
                    "decision cancelled",
                    error="human decision cancelled",
                    event_type="node.failed",
                )
        return cancelled

    def expire_overdue(self, workflow_id: str) -> list[HumanDecision]:
        expired: list[HumanDecision] = []
        now = datetime.now(timezone.utc)
        for decision in self.store.list_decisions(workflow_id, DecisionStatus.PENDING):
            current = self.store.get_decision(decision.id)
            if current.status is not DecisionStatus.PENDING:
                continue  # resolved by another writer between the query and now
            workflow = self.store.get_workflow(workflow_id)
            if is_workflow_terminal(workflow.status):
                continue
            expires_at = parse_time(current.expires_at)
            if expires_at is None or expires_at > now:
                continue
            try:
                self.store.finalize_decision(
                    current.id,
                    DecisionStatus.TIMEOUT,
                    note="decision timed out",
                )
            except InvalidTransition:
                continue
            if current.node_id:
                node = self.store.get_node(current.node_id)
                if node.status is NodeStatus.WAITING_HUMAN:
                    self.store.update_node_status(
                        current.node_id,
                        NodeStatus.FAILED,
                        "decision timeout",
                        error="human decision timed out",
                        event_type="node.failed",
                    )
            expired.append(current)
        return expired

    def pending(self, workflow_id: str) -> list[HumanDecision]:
        return self.store.list_decisions(workflow_id, DecisionStatus.PENDING)

    def list(self, workflow_id: str) -> list[HumanDecision]:
        return self.store.list_decisions(workflow_id)

    def get(self, decision_id: str) -> HumanDecision:
        return self.store.get_decision(decision_id)

    def _resume_node(self, node_id: str | None) -> None:
        if not node_id:
            return
        node = self.store.get_node(node_id)
        if node.status is NodeStatus.WAITING_HUMAN:
            try:
                self.store.update_node_status(
                    node_id,
                    NodeStatus.READY,
                    "decision resolved",
                    event_type="node.decision_resolved",
                )
            except InvalidTransition:
                return

    def _resume_workflow(self, workflow_id: str) -> None:
        workflow = self.store.get_workflow(workflow_id)
        if workflow.status is not WorkflowStatus.WAITING_HUMAN:
            return
        if self.store.list_decisions(workflow_id, DecisionStatus.PENDING):
            return
        try:
            self.store.update_workflow_status(
                workflow_id,
                WorkflowStatus.RUNNING,
                "all pending decisions resolved",
                event_type="workflow.resumed",
            )
        except InvalidTransition:
            return

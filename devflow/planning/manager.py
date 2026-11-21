"""Planning Layer orchestration: policy, clarification, validation, snapshots."""

from __future__ import annotations

from typing import Any

from ..errors import ValidationError
from .models import ClarificationStatus, Plan, PlanSnapshot, PlanStatus
from .planner import Planner, PlannerError, PlanningRequest, RepositoryContext
from .policy import DecisionPolicy, normalize_question
from .store import PlanStore
from .validator import (
    PlanValidationError,
    validate_plan,
)


def _question_key(question: str) -> str:
    return normalize_question(question)


class PlanManager:
    def __init__(
        self,
        store: PlanStore,
        planner: Planner,
        *,
        workdir: str,
        policy: DecisionPolicy | None = None,
        planner_timeout: float = 900.0,
    ) -> None:
        self.store = store
        self.planner = planner
        self.workdir = workdir
        self.policy = policy or DecisionPolicy()
        self.planner_timeout = planner_timeout

    # ---------------------------------------------------------------- create
    def create(
        self,
        objective: str,
        *,
        title: str | None = None,
        project_context: dict[str, Any] | None = None,
        authorizations: list[str] | None = None,
    ) -> Plan:
        context = dict(project_context or {})
        if authorizations:
            context["authorizations"] = authorizations
        return self.store.create_plan(title or objective[:80], objective, context)

    # ------------------------------------------------------------- generate
    def generate(self, plan_id: str, *, refine: bool = False) -> Plan:
        plan = self.store.get_plan(plan_id)
        if plan.status in (PlanStatus.CONFIRMED, PlanStatus.EXECUTION_READY, PlanStatus.CANCELLED):
            raise ValidationError(f"plan {plan_id} is {plan.status.value}; create a change request")
        if plan.status in (PlanStatus.DRAFT, PlanStatus.FAILED):
            self.store.set_plan_status(plan_id, PlanStatus.INVESTIGATING)
        self.store.set_plan_status(plan_id, PlanStatus.ANALYZING)

        repository = RepositoryContext.collect(self.workdir)
        project_context = {**repository, **plan.project_context}
        answered = self.store.list_clarifications(plan_id, ClarificationStatus.ANSWERED)
        resolved_answers = [
            {"question": item.question, "answer": item.answer, "recommended": item.recommended}
            for item in answered
        ]
        resolved_keys = {_question_key(item.question) for item in answered}
        authorizations = project_context.get("authorizations") or []
        if isinstance(authorizations, str):
            authorizations = [authorizations]

        previous_errors = None
        final: dict[str, Any] | None = None
        policy_result = None
        for _ in range(2):
            try:
                result = self.planner.plan(
                    PlanningRequest(
                        objective=plan.objective,
                        workdir=self.workdir,
                        project_context=project_context,
                        resolved_answers=resolved_answers,
                        previous_plan=plan.plan_json if refine else None,
                        previous_errors=previous_errors,
                        authorizations=list(authorizations),
                        timeout_seconds=self.planner_timeout,
                    )
                )
                candidate_plan = dict(result.plan)
                candidates = self._take_candidates(candidate_plan)
                policy_result = self.policy.evaluate(
                    candidates,
                    objective=plan.objective,
                    context=project_context,
                    authorizations=list(authorizations),
                    resolved_questions=resolved_keys,
                )
                final = self._normalize_plan(candidate_plan, plan, policy_result, resolved_answers)
                errors = validate_plan(final)
                if errors:
                    raise PlanValidationError(errors)
                break
            except Exception as exc:
                previous_errors = f"{type(exc).__name__}: {exc}"
                final = None
        if final is None or policy_result is None:
            self.store.set_plan_status(plan_id, PlanStatus.FAILED, error=previous_errors)
            raise PlannerError(previous_errors or "planning failed")

        self.store.save_plan_json(plan_id, final, project_context=project_context)

        existing = self.store.list_clarifications(plan_id)
        answered_keys = {
            _question_key(item.question)
            for item in existing
            if item.status is ClarificationStatus.ANSWERED
        }
        pending_by_key = {
            _question_key(item.question): item
            for item in existing
            if item.status is ClarificationStatus.PENDING
        }
        accepted_keys = {candidate.key for candidate in policy_result.clarifications}
        for item in pending_by_key.values():
            if _question_key(item.question) not in accepted_keys:
                self.store.cancel_clarification(item.id)
        new_items = [
            candidate.as_dict()
            for candidate in policy_result.clarifications
            if candidate.key not in answered_keys
            and candidate.key not in pending_by_key
        ]
        if new_items:
            self.store.create_clarifications(plan_id, new_items)
        pending = self.store.list_clarifications(plan_id, ClarificationStatus.PENDING)
        target = PlanStatus.NEEDS_CLARIFICATION if pending else PlanStatus.PLAN_READY
        return self.store.set_plan_status(plan_id, target)

    def _take_candidates(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        candidates = list(plan.pop("clarification_candidates", []) or [])
        clarification = plan.pop("clarification", None)
        if isinstance(clarification, dict):
            for item in clarification.get("required") or []:
                if isinstance(item, dict):
                    candidates.append(item)
        return candidates

    def _normalize_plan(
        self,
        plan: dict[str, Any],
        plan_row: Plan,
        policy_result,
        resolved_answers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        plan["title"] = str(plan.get("title") or plan_row.title)
        plan["objective"] = str(plan.get("objective") or plan_row.objective)
        plan.setdefault("project_context", {})
        scope = plan.get("scope")
        if not isinstance(scope, dict):
            scope = {}
        if not isinstance(scope.get("included"), list) or not scope["included"]:
            scope["included"] = [plan["objective"]]
        scope.setdefault("excluded", [])
        plan["scope"] = scope
        for key in ("assumptions", "decisions", "risks", "delivery_requirements"):
            if not isinstance(plan.get(key), list):
                plan[key] = []
        decisions: list[dict[str, Any]] = []
        seen: set[tuple[Any, Any]] = set()
        for decision in list(plan.get("decisions") or []) + list(policy_result.decisions):
            if not isinstance(decision, dict):
                continue
            key = (decision.get("subject"), decision.get("choice"))
            if key in seen:
                continue
            seen.add(key)
            decisions.append(decision)
        plan["decisions"] = decisions
        plan["clarification"] = {
            "required": [candidate.as_dict() for candidate in policy_result.clarifications],
            "resolved": resolved_answers,
        }

        nodes = plan.get("nodes")
        if not isinstance(nodes, list):
            nodes = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node.setdefault("type", "agent")
            agent = node.get("agent")
            if not isinstance(agent, dict):
                agent = {}
            agent.setdefault("adapter", "claude-code")
            agent.setdefault("role", "implementation agent")
            node["agent"] = agent
            node.setdefault("dependencies", [])
            node.setdefault("inputs", {})
            node.setdefault("expected_outputs", [])
            node.setdefault("retry_policy", {})
            node.setdefault("execution_config", {})
        plan["nodes"] = nodes

        node_conditions = {
            tuple(sorted(cond.items()))
            for node in nodes
            if isinstance(node, dict)
            for cond in (node.get("acceptance_criteria") or [])
            if isinstance(cond, dict)
        }
        top = plan.get("acceptance_criteria")
        if not isinstance(top, list) or not top:
            top = []
            for node in nodes:
                for cond in node.get("acceptance_criteria") or []:
                    if isinstance(cond, dict) and cond not in top:
                        top.append(cond)
        if nodes:
            last = nodes[-1]
            if isinstance(last, dict):
                last_criteria = last.setdefault("acceptance_criteria", [])
                if not isinstance(last_criteria, list):
                    last_criteria = []
                    last["acceptance_criteria"] = last_criteria
                for condition in top:
                    if not isinstance(condition, dict):
                        continue
                    key = tuple(sorted(condition.items()))
                    if key not in node_conditions:
                        last_criteria.append(condition)
                        node_conditions.add(key)
        plan["acceptance_criteria"] = top

        metadata = plan.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.setdefault("created_by", "planner")
        plan["metadata"] = metadata
        return plan

    # ---------------------------------------------------------------- answer
    def answer(
        self,
        plan_id: str,
        clarification_id: str,
        answer: str,
        *,
        answered_by: str = "cli",
        refine: bool = False,
    ) -> Plan:
        plan = self.store.get_plan(plan_id)
        if plan.status not in (PlanStatus.NEEDS_CLARIFICATION, PlanStatus.ANALYZING):
            raise ValidationError(f"plan {plan_id} is {plan.status.value}; cannot answer")
        self.store.answer_clarification(
            plan_id, clarification_id, answer, answered_by=answered_by
        )
        self.store.set_plan_status(plan_id, PlanStatus.ANALYZING)
        if refine:
            return self.generate(plan_id, refine=True)
        return self.store.get_plan(plan_id)

    def refine(self, plan_id: str) -> Plan:
        return self.generate(plan_id, refine=True)

    # -------------------------------------------------------------- validate
    def validate(self, plan_id: str) -> dict[str, Any]:
        plan = self.store.get_plan(plan_id)
        if not plan.plan_json:
            raise PlanValidationError(["plan has not been generated"])
        errors = validate_plan(plan.plan_json)
        pending = self.store.list_clarifications(plan_id, ClarificationStatus.PENDING)
        if pending:
            errors.extend(
                f"unresolved clarification: {item.question}" for item in pending
            )
        if errors:
            raise PlanValidationError(errors)
        return {"ok": True, "plan_id": plan_id, "errors": []}

    # --------------------------------------------------------------- confirm
    def confirm(self, plan_id: str, *, confirmed_by: str = "cli") -> PlanSnapshot:
        plan = self.store.get_plan(plan_id)
        if plan.status is not PlanStatus.PLAN_READY:
            raise ValidationError(f"plan {plan_id} is {plan.status.value}, not PLAN_READY")
        self.validate(plan_id)
        return self.store.confirm(plan_id, confirmed_by=confirmed_by)

    # ---------------------------------------------------------------- views
    def show(self, plan_id: str) -> dict[str, Any]:
        plan = self.store.get_plan(plan_id)
        clarifications = self.store.list_clarifications(plan_id)
        snapshot = self.store.get_snapshot(plan_id)
        return {
            "plan": plan,
            "clarifications": clarifications,
            "snapshot": snapshot,
        }

    def snapshot(self, plan_id: str, version: int | None = None) -> PlanSnapshot | None:
        return self.store.get_snapshot(plan_id, version)

    def history(self, plan_id: str) -> dict[str, Any]:
        return {
            "versions": self.store.list_versions(plan_id),
            "snapshots": self.store.list_snapshots(plan_id),
            "changes": self.store.list_change_requests(plan_id),
        }

    def changes(self, plan_id: str):
        return self.store.list_change_requests(plan_id)

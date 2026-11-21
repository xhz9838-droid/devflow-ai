"""Formal Plan Decision Policy (L0-L3), not prompt-only guidance."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .models import ChangeRisk


class DecisionLevel(str, Enum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


RISK_KEYWORDS = {
    "database",
    "db",
    "migration",
    "schema",
    "api",
    "contract",
    "security",
    "permission",
    "auth",
    "data loss",
    "delete",
    "drop",
    "irreversible",
    "compatibility",
    "breaking",
    "rework",
    "scope",
    "production",
    "payment",
    "privacy",
    "迁移",
    "数据库",
    "表结构",
    "删除",
    "权限",
    "安全",
    "兼容",
    "接口",
    "范围",
    "返工",
    "不可逆",
    "生产",
}

AUTHORIZATION_MARKERS = (
    "自主决定",
    "自行决定",
    "无需询问",
    "不需要询问",
    "按现有规范",
    "no need to ask",
    "decide yourself",
)


def normalize_question(text: str) -> str:
    return re.sub(r"[^\w一-鿿]+", " ", text.lower()).strip()


@dataclass(frozen=True)
class CandidateQuestion:
    question: str
    level: DecisionLevel
    reason: str = ""
    research: Any = ""
    impact: str = ""
    options: list[dict[str, str]] = field(default_factory=list)
    recommended: str | None = None

    @property
    def key(self) -> str:
        return normalize_question(self.question)

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "level": self.level.value,
            "reason": self.reason,
            "research": self.research,
            "impact": self.impact,
            "options": self.options,
            "recommended": self.recommended,
        }


@dataclass
class PolicyResult:
    clarifications: list[CandidateQuestion] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    suppressed: list[dict[str, Any]] = field(default_factory=list)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _has_research(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return value is not None


def _normalize_options(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for option in raw:
        if isinstance(option, str):
            result.append({"id": option, "label": option})
        elif isinstance(option, dict) and option.get("id") is not None:
            result.append(
                {"id": str(option["id"]), "label": str(option.get("label", option["id"]))}
            )
    return result


class DecisionPolicy:
    """Only L3 questions reach the user. L0-L2 are auto-decided or suppressed."""

    def __init__(self, max_questions: int = 5) -> None:
        self.max_questions = max(1, max_questions)

    def evaluate(
        self,
        candidates: list[dict[str, Any]],
        *,
        objective: str,
        context: dict[str, Any] | None = None,
        authorizations: list[str] | None = None,
        resolved_questions: set[str] | None = None,
    ) -> PolicyResult:
        resolved_questions = resolved_questions or set()
        resolved_questions = {normalize_question(item) for item in resolved_questions}
        authorization_text = " ".join(authorizations or []).lower()
        authorized = any(marker in authorization_text for marker in AUTHORIZATION_MARKERS)
        result = PolicyResult()
        seen: set[str] = set()

        for raw in candidates:
            if not isinstance(raw, dict):
                result.suppressed.append({"question": str(raw), "reason": "invalid candidate"})
                continue
            question = _text(raw.get("question") or raw.get("title")).strip()
            if not question:
                result.suppressed.append({"question": "", "reason": "missing question"})
                continue
            try:
                level = DecisionLevel(str(raw.get("level", "L3")).upper())
            except ValueError:
                level = DecisionLevel.L3
            candidate = CandidateQuestion(
                question=question,
                level=level,
                reason=_text(raw.get("reason")),
                research=raw.get("research") or "",
                impact=_text(raw.get("impact")),
                options=_normalize_options(raw.get("options")),
                recommended=(
                    str(raw["recommended"]) if raw.get("recommended") is not None else None
                ),
            )
            if candidate.key in seen or candidate.key in resolved_questions:
                continue
            seen.add(candidate.key)

            if level in (DecisionLevel.L0, DecisionLevel.L1, DecisionLevel.L2):
                result.decisions.append(self._decision(candidate, "auto-decided"))
                continue

            risk_text = " ".join(
                [question, candidate.reason, candidate.impact, objective]
            ).lower()
            risky = any(keyword in risk_text for keyword in RISK_KEYWORDS)
            if authorized and not risky:
                result.decisions.append(self._decision(candidate, "covered by user authorization"))
                continue
            if not _has_research(candidate.research):
                result.suppressed.append(
                    {
                        **candidate.as_dict(),
                        "reason": "research-first: no repository evidence, not asked",
                    }
                )
                result.decisions.append(self._decision(candidate, "deferred until researched"))
                continue
            if not candidate.impact:
                result.suppressed.append(
                    {
                        **candidate.as_dict(),
                        "reason": "L3 requires impact explanation, not asked",
                    }
                )
                result.decisions.append(self._decision(candidate, "auto-decided without impact"))
                continue
            if len(candidate.options) < 2:
                result.decisions.append(
                    self._decision(candidate, "single/no option: use recommended/default")
                )
                continue
            if candidate.recommended is None:
                candidate = CandidateQuestion(
                    question=candidate.question,
                    level=candidate.level,
                    reason=candidate.reason,
                    research=candidate.research,
                    impact=candidate.impact,
                    options=candidate.options,
                    recommended=candidate.options[0]["id"],
                )
            option_ids = {option["id"] for option in candidate.options}
            if candidate.recommended not in option_ids:
                result.suppressed.append(
                    {
                        **candidate.as_dict(),
                        "reason": "recommended option is not one of the options",
                    }
                )
                continue
            if len(result.clarifications) >= self.max_questions:
                result.suppressed.append(
                    {
                        **candidate.as_dict(),
                        "reason": "question batch limit reached",
                    }
                )
                continue
            result.clarifications.append(candidate)

        return result

    def _decision(self, candidate: CandidateQuestion, reason: str) -> dict[str, Any]:
        choice = candidate.recommended
        if choice is None and candidate.options:
            choice = candidate.options[0]["id"]
        if choice is None:
            choice = "follow existing project conventions"
        return {
            "level": candidate.level.value,
            "subject": candidate.question,
            "choice": choice,
            "reason": candidate.reason or reason,
        }


def classify_change_risk(
    *,
    declared: str | None,
    title: str,
    reason: str,
    impact: str,
    proposed_changes: dict[str, Any],
    completed_node_ids: set[str],
    terminal_node_ids: set[str] | None = None,
) -> ChangeRisk:
    """Core decides risk; an agent cannot downgrade a structural change."""
    terminal = (
        set(terminal_node_ids)
        if terminal_node_ids is not None
        else set(completed_node_ids)
    )
    if declared and declared.upper() == ChangeRisk.MAJOR.value:
        return ChangeRisk.MAJOR
    text = " ".join([title, reason, impact, str(proposed_changes)]).lower()
    if any(keyword in text for keyword in RISK_KEYWORDS):
        return ChangeRisk.MAJOR
    affected = set(proposed_changes.get("affected_nodes") or [])
    if affected & terminal:
        return ChangeRisk.MAJOR
    if proposed_changes.get("remove_nodes"):
        return ChangeRisk.MAJOR
    for update in proposed_changes.get("update_nodes") or []:
        if isinstance(update, dict) and (
            update.get("force_rework") or str(update.get("id")) in terminal
        ):
            return ChangeRisk.MAJOR
    for added in proposed_changes.get("add_nodes") or []:
        if isinstance(added, dict) and str(added.get("id")) in terminal:
            return ChangeRisk.MAJOR
    return ChangeRisk.LOW

"""Planning layer domain models. No workflow state is written here."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..errors import InvalidTransition


class PlanStatus(str, Enum):
    DRAFT = "DRAFT"
    INVESTIGATING = "INVESTIGATING"
    ANALYZING = "ANALYZING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    PLAN_READY = "PLAN_READY"
    CONFIRMED = "CONFIRMED"
    EXECUTION_READY = "EXECUTION_READY"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


PLAN_TRANSITIONS: dict[PlanStatus, set[PlanStatus]] = {
    PlanStatus.DRAFT: {
        PlanStatus.INVESTIGATING,
        PlanStatus.ANALYZING,
        PlanStatus.FAILED,
        PlanStatus.CANCELLED,
    },
    PlanStatus.INVESTIGATING: {
        PlanStatus.ANALYZING,
        PlanStatus.NEEDS_CLARIFICATION,
        PlanStatus.PLAN_READY,
        PlanStatus.FAILED,
        PlanStatus.CANCELLED,
    },
    PlanStatus.ANALYZING: {
        PlanStatus.NEEDS_CLARIFICATION,
        PlanStatus.PLAN_READY,
        PlanStatus.FAILED,
        PlanStatus.CANCELLED,
    },
    PlanStatus.NEEDS_CLARIFICATION: {
        PlanStatus.ANALYZING,
        PlanStatus.PLAN_READY,
        PlanStatus.FAILED,
        PlanStatus.CANCELLED,
    },
    PlanStatus.PLAN_READY: {
        PlanStatus.CONFIRMED,
        PlanStatus.ANALYZING,
        PlanStatus.FAILED,
        PlanStatus.CANCELLED,
    },
    PlanStatus.CONFIRMED: {PlanStatus.EXECUTION_READY, PlanStatus.CANCELLED},
    PlanStatus.EXECUTION_READY: {PlanStatus.CANCELLED},
    PlanStatus.FAILED: {
        PlanStatus.INVESTIGATING,
        PlanStatus.ANALYZING,
        PlanStatus.CANCELLED,
    },
    PlanStatus.CANCELLED: set(),
}

PLAN_TERMINAL = {PlanStatus.EXECUTION_READY, PlanStatus.CANCELLED}


class ClarificationStatus(str, Enum):
    PENDING = "PENDING"
    ANSWERED = "ANSWERED"
    CANCELLED = "CANCELLED"


class ChangeRisk(str, Enum):
    LOW = "LOW"
    MAJOR = "MAJOR"


class ChangeStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


def ensure_plan_transition(current: PlanStatus, target: PlanStatus) -> None:
    if target not in PLAN_TRANSITIONS[current]:
        raise InvalidTransition("plan", current.value, target.value)


@dataclass
class Plan:
    id: str
    title: str
    objective: str
    status: PlanStatus
    project_context: dict[str, Any] = field(default_factory=dict)
    plan_json: dict[str, Any] | None = None
    version: int = 0
    workflow_id: str | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""
    confirmed_at: str | None = None
    confirmed_by: str | None = None


@dataclass
class PlanClarification:
    id: str
    plan_id: str
    question: str
    level: str
    reason: str
    research: Any
    impact: str
    options: list[dict[str, str]]
    recommended: str | None
    status: ClarificationStatus
    answer: str | None = None
    answered_by: str | None = None
    created_at: str = ""
    answered_at: str | None = None


@dataclass
class PlanSnapshot:
    id: str
    plan_id: str
    version: int
    plan_json: dict[str, Any]
    content_hash: str
    created_at: str
    confirmed_by: str | None = None


@dataclass
class PlanVersion:
    id: str
    plan_id: str
    version: int
    plan_json: dict[str, Any]
    content_hash: str
    created_at: str
    created_by: str | None = None
    snapshot_id: str | None = None


@dataclass
class PlanChangeRequest:
    id: str
    plan_id: str
    workflow_id: str
    node_id: str | None
    risk_level: ChangeRisk
    status: ChangeStatus
    title: str
    reason: str
    impact: str
    proposed_changes: dict[str, Any]
    decision_id: str | None = None
    new_version: int | None = None
    note: str | None = None
    created_at: str = ""
    resolved_at: str | None = None
    resolved_by: str | None = None

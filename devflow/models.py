"""Authoritative domain models. JSON columns stay plain dicts/lists."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class WorkflowStatus(str, Enum):
    CREATED = "CREATED"
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    WAITING_HUMAN = "WAITING_HUMAN"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class NodeStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_HUMAN = "WAITING_HUMAN"
    BLOCKED = "BLOCKED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class AgentRunStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"


class DecisionStatus(str, Enum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


@dataclass
class Workflow:
    id: str
    name: str
    goal: str
    status: WorkflowStatus
    context: dict[str, Any] = field(default_factory=dict)
    plan_id: str | None = None
    plan_snapshot_id: str | None = None
    plan_version: int | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class Stage:
    id: str
    workflow_id: str
    name: str
    sequence: int
    status: str = "PENDING"
    created_at: str = ""


@dataclass
class Node:
    id: str
    workflow_id: str
    name: str
    status: NodeStatus
    dependencies: list[str] = field(default_factory=list)
    stage_id: str | None = None
    execution_config: dict[str, Any] = field(default_factory=dict)
    retry_policy: dict[str, Any] = field(default_factory=dict)
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] | None = None
    error: str | None = None
    attempts: int = 0
    required: bool = True
    idempotency_key: str | None = None
    created_at: str = ""
    updated_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class AgentRun:
    id: str
    workflow_id: str
    node_id: str
    adapter: str
    status: AgentRunStatus
    attempt: int = 1
    prompt: str = ""
    workdir: str = ""
    pid: int | None = None
    process_start_time: str | None = None
    exit_code: int | None = None
    output: str = ""
    error: str | None = None
    timeout_seconds: float = 0.0
    started_at: str = ""
    finished_at: str | None = None
    heartbeat_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HumanDecision:
    id: str
    workflow_id: str
    node_id: str | None
    decision_type: str
    question: str
    status: DecisionStatus
    context: dict[str, Any] = field(default_factory=dict)
    options: list[dict[str, Any]] = field(default_factory=list)
    selected_option: str | None = None
    decision_payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    resolved_at: str | None = None
    resolved_by: str | None = None
    expires_at: str | None = None
    note: str | None = None


@dataclass
class Checkpoint:
    id: str
    workflow_id: str
    node_id: str | None
    reason: str
    sequence: int
    state_snapshot: dict[str, Any]
    context: dict[str, Any]
    created_at: str


@dataclass
class ExecutionEvent:
    id: int
    workflow_id: str
    node_id: str | None
    event_type: str
    payload: dict[str, Any]
    actor: str
    created_at: str


@dataclass
class RetryRecord:
    id: int
    workflow_id: str
    node_id: str
    agent_run_id: str | None
    attempt: int
    reason: str
    next_retry_at: str | None
    created_at: str


@dataclass
class VerifyReport:
    passed: bool
    summary: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    failed_conditions: list[str] = field(default_factory=list)
    verified_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "evidence": self.evidence,
            "failed_conditions": self.failed_conditions,
            "verified_at": self.verified_at or utcnow(),
        }

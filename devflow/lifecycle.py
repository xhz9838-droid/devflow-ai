"""Lifecycle state machines. Every status write goes through these rules."""

from __future__ import annotations

from typing import Iterable

from .errors import InvalidTransition
from .models import AgentRunStatus, DecisionStatus, NodeStatus, WorkflowStatus

WORKFLOW_TRANSITIONS: dict[WorkflowStatus, set[WorkflowStatus]] = {
    WorkflowStatus.CREATED: {WorkflowStatus.INITIALIZING, WorkflowStatus.CANCELLED},
    WorkflowStatus.INITIALIZING: {
        WorkflowStatus.RUNNING,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.RUNNING: {
        WorkflowStatus.WAITING,
        WorkflowStatus.WAITING_HUMAN,
        WorkflowStatus.RECOVERING,
        WorkflowStatus.COMPLETED,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.WAITING: {
        WorkflowStatus.RUNNING,
        WorkflowStatus.WAITING_HUMAN,
        WorkflowStatus.RECOVERING,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.WAITING_HUMAN: {
        WorkflowStatus.RUNNING,
        WorkflowStatus.RECOVERING,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.RECOVERING: {
        WorkflowStatus.RUNNING,
        WorkflowStatus.WAITING,
        WorkflowStatus.WAITING_HUMAN,
        WorkflowStatus.FAILED,
        WorkflowStatus.CANCELLED,
    },
    WorkflowStatus.COMPLETED: set(),
    WorkflowStatus.FAILED: set(),
    WorkflowStatus.CANCELLED: set(),
}

NODE_TRANSITIONS: dict[NodeStatus, set[NodeStatus]] = {
    NodeStatus.PENDING: {
        NodeStatus.READY,
        NodeStatus.BLOCKED,
        NodeStatus.SKIPPED,
        NodeStatus.CANCELLED,
    },
    NodeStatus.READY: {NodeStatus.RUNNING, NodeStatus.BLOCKED, NodeStatus.CANCELLED},
    NodeStatus.RUNNING: {
        NodeStatus.READY,
        NodeStatus.SUCCEEDED,
        NodeStatus.FAILED,
        NodeStatus.WAITING_HUMAN,
        NodeStatus.CANCELLED,
    },
    NodeStatus.WAITING_HUMAN: {
        NodeStatus.READY,
        NodeStatus.FAILED,
        NodeStatus.CANCELLED,
    },
    NodeStatus.BLOCKED: {NodeStatus.READY, NodeStatus.CANCELLED},
    NodeStatus.FAILED: {NodeStatus.READY, NodeStatus.CANCELLED},
    NodeStatus.SUCCEEDED: set(),
    NodeStatus.SKIPPED: set(),
    NodeStatus.CANCELLED: set(),
}

AGENT_RUN_TRANSITIONS: dict[AgentRunStatus, set[AgentRunStatus]] = {
    AgentRunStatus.CREATED: {
        AgentRunStatus.RUNNING,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    },
    AgentRunStatus.RUNNING: {
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.TIMEOUT,
        AgentRunStatus.INTERRUPTED,
        AgentRunStatus.CANCELLED,
    },
    AgentRunStatus.SUCCEEDED: set(),
    AgentRunStatus.FAILED: set(),
    AgentRunStatus.TIMEOUT: set(),
    AgentRunStatus.INTERRUPTED: set(),
    AgentRunStatus.CANCELLED: set(),
}

DECISION_TRANSITIONS: dict[DecisionStatus, set[DecisionStatus]] = {
    DecisionStatus.PENDING: {
        DecisionStatus.RESOLVED,
        DecisionStatus.CANCELLED,
        DecisionStatus.TIMEOUT,
    },
    DecisionStatus.RESOLVED: set(),
    DecisionStatus.CANCELLED: set(),
    DecisionStatus.TIMEOUT: set(),
}

WORKFLOW_TERMINAL = {
    WorkflowStatus.COMPLETED,
    WorkflowStatus.FAILED,
    WorkflowStatus.CANCELLED,
}

NODE_TERMINAL = {
    NodeStatus.SUCCEEDED,
    NodeStatus.FAILED,
    NodeStatus.SKIPPED,
    NodeStatus.CANCELLED,
}


def _machines() -> dict[str, dict]:
    return {
        "workflow": WORKFLOW_TRANSITIONS,
        "node": NODE_TRANSITIONS,
        "agent_run": AGENT_RUN_TRANSITIONS,
        "decision": DECISION_TRANSITIONS,
    }


def can_transition(entity: str, current: str | object, target: str | object) -> bool:
    machine = _machines()[entity]
    return target in machine[current]  # type: ignore[index]


def ensure_transition(entity: str, current: str | object, target: str | object) -> None:
    if not can_transition(entity, current, target):
        raise InvalidTransition(
            entity,
            getattr(current, "value", str(current)),
            getattr(target, "value", str(target)),
        )


def is_workflow_terminal(status: WorkflowStatus) -> bool:
    return status in WORKFLOW_TERMINAL


def is_node_terminal(status: NodeStatus) -> bool:
    return status in NODE_TERMINAL


def is_any_terminal(workflow_statuses: Iterable[WorkflowStatus]) -> bool:
    return all(status in WORKFLOW_TERMINAL for status in workflow_statuses)

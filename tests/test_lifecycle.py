import pytest

from devflow.errors import InvalidTransition
from devflow.lifecycle import (
    can_transition,
    ensure_transition,
    is_node_terminal,
    is_workflow_terminal,
)
from devflow.models import (
    AgentRunStatus,
    DecisionStatus,
    NodeStatus,
    WorkflowStatus,
)


def test_happy_path_workflow_lifecycle():
    path = [
        WorkflowStatus.CREATED,
        WorkflowStatus.INITIALIZING,
        WorkflowStatus.RUNNING,
        WorkflowStatus.WAITING_HUMAN,
        WorkflowStatus.RUNNING,
        WorkflowStatus.COMPLETED,
    ]
    for current, target in zip(path, path[1:]):
        ensure_transition("workflow", current, target)


def test_workflow_can_recover_from_waiting_and_recovering():
    assert can_transition("workflow", WorkflowStatus.WAITING, WorkflowStatus.RECOVERING)
    assert can_transition("workflow", WorkflowStatus.RECOVERING, WorkflowStatus.RUNNING)
    assert can_transition("workflow", WorkflowStatus.RUNNING, WorkflowStatus.RECOVERING)


@pytest.mark.parametrize(
    "current,target",
    [
        (WorkflowStatus.CREATED, WorkflowStatus.COMPLETED),
        (WorkflowStatus.CREATED, WorkflowStatus.RUNNING),
        (WorkflowStatus.COMPLETED, WorkflowStatus.RUNNING),
        (WorkflowStatus.FAILED, WorkflowStatus.RECOVERING),
        (WorkflowStatus.CANCELLED, WorkflowStatus.RUNNING),
    ],
)
def test_illegal_workflow_transitions_raise(current, target):
    with pytest.raises(InvalidTransition):
        ensure_transition("workflow", current, target)


def test_terminal_workflows_have_no_exits():
    for status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
        assert is_workflow_terminal(status)
    assert not is_workflow_terminal(WorkflowStatus.RUNNING)


def test_node_lifecycle_including_retry_and_human_wait():
    path = [
        NodeStatus.PENDING,
        NodeStatus.READY,
        NodeStatus.RUNNING,
        NodeStatus.WAITING_HUMAN,
        NodeStatus.READY,
        NodeStatus.RUNNING,
        NodeStatus.FAILED,
        NodeStatus.READY,
        NodeStatus.RUNNING,
        NodeStatus.SUCCEEDED,
    ]
    for current, target in zip(path, path[1:]):
        ensure_transition("node", current, target)
    assert is_node_terminal(NodeStatus.SUCCEEDED)
    assert not is_node_terminal(NodeStatus.WAITING_HUMAN)


def test_illegal_node_transitions_raise():
    with pytest.raises(InvalidTransition):
        ensure_transition("node", NodeStatus.SUCCEEDED, NodeStatus.RUNNING)
    with pytest.raises(InvalidTransition):
        ensure_transition("node", NodeStatus.PENDING, NodeStatus.RUNNING)


def test_agent_run_lifecycle():
    for status in (
        AgentRunStatus.SUCCEEDED,
        AgentRunStatus.FAILED,
        AgentRunStatus.TIMEOUT,
        AgentRunStatus.INTERRUPTED,
        AgentRunStatus.CANCELLED,
    ):
        ensure_transition("agent_run", AgentRunStatus.RUNNING, status)
        with pytest.raises(InvalidTransition):
            ensure_transition("agent_run", status, AgentRunStatus.RUNNING)


def test_decision_lifecycle():
    for status in (DecisionStatus.RESOLVED, DecisionStatus.CANCELLED, DecisionStatus.TIMEOUT):
        ensure_transition("decision", DecisionStatus.PENDING, status)
        with pytest.raises(InvalidTransition):
            ensure_transition("decision", status, DecisionStatus.PENDING)


def test_unknown_entity_is_rejected():
    with pytest.raises(KeyError):
        can_transition("unknown", WorkflowStatus.RUNNING, WorkflowStatus.COMPLETED)

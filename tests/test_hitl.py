import pytest

from devflow.errors import DecisionError, ValidationError
from devflow.hitl import HumanDecisionManager
from devflow.models import DecisionStatus, NodeStatus, WorkflowStatus

SPEC = {
    "question": "Approve the design?",
    "decision_type": "approval",
    "options": [{"id": "approve", "label": "Approve"}, {"id": "reject", "label": "Reject"}],
}


def running_node(store, create_workflow):
    workflow = create_workflow([{"id": "n"}])
    store.update_workflow_status(workflow.id, WorkflowStatus.INITIALIZING)
    store.update_workflow_status(workflow.id, WorkflowStatus.RUNNING)
    store.update_node_status("n", NodeStatus.READY)
    store.update_node_status("n", NodeStatus.RUNNING)
    return workflow


def test_request_persists_decision_and_parks_node(store, create_workflow):
    workflow = running_node(store, create_workflow)
    manager = HumanDecisionManager(store)
    decision = manager.request(workflow.id, "n", SPEC)

    assert decision.status is DecisionStatus.PENDING
    assert decision.options[0]["id"] == "approve"
    assert store.get_node("n").status is NodeStatus.WAITING_HUMAN
    assert store.find_pending_decision("n").id == decision.id
    assert "human_decision.requested" in [e.event_type for e in store.list_events(workflow.id)]


def test_request_is_idempotent_per_node(store, create_workflow):
    workflow = running_node(store, create_workflow)
    manager = HumanDecisionManager(store)
    first = manager.request(workflow.id, "n", SPEC)
    second = manager.request(workflow.id, "n", SPEC)
    assert first.id == second.id


def test_duplicate_options_are_rejected(store, create_workflow):
    workflow = running_node(store, create_workflow)
    with pytest.raises(ValidationError):
        HumanDecisionManager(store).request(
            workflow.id, "n", {"question": "Q", "options": ["a", "a"]}
        )


def test_resolve_validates_options(store, create_workflow):
    workflow = running_node(store, create_workflow)
    manager = HumanDecisionManager(store)
    decision = manager.request(workflow.id, "n", SPEC)

    with pytest.raises(DecisionError):
        manager.resolve(decision.id, "maybe")
    with pytest.raises(DecisionError):
        manager.resolve(decision.id)
    assert store.get_decision(decision.id).status is DecisionStatus.PENDING
    assert store.get_node("n").status is NodeStatus.WAITING_HUMAN


def test_resolve_requeues_node_and_resumes_workflow(store, create_workflow):
    workflow = running_node(store, create_workflow)
    manager = HumanDecisionManager(store)
    decision = manager.request(workflow.id, "n", SPEC)
    store.update_workflow_status(workflow.id, WorkflowStatus.WAITING_HUMAN)

    resolved = manager.resolve(
        decision.id, "approve", {"comment": "lgtm"}, resolved_by="tester"
    )
    assert resolved.status is DecisionStatus.RESOLVED
    assert resolved.selected_option == "approve"
    assert resolved.decision_payload == {"comment": "lgtm"}
    assert store.get_node("n").status is NodeStatus.READY
    assert store.get_workflow(workflow.id).status is WorkflowStatus.RUNNING


def test_resolution_keeps_workflow_waiting_while_other_decisions_are_pending(store, create_workflow):
    workflow = running_node(store, create_workflow)
    store.update_node_status("n", NodeStatus.WAITING_HUMAN)
    second = store.create_nodes(workflow.id, [{"id": "m"}])[0]
    store.update_node_status(second.id, NodeStatus.READY)
    store.update_node_status(second.id, NodeStatus.RUNNING)
    manager = HumanDecisionManager(store)
    d1 = manager.request(workflow.id, "n", SPEC)
    manager.request(workflow.id, "m", SPEC)
    store.update_workflow_status(workflow.id, WorkflowStatus.WAITING_HUMAN)

    manager.resolve(d1.id, "approve")
    assert store.get_workflow(workflow.id).status is WorkflowStatus.WAITING_HUMAN


def test_timeout_fails_the_node(store, create_workflow):
    workflow = running_node(store, create_workflow)
    manager = HumanDecisionManager(store)
    decision = manager.request(
        workflow.id, "n", {"question": "Q?", "timeout_seconds": -1}
    )
    expired = manager.expire_overdue(workflow.id)
    assert [d.id for d in expired] == [decision.id]
    assert store.get_decision(decision.id).status is DecisionStatus.TIMEOUT
    assert store.get_node("n").status is NodeStatus.FAILED
    assert "timed out" in store.get_node("n").error


def test_cancel_fails_the_node(store, create_workflow):
    workflow = running_node(store, create_workflow)
    manager = HumanDecisionManager(store)
    decision = manager.request(workflow.id, "n", SPEC)
    cancelled = manager.cancel(decision.id, note="not needed")
    assert cancelled.status is DecisionStatus.CANCELLED
    assert store.get_node("n").status is NodeStatus.FAILED


def test_resolution_after_timeout_is_rejected(store, create_workflow):
    workflow = running_node(store, create_workflow)
    manager = HumanDecisionManager(store)
    decision = manager.request(workflow.id, "n", {"question": "Q?", "timeout_seconds": -1})
    manager.expire_overdue(workflow.id)
    with pytest.raises(DecisionError):
        manager.resolve(decision.id, "approve")

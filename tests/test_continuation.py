import json

import pytest

from devflow.continuation import CheckpointManager, build_continuation_context
from devflow.errors import CheckpointError
from devflow.models import NodeStatus
from devflow.scheduler import DAGScheduler


def test_checkpoint_sequence_snapshot_and_context(store, create_workflow):
    workflow = create_workflow(
        [
            {"id": "a", "idempotency_key": "side-effect-a"},
            {"id": "b", "dependencies": ["a"]},
        ]
    )
    store.update_node_status("a", NodeStatus.READY)
    manager = CheckpointManager(store)

    first = manager.save(workflow.id, "started")
    assert first.sequence == 1
    assert first.state_snapshot["workflow"]["id"] == workflow.id
    assert {n["id"] for n in first.state_snapshot["nodes"]} == {"a", "b"}

    store.update_node_status("a", NodeStatus.RUNNING)
    store.update_node_status("a", NodeStatus.SUCCEEDED, output={"summary": "done"})
    from devflow.scheduler import DAGScheduler

    DAGScheduler(store).refresh(workflow.id)
    second = manager.save(workflow.id, "node_completed")
    assert second.sequence == 2
    snapshot_a = next(n for n in second.state_snapshot["nodes"] if n["id"] == "a")
    assert snapshot_a["status"] == NodeStatus.SUCCEEDED.value
    assert snapshot_a["output"]["summary"] == "done"

    context = second.context
    assert context["workflow_id"] == workflow.id
    assert context["goal"] == workflow.goal
    assert context["progress"]["completed"] == 1
    assert context["side_effects_completed"] == [
        {"node_id": "a", "idempotency_key": "side-effect-a"}
    ]
    assert context["next_actions"] and "b" in context["next_actions"][0]
    assert context["checkpoint_source"] == "sqlite"


def test_context_marks_open_decisions_and_failures(store, create_workflow):
    workflow = create_workflow([{"id": "a"}, {"id": "b"}])
    store.create_decision(workflow.id, "a", "approval", "Approve?")
    store.update_workflow_status(workflow.id, "INITIALIZING")
    store.update_workflow_status(workflow.id, "RUNNING")
    store.update_node_status("a", NodeStatus.READY)
    store.update_node_status("a", NodeStatus.RUNNING)
    store.update_node_status("a", NodeStatus.WAITING_HUMAN)

    context = CheckpointManager(store).build_context(workflow.id)
    assert len(context["open_decisions"]) == 1
    assert any("resolve decision" in action for action in context["next_actions"])
    assert any("Approve?" in issue for issue in context["unresolved_issues"])


def test_handoff_prefers_latest_checkpoint(store, create_workflow):
    workflow = create_workflow([{"id": "a"}])
    manager = CheckpointManager(store)
    manager.save(workflow.id, "started")
    store.update_node_status("a", NodeStatus.READY)
    assert manager.handoff_context(workflow.id)["progress"]["ready"] == 0
    assert manager.build_context(workflow.id)["progress"]["ready"] == 1


def test_checkpoint_failure_is_not_swallowed(store, create_workflow, monkeypatch):
    workflow = create_workflow([{"id": "a"}])
    manager = CheckpointManager(store)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "insert_checkpoint", boom)
    with pytest.raises(CheckpointError, match="disk full"):
        manager.save(workflow.id, "started")
    assert store.list_checkpoints(workflow.id) == []


def test_completed_node_output_is_ready_for_handoff(store, create_workflow):
    workflow = create_workflow(
        [
            {"id": "research", "idempotency_key": "research-side-effect"},
            {"id": "handoff", "dependencies": ["research"]},
        ]
    )
    store.update_node_status("research", NodeStatus.READY)
    store.update_node_status("research", NodeStatus.RUNNING)
    store.update_node_status(
        "research",
        NodeStatus.SUCCEEDED,
        output={
            "output": json.dumps({"result": "TOKEN-WCP-1", "session_id": "s"}),
            "verification": {"passed": True},
            "artifacts": ["research.md"],
        },
    )
    DAGScheduler(store).refresh(workflow.id)

    context = CheckpointManager(store).build_context(workflow.id)
    completed = context["completed_nodes"][0]
    assert completed["id"] == "research"
    assert completed["output"] == "TOKEN-WCP-1"
    assert completed["verification_passed"] is True
    assert completed["artifacts"] == ["research.md"]
    assert completed["idempotency_key"] == "research-side-effect"


def test_context_builder_handles_empty_workflow(store, create_workflow):
    from dataclasses import asdict

    workflow = create_workflow([{"id": "a"}])
    context = build_continuation_context(
        {
            "workflow": asdict(store.get_workflow(workflow.id)),
            "nodes": [],
            "decisions": [],
            "recent_events": [],
            "stages": [],
        }
    )
    assert context["next_actions"] == ["request recovery"]

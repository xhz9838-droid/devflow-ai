import sqlite3

import pytest

from devflow.db import Database
from devflow.errors import DecisionError, InvalidTransition, NotFoundError
from devflow.models import DecisionStatus, NodeStatus, WorkflowStatus


def test_migrations_are_idempotent(tmp_path):
    db = Database(tmp_path / "m.db")
    assert db.migrate() == 2
    assert db.migrate() == 2
    with db.read() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {
        "workflows",
        "workflow_stages",
        "workflow_nodes",
        "agent_runs",
        "execution_events",
        "checkpoints",
        "human_decisions",
        "retry_records",
        "schema_migrations",
    } <= tables


def test_workflow_and_node_creation(store, create_workflow):
    workflow = create_workflow([{"id": "a", "name": "A"}, {"id": "b", "dependencies": ["a"]}])
    assert workflow.status is WorkflowStatus.CREATED
    assert [n.id for n in store.list_nodes(workflow.id)] == ["a", "b"]
    assert store.list_stages(workflow.id)[0].name == "main"
    events = [e.event_type for e in store.list_events(workflow.id)]
    assert events == ["workflow.created", "stage.created", "nodes.created"]


def test_status_changes_are_persisted_with_events(store, create_workflow):
    workflow = create_workflow([{"id": "a"}])
    store.update_workflow_status(workflow.id, WorkflowStatus.INITIALIZING, "init")
    updated = store.update_workflow_status(workflow.id, WorkflowStatus.RUNNING, "go")
    assert updated.status is WorkflowStatus.RUNNING
    assert updated.started_at is not None
    events = store.list_events(workflow.id)
    assert events[-1].payload["from"] == "INITIALIZING"
    assert events[-1].payload["to"] == "RUNNING"


def test_illegal_transition_leaves_state_untouched(store, create_workflow):
    workflow = create_workflow([{"id": "a"}])
    with pytest.raises(InvalidTransition):
        store.update_workflow_status(workflow.id, WorkflowStatus.COMPLETED)
    assert store.get_workflow(workflow.id).status is WorkflowStatus.CREATED


def test_missing_entities_raise_not_found(store):
    with pytest.raises(NotFoundError):
        store.get_workflow("missing")
    with pytest.raises(NotFoundError):
        store.get_node("missing")


def test_node_claim_is_atomic(store, create_workflow):
    workflow = create_workflow([{"id": "a"}])
    store.update_node_status("a", NodeStatus.READY, "test")
    first = store.claim_node("a")
    second = store.claim_node("a")
    assert first is not None and first.status is NodeStatus.RUNNING
    assert second is None
    assert store.get_node("a").attempts == 0


def test_attempts_increment(store, create_workflow):
    create_workflow([{"id": "a"}])
    assert store.increment_attempts("a") == 1
    assert store.increment_attempts("a") == 2


def test_agent_run_process_identity_is_persisted(store, create_workflow):
    workflow = create_workflow([{"id": "a"}])
    run = store.create_agent_run(workflow.id, "a", "stub", 1, timeout_seconds=10)
    assert store.set_agent_run_process(run.id, 1234, "win:5678") is True
    updated = store.get_agent_run(run.id)
    assert updated.pid == 1234
    assert updated.process_start_time == "win:5678"
    assert "agent.process_started" in [e.event_type for e in store.list_events(workflow.id)]


def test_duplicate_pending_decision_is_rejected_by_database(store, create_workflow):
    workflow = create_workflow([{"id": "a"}])
    store.create_decision(workflow.id, "a", "approval", "Approve?")
    with pytest.raises(DecisionError):
        store.create_decision(workflow.id, "a", "approval", "Again?")
    assert len(store.list_decisions(workflow.id, DecisionStatus.PENDING)) == 1


def test_same_status_writes_are_idempotent(store, create_workflow):
    workflow = create_workflow([{"id": "a"}])
    before = len(store.list_events(workflow.id))
    assert store.update_workflow_status(workflow.id, WorkflowStatus.CREATED).status is WorkflowStatus.CREATED
    assert store.update_node_status("a", NodeStatus.PENDING).status is NodeStatus.PENDING
    assert len(store.list_events(workflow.id)) == before


def test_current_workflow_is_single_authority(store, create_workflow):
    """No parallel state file: re-reading the same SQLite row sees the change."""
    workflow = create_workflow([{"id": "a"}])
    store.update_workflow_status(workflow.id, WorkflowStatus.INITIALIZING)
    store.update_workflow_status(workflow.id, WorkflowStatus.RUNNING)
    reopened = Database(store.db.path)
    with reopened.read() as conn:
        row = conn.execute("SELECT status FROM workflows WHERE id = ?", (workflow.id,)).fetchone()
    assert row["status"] == "RUNNING"


def test_foreign_keys_are_enforced(store):
    with pytest.raises(sqlite3.IntegrityError):
        with store.db.transaction() as conn:
            conn.execute(
                "INSERT INTO workflow_nodes(id, workflow_id, name, status, created_at, updated_at)"
                " VALUES ('x', 'ghost', 'x', 'PENDING', 'now', 'now')"
            )

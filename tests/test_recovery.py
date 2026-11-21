from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from devflow.models import AgentRunStatus, DecisionStatus, NodeStatus, WorkflowStatus
from devflow.recovery import RecoveryManager
from devflow.scheduler import DAGScheduler
from devflow.verifier import DefaultVerifier


def _recovery(store, grace_seconds=0.0):
    return RecoveryManager(
        store, DAGScheduler(store), DefaultVerifier(), grace_seconds=grace_seconds
    )


def _running_workflow(store, create_workflow, specs=None):
    workflow = create_workflow(
        specs
        or [{"id": "a", "retry_policy": {"max_retries": 2, "base_delay_seconds": 0}}]
    )
    store.update_workflow_status(workflow.id, WorkflowStatus.INITIALIZING)
    store.update_workflow_status(workflow.id, WorkflowStatus.RUNNING)
    return workflow


def _claim(store, node_id="a"):
    store.update_node_status(node_id, NodeStatus.READY)
    return store.claim_node(node_id)


def test_interrupted_run_is_retried_not_failed(store, create_workflow):
    workflow = _running_workflow(store, create_workflow)
    _claim(store)
    store.increment_attempts("a")
    run = store.create_agent_run(workflow.id, "a", "stub", 1, timeout_seconds=30)
    store.update_agent_run(run.id, AgentRunStatus.INTERRUPTED, error="process gone")

    result = _recovery(store).recover_workflow(workflow.id)

    assert result.action == "resumed"
    assert store.get_node("a").status is NodeStatus.READY
    assert store.get_workflow(workflow.id).status is WorkflowStatus.RUNNING
    retries = store.list_retries(workflow.id)
    assert len(retries) == 1
    assert retries[0].attempt == 2
    assert "interrupted" in retries[0].reason
    assert result.details["retried"] == ["a"]


def test_retry_exhaustion_marks_node_failed(store, create_workflow):
    workflow = _running_workflow(
        store, create_workflow, [{"id": "a", "retry_policy": {"max_retries": 1}}]
    )
    _claim(store)
    for _ in range(2):
        store.increment_attempts("a")
    run = store.create_agent_run(workflow.id, "a", "stub", 2, timeout_seconds=30)
    store.update_agent_run(run.id, AgentRunStatus.INTERRUPTED)

    result = _recovery(store).recover_workflow(workflow.id)

    node = store.get_node("a")
    assert node.status is NodeStatus.FAILED
    assert "retry exhausted" in node.error
    assert result.details["failed"] == ["a"]
    assert store.list_retries(workflow.id) == []


def test_stale_running_agent_is_reclassified(store, create_workflow):
    workflow = _running_workflow(store, create_workflow)
    _claim(store)
    store.increment_attempts("a")
    run = store.create_agent_run(workflow.id, "a", "stub", 1, timeout_seconds=1)
    old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(timespec="seconds")
    with store.db.transaction() as conn:
        conn.execute(
            "UPDATE agent_runs SET started_at = ?, heartbeat_at = ? WHERE id = ?",
            (old, old, run.id),
        )

    _recovery(store).recover_workflow(workflow.id)

    assert store.get_agent_run(run.id).status is AgentRunStatus.INTERRUPTED
    assert store.get_node("a").status is NodeStatus.READY


def test_unbounded_run_with_live_heartbeat_is_not_stale(store, create_workflow):
    workflow = _running_workflow(store, create_workflow)
    _claim(store)
    store.increment_attempts("a")
    run = store.create_agent_run(workflow.id, "a", "stub", 1, timeout_seconds=0)

    result = _recovery(store, grace_seconds=5.0).recover_workflow(workflow.id)

    assert result.action == "active"
    assert store.get_agent_run(run.id).status is AgentRunStatus.RUNNING
    assert store.get_node("a").status is NodeStatus.RUNNING


def test_unbounded_run_with_dead_heartbeat_is_recovered(store, create_workflow):
    workflow = _running_workflow(store, create_workflow)
    _claim(store)
    store.increment_attempts("a")
    run = store.create_agent_run(workflow.id, "a", "stub", 1, timeout_seconds=0)
    old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(timespec="seconds")
    with store.db.transaction() as conn:
        conn.execute(
            "UPDATE agent_runs SET started_at = ?, heartbeat_at = ? WHERE id = ?",
            (old, old, run.id),
        )

    _recovery(store, grace_seconds=5.0).recover_workflow(workflow.id)

    assert store.get_agent_run(run.id).status is AgentRunStatus.INTERRUPTED
    assert store.get_node("a").status is NodeStatus.READY


def test_concurrent_recovery_scans_are_idempotent(store, create_workflow):
    workflow = _running_workflow(store, create_workflow)
    _claim(store)
    store.increment_attempts("a")
    run = store.create_agent_run(workflow.id, "a", "stub", 1, timeout_seconds=30)
    store.update_agent_run(run.id, AgentRunStatus.INTERRUPTED)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: _recovery(store).recover_workflow(workflow.id), range(4)
            )
        )

    assert all(r.action in {"resumed", "skipped", "active"} for r in results)
    assert len(store.list_retries(workflow.id)) == 1


def test_successful_agent_is_verified_instead_of_rerun(store, create_workflow):
    workflow = _running_workflow(store, create_workflow)
    _claim(store)
    store.increment_attempts("a")
    run = store.create_agent_run(workflow.id, "a", "stub", 1, timeout_seconds=30)
    store.update_agent_run(run.id, AgentRunStatus.SUCCEEDED, output="done", exit_code=0)

    result = _recovery(store).recover_workflow(workflow.id)

    node = store.get_node("a")
    assert node.status is NodeStatus.SUCCEEDED
    assert node.output["verification"]["passed"]
    assert store.list_retries(workflow.id) == []
    assert result.details["completed"] == ["a"]


def test_pending_decision_keeps_workflow_waiting(store, create_workflow):
    workflow = _running_workflow(store, create_workflow)
    store.create_decision(workflow.id, "a", "approval", "Approve?")

    result = _recovery(store).recover_workflow(workflow.id)

    assert result.action == "waiting_human"
    assert store.get_workflow(workflow.id).status is WorkflowStatus.WAITING_HUMAN


def test_orphaned_resolved_decision_requeues_node(store, create_workflow):
    workflow = _running_workflow(store, create_workflow)
    store.update_node_status("a", NodeStatus.READY)
    store.claim_node("a")
    store.update_node_status("a", NodeStatus.WAITING_HUMAN)
    decision = store.create_decision(workflow.id, "a", "approval", "Approve?", options=["yes"])
    store.finalize_decision(decision.id, DecisionStatus.RESOLVED, selected_option="yes")

    result = _recovery(store).recover_workflow(workflow.id)

    assert store.get_node("a").status is NodeStatus.READY
    assert result.action == "resumed"


def test_recovery_is_idempotent(store, create_workflow):
    workflow = _running_workflow(store, create_workflow)
    _claim(store)
    store.increment_attempts("a")
    run = store.create_agent_run(workflow.id, "a", "stub", 1, timeout_seconds=30)
    store.update_agent_run(run.id, AgentRunStatus.INTERRUPTED)

    recovery = _recovery(store)
    recovery.recover_workflow(workflow.id)
    recovery.recover_workflow(workflow.id)

    assert len(store.list_retries(workflow.id)) == 1


def test_terminal_workflow_is_skipped(store, create_workflow):
    workflow = create_workflow([{"id": "a"}])
    store.update_workflow_status(workflow.id, WorkflowStatus.CANCELLED)
    assert _recovery(store).recover_workflow(workflow.id).action == "skipped"


def test_scan_covers_all_active_workflows(store, create_workflow):
    first = create_workflow([{"id": "a"}], name="one")
    second = create_workflow([{"id": "b"}], name="two")
    done = create_workflow([{"id": "c"}], name="three")
    store.update_workflow_status(done.id, WorkflowStatus.CANCELLED)
    results = _recovery(store).scan()
    assert {r.workflow_id for r in results} == {first.id, second.id}

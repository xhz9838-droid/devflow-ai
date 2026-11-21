import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from devflow.agent_protocol import plan_change_request_path
from devflow.agents.base import AgentAdapter, AgentResult
from devflow.config import DevFlowConfig
from devflow.engine import WorkflowEngine
from devflow.errors import ValidationError
from devflow.models import NodeStatus, WorkflowStatus
from devflow.planning.models import ChangeRisk, ChangeStatus, PlanStatus
from planning_helpers import make_manager, plan_node, valid_plan


class ChangeAdapter(AgentAdapter):
    name = "change"

    def __init__(self, changes=None):
        self.changes = dict(changes or {})
        self.calls = []

    def execute(self, request):
        self.calls.append(request.node_id)
        payload = self.changes.get(request.node_id)
        if payload is not None:
            path = plan_change_request_path(request.workdir, request.node_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
        return AgentResult(success=True, output=f"ok:{request.node_id}", exit_code=0)


def setup(tmp_path, change=None, *, change_node="implement", changes=None, plan_json=None):
    manager, store, _ = make_manager(tmp_path, [plan_json or valid_plan("change test")])
    plan = manager.create("change test")
    manager.generate(plan.id)
    manager.confirm(plan.id)
    if changes is None:
        changes = {change_node: change} if change is not None else {}
    adapter = ChangeAdapter(changes)
    engine = WorkflowEngine(
        DevFlowConfig(
            db_path=tmp_path / "plan.db",
            workdir=tmp_path,
            max_parallel=2,
            agent_timeout=30,
            recovery_grace=1,
        ),
        adapter,
    )
    return manager, store, engine, plan, adapter


def add_node_change(risk="LOW"):
    return {
        "title": "Add a docs node",
        "risk_level": risk,
        "reason": "small local addition",
        "impact": "low",
        "proposed_changes": {"add_nodes": [plan_node("docs")]},
    }


def test_low_risk_change_is_applied_and_continues(tmp_path):
    _, store, engine, plan, adapter = setup(tmp_path, add_node_change("LOW"))
    workflow = engine.start_workflow_from_plan(plan.id)

    assert workflow.status is WorkflowStatus.COMPLETED
    assert set(adapter.calls) == {"implement", "verify", "docs"}
    assert adapter.calls.count("implement") == 1
    changes = store.list_change_requests(plan.id)
    assert len(changes) == 1
    assert changes[0].status is ChangeStatus.APPROVED
    assert changes[0].new_version == 2
    assert store.get_plan(plan.id).version == 2
    snapshot = store.get_snapshot(plan.id)
    assert snapshot.version == 2
    assert "docs" in {node["id"] for node in snapshot.plan_json["nodes"]}
    assert not plan_change_request_path(tmp_path, "implement").exists()


def test_major_change_waits_for_human_and_rejection_keeps_plan(tmp_path):
    _, store, engine, plan, adapter = setup(tmp_path, add_node_change("MAJOR"))
    workflow = engine.start_workflow_from_plan(plan.id)

    assert workflow.status is WorkflowStatus.WAITING_HUMAN
    pending = engine.decisions.pending(workflow.id)
    assert len(pending) == 1
    assert pending[0].node_id is None
    assert store.list_change_requests(plan.id)[0].status is ChangeStatus.PENDING

    engine.decisions.resolve(pending[0].id, "reject")
    final = engine.resume(workflow.id)
    assert final.status is WorkflowStatus.COMPLETED
    change = store.list_change_requests(plan.id)[0]
    assert change.status is ChangeStatus.REJECTED
    assert store.get_plan(plan.id).version == 1
    assert "docs" not in {n.id for n in engine.store.list_nodes(workflow.id)}
    assert adapter.calls.count("implement") == 1


def test_major_change_approval_applies_new_snapshot(tmp_path):
    _, store, engine, plan, adapter = setup(tmp_path, add_node_change("MAJOR"))
    workflow = engine.start_workflow_from_plan(plan.id)
    pending = engine.decisions.pending(workflow.id)

    engine.decisions.resolve(pending[0].id, "approve")
    final = engine.resume(workflow.id)

    assert final.status is WorkflowStatus.COMPLETED
    change = store.list_change_requests(plan.id)[0]
    assert change.status is ChangeStatus.APPROVED
    assert change.new_version == 2
    assert "docs" in adapter.calls
    assert store.get_plan(plan.id).version == 2
    snapshot = store.get_snapshot(plan.id)
    assert "docs" in {node["id"] for node in snapshot.plan_json["nodes"]}


def test_completed_node_rework_creates_replacement_without_rerunning_others(tmp_path):
    change = {
        "title": "Rework implement",
        "risk_level": "MAJOR",
        "reason": "API contract changed",
        "impact": "needs rework",
        "proposed_changes": {
            "affected_nodes": ["implement"],
            "update_nodes": [
                {
                    "id": "implement",
                    "execution_config": {"prompt": "reimplement with the new contract"},
                }
            ],
        },
    }
    _, store, engine, plan, adapter = setup(tmp_path, change)
    workflow = engine.start_workflow_from_plan(plan.id)
    pending = engine.decisions.pending(workflow.id)
    assert engine.store.get_node("implement").status is NodeStatus.SUCCEEDED
    assert engine.store.get_node("verify").status is NodeStatus.SUCCEEDED

    engine.decisions.resolve(pending[0].id, "approve")
    final = engine.resume(workflow.id)

    assert final.status is WorkflowStatus.COMPLETED
    assert adapter.calls.count("implement") == 1
    assert adapter.calls.count("verify") == 1
    assert adapter.calls.count("implement-rev2") == 1
    assert engine.store.get_node("implement-rev2").status is NodeStatus.SUCCEEDED
    snapshot = store.get_snapshot(plan.id)
    ids = {node["id"] for node in snapshot.plan_json["nodes"]}
    assert "implement-rev2" in ids
    assert "implement" not in ids
    verify_plan_node = next(
        node for node in snapshot.plan_json["nodes"] if node["id"] == "verify"
    )
    assert verify_plan_node["dependencies"] == ["implement-rev2"]


def test_invalid_change_request_fails_the_node(tmp_path):
    manager, store, _ = make_manager(tmp_path, [valid_plan("bad change")])
    plan = manager.create("bad change")
    manager.generate(plan.id)
    manager.confirm(plan.id)

    class BadAdapter(AgentAdapter):
        name = "bad"

        def execute(self, request):
            path = plan_change_request_path(request.workdir, request.node_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{not json", encoding="utf-8")
            return AgentResult(success=True, output="ok", exit_code=0)

    engine = WorkflowEngine(
        DevFlowConfig(
            db_path=tmp_path / "plan.db",
            workdir=tmp_path,
            max_parallel=1,
            agent_timeout=30,
            recovery_grace=1,
        ),
        BadAdapter(),
    )
    workflow = engine.start_workflow_from_plan(plan.id)
    assert workflow.status is WorkflowStatus.FAILED
    assert engine.store.get_node("implement").status is NodeStatus.FAILED


def test_declared_low_update_to_completed_node_becomes_major_and_replaces(tmp_path):
    change = {
        "title": "Different implementation",
        "risk_level": "LOW",
        "reason": "small local tweak",
        "impact": "low",
        "proposed_changes": {
            "update_nodes": [
                {"id": "implement", "execution_config": {"prompt": "totally different"}}
            ]
        },
    }
    _, store, engine, plan, adapter = setup(
        tmp_path, change=change, change_node="verify"
    )
    workflow = engine.start_workflow_from_plan(plan.id)

    change_row = store.list_change_requests(plan.id)[0]
    assert change_row.risk_level is ChangeRisk.MAJOR
    assert change_row.status is ChangeStatus.PENDING
    pending = engine.decisions.pending(workflow.id)
    assert len(pending) == 1

    engine.decisions.resolve(pending[0].id, "approve")
    final = engine.resume(workflow.id)

    assert final.status is WorkflowStatus.COMPLETED
    assert change_row.id
    assert store.list_change_requests(plan.id)[0].status is ChangeStatus.APPROVED
    assert adapter.calls.count("implement") == 1
    assert adapter.calls.count("verify") == 1
    assert adapter.calls.count("implement-rev2") == 1
    snapshot = store.get_snapshot(plan.id)
    assert "implement-rev2" in {node["id"] for node in snapshot.plan_json["nodes"]}


def test_parallel_low_risk_changes_do_not_lose_updates(tmp_path):
    plan_json = valid_plan("parallel changes")
    plan_json["nodes"] = [plan_node("a"), plan_node("b")]
    plan_json["acceptance_criteria"] = [{"type": "exit_code", "equals": 0}]
    changes = {
        "a": {
            "title": "Add docs A",
            "risk_level": "LOW",
            "reason": "small addition",
            "impact": "low",
            "proposed_changes": {"add_nodes": [plan_node("docsA")]},
        },
        "b": {
            "title": "Add docs B",
            "risk_level": "LOW",
            "reason": "small addition",
            "impact": "low",
            "proposed_changes": {"add_nodes": [plan_node("docsB")]},
        },
    }
    _, store, engine, plan, adapter = setup(
        tmp_path, changes=changes, plan_json=plan_json
    )
    workflow = engine.start_workflow_from_plan(plan.id)

    assert workflow.status is WorkflowStatus.COMPLETED
    assert store.get_plan(plan.id).version == 3
    assert {item.status for item in store.list_change_requests(plan.id)} == {
        ChangeStatus.APPROVED
    }
    snapshot = store.get_snapshot(plan.id)
    ids = {node["id"] for node in snapshot.plan_json["nodes"]}
    assert {"docsA", "docsB"} <= ids
    assert {"docsA", "docsB"} <= set(adapter.calls)
    assert engine.store.get_workflow(workflow.id).plan_version == 3


def test_apply_to_terminal_workflow_is_atomic(tmp_path):
    manager, store, engine, plan, _ = setup(
        tmp_path,
        changes={
            "implement": {
                "title": "Add docs",
                "risk_level": "LOW",
                "reason": "small",
                "impact": "low",
                "proposed_changes": {"add_nodes": [plan_node("docs")]},
            }
        },
    )
    workflow = engine.create_workflow_from_plan(plan.id)
    engine.cancel(workflow.id, "stop")
    change = store.create_change_request(
        plan_id=plan.id,
        workflow_id=workflow.id,
        node_id="implement",
        risk_level="LOW",
        title="Add docs",
        reason="small",
        impact="low",
        proposed_changes={"add_nodes": [plan_node("docs")]},
    )
    with pytest.raises(ValidationError):
        engine.plan_changes._apply(change, resolved_by="test")
    assert store.get_plan(plan.id).version == 1
    assert len(store.list_snapshots(plan.id)) == 1
    assert "docs" not in {node.id for node in engine.store.list_nodes(workflow.id)}


def test_concurrent_plan_workflow_creation_returns_one_workflow(tmp_path):
    manager, store, engine, plan, _ = setup(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(engine.create_workflow_from_plan, plan.id)
            for _ in range(2)
        ]
        workflows = [future.result(timeout=20) for future in futures]
    assert workflows[0].id == workflows[1].id
    assert engine.store.list_nodes(workflows[0].id)
    assert store.get_plan(plan.id).workflow_id == workflows[0].id


def test_readded_cancelled_node_is_replaced_and_does_not_block(tmp_path):
    plan_json = valid_plan("readd cancelled node")
    plan_json["nodes"] = [
        plan_node("implement"),
        plan_node("verify", dependencies=["implement"]),
        plan_node("docs"),
    ]
    plan_json["acceptance_criteria"] = [{"type": "exit_code", "equals": 0}]
    _, store, engine, plan, adapter = setup(
        tmp_path, changes={}, plan_json=plan_json
    )
    workflow = engine.create_workflow_from_plan(plan.id)
    engine.store.update_node_status("docs", NodeStatus.CANCELLED, "removed earlier")
    change = {
        "title": "Re-add docs",
        "risk_level": "LOW",
        "reason": "small re-add",
        "impact": "low",
        "proposed_changes": {"add_nodes": [plan_node("docs")]},
    }
    row = engine.plan_changes.handle_request(workflow.id, "implement", change)
    assert row.risk_level is ChangeRisk.MAJOR
    pending = engine.decisions.pending(workflow.id)
    assert len(pending) == 1

    engine.decisions.resolve(pending[0].id, "approve")
    engine.plan_changes.apply_approved(workflow.id)

    assert engine.store.get_node("docs").required is False
    assert engine.store.get_node("docs-rev2").status is NodeStatus.PENDING
    snapshot = store.get_snapshot(plan.id)
    assert "docs-rev2" in {node["id"] for node in snapshot.plan_json["nodes"]}

    final = engine.start(workflow.id)
    assert final.status is WorkflowStatus.COMPLETED
    assert adapter.calls.count("docs-rev2") == 1
    assert "docs" not in adapter.calls

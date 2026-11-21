from devflow.agents.base import AgentAdapter, AgentResult
from devflow.config import DevFlowConfig
from devflow.engine import WorkflowEngine
from devflow.models import NodeStatus, WorkflowStatus
from devflow.planning.models import PlanStatus
from planning_helpers import make_manager, valid_plan


class CountingAdapter(AgentAdapter):
    name = "counting"

    def __init__(self):
        self.calls = []

    def execute(self, request):
        self.calls.append(request.node_id)
        return AgentResult(success=True, output=f"ok:{request.node_id}", exit_code=0)


def test_confirmed_plan_starts_the_existing_workflow_core(tmp_path):
    manager, store, _ = make_manager(tmp_path, [valid_plan("build a feature")])
    plan = manager.create("build a feature")
    manager.generate(plan.id)
    snapshot = manager.confirm(plan.id)

    adapter = CountingAdapter()
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
    workflow = engine.start_workflow_from_plan(plan.id)

    assert workflow.status is WorkflowStatus.COMPLETED
    assert adapter.calls == ["implement", "verify"]
    assert workflow.plan_id == plan.id
    assert workflow.plan_snapshot_id == snapshot.id
    assert workflow.plan_version == snapshot.version
    bound = store.get_plan(plan.id)
    assert bound.status is PlanStatus.EXECUTION_READY
    assert bound.workflow_id == workflow.id
    for node in engine.store.list_nodes(workflow.id):
        assert node.status is NodeStatus.SUCCEEDED
        assert node.output["verification"]["passed"] is True

    again = engine.create_workflow_from_plan(plan.id)
    assert again.id == workflow.id
    inspect = engine.inspect(workflow.id)
    assert inspect["workflow"]["plan_snapshot_id"] == snapshot.id
    assert inspect["plan_changes"] == []


def test_snapshot_bound_workflow_uses_plan_acceptance(tmp_path):
    plan_json = valid_plan()
    plan_json["nodes"][0]["acceptance_criteria"] = [
        {"type": "output_contains", "value": "ok:implement"}
    ]
    plan_json["acceptance_criteria"] = [
        {"type": "output_contains", "value": "ok:implement"}
    ]
    manager, store, _ = make_manager(tmp_path, [plan_json])
    plan = manager.create("build")
    manager.generate(plan.id)
    manager.confirm(plan.id)

    engine = WorkflowEngine(
        DevFlowConfig(
            db_path=tmp_path / "plan.db",
            workdir=tmp_path,
            max_parallel=1,
            agent_timeout=30,
            recovery_grace=1,
        ),
        CountingAdapter(),
    )
    workflow = engine.start_workflow_from_plan(plan.id)
    assert workflow.status is WorkflowStatus.COMPLETED
    node = engine.store.get_node("implement")
    assert node.output["verification"]["evidence"][0]["condition"] == "output_contains"

import json
from pathlib import Path

import pytest

import devflow
from devflow.agents.base import AgentResult
from devflow.errors import AgentInterrupted, DecisionError, DAGValidationError, ValidationError
from devflow.models import (
    AgentRunStatus,
    DecisionStatus,
    NodeStatus,
    WorkflowStatus,
)

NODES = [
    {"id": "n1", "name": "analyze", "idempotency_key": "analysis-done"},
    {
        "id": "n2",
        "name": "implement",
        "dependencies": ["n1"],
        "retry_policy": {"max_retries": 2, "base_delay_seconds": 0},
    },
    {"id": "n3", "name": "review", "dependencies": ["n2"]},
    {
        "id": "n4",
        "name": "approval",
        "execution_config": {
            "human_decision": {
                "question": "Approve the implementation?",
                "options": [
                    {"id": "approve", "label": "Approve"},
                    {"id": "reject", "label": "Reject"},
                ],
            }
        },
    },
    {"id": "n5", "name": "docs"},
    {"id": "n6", "name": "finalize", "dependencies": ["n3", "n4", "n5"]},
]


def test_p0_end_to_end_with_interruption_recovery_and_hitl(adapter, make_engine):
    def interrupt_first_attempt(request, count):
        if count == 1:
            raise AgentInterrupted("simulated agent process loss")
        return AgentResult(success=True, output="implemented", exit_code=0)

    adapter.handlers["n2"] = interrupt_first_attempt
    engine = make_engine(adapter)
    workflow = engine.create_workflow(
        "P0 end-to-end", "Ship the sample feature", NODES, context={"ticket": "DF-P0"}
    )

    # 1. Start: independent work continues while the human node waits.
    result = engine.start(workflow.id)
    assert result.status is WorkflowStatus.WAITING_HUMAN
    assert engine.store.get_node("n1").status is NodeStatus.SUCCEEDED
    assert engine.store.get_node("n5").status is NodeStatus.SUCCEEDED
    assert engine.store.get_node("n4").status is NodeStatus.WAITING_HUMAN
    assert adapter.call_count("n1") == 1
    assert adapter.call_count("n2") == 1

    # 2. Interruption was classified and handed to Recovery, not treated as failure.
    assert engine.store.get_node("n2").status is NodeStatus.READY
    runs_n2 = engine.store.list_agent_runs(node_id="n2")
    assert runs_n2[0].status is AgentRunStatus.INTERRUPTED
    retries = engine.store.list_retries(workflow.id)
    assert len(retries) == 1 and retries[0].attempt == 2

    pending = engine.decisions.pending(workflow.id)
    assert len(pending) == 1
    assert pending[0].decision_type == "approval"

    # 3. Human decision arrives; the same workflow resumes.
    resolved = engine.decisions.resolve(
        pending[0].id, "approve", {"reviewer": "qa"}, resolved_by="test"
    )
    assert resolved.status is DecisionStatus.RESOLVED
    assert engine.store.get_workflow(workflow.id).status is WorkflowStatus.RUNNING

    final = engine.resume(workflow.id)
    assert final.status is WorkflowStatus.COMPLETED

    # 4. Nothing executed twice.
    expected_calls = {"n1": 1, "n2": 2, "n3": 1, "n4": 1, "n5": 1, "n6": 1}
    assert {node: adapter.call_count(node) for node in expected_calls} == expected_calls
    for node in engine.store.list_nodes(workflow.id):
        assert node.status is NodeStatus.SUCCEEDED
        assert node.output["verification"]["passed"] is True
        assert node.error is None

    # 5. Agent run history shows interrupted then succeeded.
    run_statuses = {r.status for r in engine.store.list_agent_runs(node_id="n2")}
    assert run_statuses == {AgentRunStatus.INTERRUPTED, AgentRunStatus.SUCCEEDED}

    # 6. Full audit trail and checkpoints exist; DB is the authority.
    event_types = {e.event_type for e in engine.store.list_events(workflow.id)}
    assert {
        "node.interrupted",
        "node.retry_scheduled",
        "human_decision.requested",
        "human_decision.resolved",
        "workflow.recovery_started",
        "workflow.recovered",
        "workflow.completed",
        "checkpoint.saved",
    } <= event_types
    checkpoints = engine.store.list_checkpoints(workflow.id)
    assert len(checkpoints) >= 5
    latest = engine.checkpoints.latest(workflow.id)
    assert latest.context["progress"]["completed"] == 6
    assert {"node_id": "n1", "idempotency_key": "analysis-done"} in latest.context[
        "side_effects_completed"
    ]
    assert latest.state_snapshot["workflow"]["status"] == "COMPLETED"

    inspect_payload = engine.inspect(workflow.id)
    json.dumps(inspect_payload, default=str)  # must be serializable for the CLI

    # 7. No external provider is involved.
    package_text = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in Path(devflow.__file__).parent.rglob("*.py")
    )
    for forbidden in ("zentao", "dingtalk", "jenkins"):
        assert forbidden not in package_text

    # 8. A second resume of a completed workflow is a no-op.
    assert engine.resume(workflow.id).status is WorkflowStatus.COMPLETED
    assert adapter.call_count("n6") == 1


def test_empty_workflow_is_rejected(make_engine):
    engine = make_engine()
    with pytest.raises(ValidationError):
        engine.create_workflow("empty", "nothing", [])


def test_single_node_completes_without_any_provider(make_engine):
    engine = make_engine()
    workflow = engine.create_workflow("one", "goal", [{"id": "only"}])
    assert engine.start(workflow.id).status is WorkflowStatus.COMPLETED
    assert engine.store.get_node("only").status is NodeStatus.SUCCEEDED


def test_agent_failure_exhausts_retries_and_fails_workflow(make_engine):
    engine = make_engine()
    workflow = engine.create_workflow(
        "fail", "goal", [{"id": "boom", "retry_policy": {"max_retries": 0}}]
    )
    engine.adapter.handlers["boom"] = lambda request, count: AgentResult(
        success=False, output="", exit_code=1, error="compiler error"
    )
    result = engine.start(workflow.id)
    assert result.status is WorkflowStatus.FAILED
    assert "compiler error" in engine.store.get_node("boom").error


def test_cancel_is_terminal_and_cancels_nodes(make_engine):
    engine = make_engine()
    workflow = engine.create_workflow("cancel", "goal", [{"id": "a"}, {"id": "b"}])
    cancelled = engine.cancel(workflow.id, "user changed mind")
    assert cancelled.status is WorkflowStatus.CANCELLED
    assert {n.status for n in engine.store.list_nodes(workflow.id)} == {NodeStatus.CANCELLED}
    with pytest.raises(ValidationError):
        engine.start(workflow.id)


def test_custom_prompt_still_receives_continuation_context(adapter, make_engine):
    engine = make_engine(adapter)
    workflow = engine.create_workflow(
        "handoff",
        "pass a token",
        [
            {"id": "first"},
            {
                "id": "second",
                "dependencies": ["first"],
                "execution_config": {"prompt": "custom prompt without context"},
            },
        ],
    )
    result = engine.start(workflow.id)
    assert result.status is WorkflowStatus.COMPLETED
    second = next(call for call in adapter.calls if call.node_id == "second")
    assert second.prompt.startswith("custom prompt without context")
    assert "Continuation context" in second.prompt
    assert "ok:first" in second.prompt


def test_invalid_human_decision_spec_fails_instead_of_looping(make_engine):
    engine = make_engine()
    workflow = engine.create_workflow(
        "bad gate",
        "goal",
        [{"id": "gate", "execution_config": {"human_decision": {"options": ["yes"]}}}],
    )
    result = engine.start(workflow.id)
    assert result.status is WorkflowStatus.FAILED
    assert "question" in engine.store.get_node("gate").error


def test_invalid_retry_policy_is_rejected_before_creation(make_engine):
    engine = make_engine()
    with pytest.raises(DAGValidationError):
        engine.create_workflow(
            "bad policy", "goal", [{"id": "a", "retry_policy": {"max_retries": "two"}}]
        )
    with pytest.raises(DAGValidationError):
        engine.create_workflow(
            "bad policy", "goal", [{"id": "a", "retry_policy": ["not", "an", "object"]}]
        )
    assert engine.store.list_workflows() == []


def test_failed_workflow_closes_pending_decisions_and_nodes(make_engine):
    engine = make_engine()
    adapter = engine.adapter
    adapter.handlers["boom"] = lambda request, count: AgentResult(
        success=False, output="", exit_code=1, error="failed"
    )
    workflow = engine.create_workflow(
        "fail and wait",
        "goal",
        [
            {"id": "boom", "retry_policy": {"max_retries": 0}},
            {
                "id": "gate",
                "execution_config": {
                    "human_decision": {
                        "question": "Go?",
                        "options": [{"id": "approve", "label": "Approve"}],
                    }
                },
            },
        ],
    )

    result = engine.start(workflow.id)

    assert result.status is WorkflowStatus.FAILED
    assert engine.decisions.pending(workflow.id) == []
    decision = engine.decisions.list(workflow.id)[0]
    assert decision.status is DecisionStatus.CANCELLED
    assert engine.store.get_node("gate").status is NodeStatus.CANCELLED
    with pytest.raises(DecisionError):
        engine.decisions.resolve(decision.id, "approve")


def test_long_chain_scales_the_iteration_limit(make_engine):
    engine = make_engine()
    nodes = [{"id": "n0"}]
    for index in range(1, 110):
        nodes.append({"id": f"n{index}", "dependencies": [f"n{index - 1}"]})
    workflow = engine.create_workflow("long chain", "goal", nodes)
    assert engine.start(workflow.id).status is WorkflowStatus.COMPLETED
    assert all(n.status is NodeStatus.SUCCEEDED for n in engine.store.list_nodes(workflow.id))


def test_human_node_waits_while_independent_node_completes(make_engine):
    engine = make_engine()
    workflow = engine.create_workflow(
        "wait",
        "goal",
        [
            {"id": "work"},
            {
                "id": "gate",
                "execution_config": {"human_decision": {"question": "Go?"}},
            },
        ],
    )
    result = engine.start(workflow.id)
    assert result.status is WorkflowStatus.WAITING_HUMAN
    assert engine.store.get_node("work").status is NodeStatus.SUCCEEDED
    decision = engine.decisions.pending(workflow.id)[0]
    engine.decisions.resolve(decision.id)
    assert engine.resume(workflow.id).status is WorkflowStatus.COMPLETED

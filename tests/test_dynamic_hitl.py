import json

from devflow.agent_protocol import decision_request_path
from devflow.agents.base import AgentResult
from devflow.models import DecisionStatus, NodeStatus, WorkflowStatus


def write_request(request, payload):
    path = decision_request_path(request.workdir, request.node_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return AgentResult(success=True, output="decision requested", exit_code=0)


def test_agent_dynamic_decision_parks_node_and_resumes(adapter, make_engine, tmp_path):
    def handler(request, count):
        if count > 1:
            return AgentResult(success=True, output="continued", exit_code=0)
        return write_request(
            request,
            {
                "question": "Approve the next step?",
                "decision_type": "approval",
                "reason": "The agent needs a human choice",
                "context": {"detail": "dynamic"},
                "options": [
                    {"id": "approve", "label": "Approve"},
                    {"id": "reject", "label": "Reject"},
                ],
                "recommended": "approve",
                "risk": "low",
            },
        )

    adapter.handlers["dynamic"] = handler
    engine = make_engine(adapter)
    workflow = engine.create_workflow(
        "dynamic hitl",
        "ask a human mid-run",
        [
            {"id": "dynamic"},
            {"id": "independent"},
            {"id": "dependent", "dependencies": ["dynamic"]},
        ],
    )

    result = engine.start(workflow.id)
    assert result.status is WorkflowStatus.WAITING_HUMAN
    assert engine.store.get_node("dynamic").status is NodeStatus.WAITING_HUMAN
    assert engine.store.get_node("independent").status is NodeStatus.SUCCEEDED
    assert engine.store.get_node("dependent").status is NodeStatus.BLOCKED
    assert adapter.call_count("independent") == 1

    decision = engine.decisions.pending(workflow.id)[0]
    assert decision.question == "Approve the next step?"
    assert decision.context["reason"] == "The agent needs a human choice"
    assert decision.context["recommended"] == "approve"
    assert decision.context["risk"] == "low"
    assert [o["id"] for o in decision.options] == ["approve", "reject"]
    assert "human_decision.agent_requested" in [
        e.event_type for e in engine.store.list_events(workflow.id)
    ]
    archived = list((tmp_path / ".devflow" / "decisions" / "archive").glob("*.json"))
    assert len(archived) == 1

    engine.decisions.resolve(decision.id, "approve")
    final = engine.resume(workflow.id)

    assert final.status is WorkflowStatus.COMPLETED
    assert engine.store.get_node("dynamic").status is NodeStatus.SUCCEEDED
    assert engine.store.get_node("dependent").status is NodeStatus.SUCCEEDED
    assert adapter.call_count("dynamic") == 2
    assert adapter.call_count("independent") == 1
    second = [c for c in adapter.calls if c.node_id == "dynamic"][1]
    assert '"approved_decision"' in second.prompt
    assert '"selected_option": "approve"' in second.prompt


def test_invalid_decision_request_fails_the_node(adapter, make_engine, tmp_path):
    def handler(request, count):
        path = decision_request_path(request.workdir, request.node_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        return AgentResult(success=True, output="bad", exit_code=0)

    adapter.handlers["bad"] = handler
    engine = make_engine(adapter)
    workflow = engine.create_workflow("bad request", "goal", [{"id": "bad"}])
    result = engine.start(workflow.id)

    assert result.status is WorkflowStatus.FAILED
    node = engine.store.get_node("bad")
    assert node.status is NodeStatus.FAILED
    assert "invalid decision request" in node.error


def test_decision_request_without_question_fails_the_node(adapter, make_engine):
    adapter.handlers["bad"] = lambda request, count: write_request(
        request, {"reason": "missing question"}
    )
    engine = make_engine(adapter)
    workflow = engine.create_workflow("no question", "goal", [{"id": "bad"}])
    assert engine.start(workflow.id).status is WorkflowStatus.FAILED
    assert "question" in engine.store.get_node("bad").error


def test_decision_request_path_is_sanitized(tmp_path):
    path = decision_request_path(tmp_path, "../evil/node")
    assert path.parent.name == "decisions"
    assert ".." not in path.name
    assert "/" not in path.name

from types import SimpleNamespace

from devflow.models import Node, NodeStatus, Workflow, WorkflowStatus
from devflow.verifier import CompletionVerifier, DefaultVerifier


def _node(
    node_id="n",
    status=NodeStatus.SUCCEEDED,
    *,
    required=True,
    verification=True,
    config=None,
):
    output = {"verification": {"passed": True}} if verification else None
    return Node(
        id=node_id,
        workflow_id="w",
        name=node_id,
        status=status,
        required=required,
        output=output,
        execution_config=config or {},
    )


def _workflow(status=WorkflowStatus.RUNNING):
    return Workflow(id="w", name="w", goal="goal", status=status)


def _result(success=True, output="hello world", exit_code=0):
    return SimpleNamespace(success=success, output=output, exit_code=exit_code, error=None)


def test_default_verifier_uses_agent_success_without_conditions():
    verifier = DefaultVerifier()
    assert verifier.verify(_workflow(), _node(), _result(True)).passed
    report = verifier.verify(_workflow(), _node(), _result(False, "", 1))
    assert not report.passed
    assert report.failed_conditions


def test_default_verifier_evaluates_acceptance_conditions(tmp_path):
    artifact = tmp_path / "report.md"
    artifact.write_text("done", encoding="utf-8")
    node = _node(
        config={
            "acceptance": [
                {"type": "exit_code", "equals": 0},
                {"type": "output_contains", "value": "hello"},
                {"type": "output_not_contains", "value": "error"},
                {"type": "artifact_exists", "path": "report.md"},
            ]
        }
    )
    report = DefaultVerifier().verify(_workflow(), node, _result(), {"workdir": str(tmp_path)})
    assert report.passed
    assert len(report.evidence) == 4


def test_default_verifier_reports_failed_conditions(tmp_path):
    node = _node(
        config={
            "acceptance": [
                {"type": "output_contains", "value": "missing"},
                {"type": "artifact_exists", "path": "nope.md"},
            ]
        }
    )
    report = DefaultVerifier().verify(_workflow(), node, _result(), {"workdir": str(tmp_path)})
    assert not report.passed
    assert len(report.failed_conditions) == 2


def test_artifact_contains_checks_file_content(tmp_path):
    (tmp_path / "artifact.txt").write_text("token-DF-012-OK", encoding="utf-8")
    passing = _node(
        config={
            "acceptance": [
                {"type": "artifact_contains", "path": "artifact.txt", "value": "DF-012-OK"}
            ]
        }
    )
    failing = _node(
        config={
            "acceptance": [
                {"type": "artifact_contains", "path": "artifact.txt", "value": "missing"}
            ]
        }
    )
    assert DefaultVerifier().verify(_workflow(), passing, _result(), {"workdir": str(tmp_path)}).passed
    assert not DefaultVerifier().verify(_workflow(), failing, _result(), {"workdir": str(tmp_path)}).passed


def test_default_verifier_rejects_invalid_condition():
    node = _node(config={"acceptance": [{"type": "telepathy"}]})
    report = DefaultVerifier().verify(_workflow(), node, _result())
    assert not report.passed


def test_completion_requires_all_required_nodes_verified():
    verifier = CompletionVerifier()
    nodes = [_node("a"), _node("b", required=False, status=NodeStatus.FAILED, verification=False)]
    assert verifier.evaluate(_workflow(), nodes, 0).passed


def test_completion_fails_with_pending_decision():
    report = CompletionVerifier().evaluate(_workflow(WorkflowStatus.WAITING_HUMAN), [_node("a")], 1)
    assert not report.passed
    assert any("human decision" in reason for reason in report.failed_conditions)


def test_completion_fails_without_verification_or_open_nodes():
    missing = CompletionVerifier().evaluate(_workflow(), [_node("a", verification=False)], 0)
    running = CompletionVerifier().evaluate(_workflow(), [_node("a", status=NodeStatus.RUNNING)], 0)
    failed = CompletionVerifier().evaluate(
        _workflow(), [_node("a", status=NodeStatus.FAILED, verification=False)], 0
    )
    assert not missing.passed
    assert not running.passed
    assert not failed.passed

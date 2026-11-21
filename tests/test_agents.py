import pytest

from devflow.agents.base import AdapterRegistry, AgentAdapter, AgentRequest, AgentResult, Capability
from devflow.agents.claude_code import ClaudeCodeAdapter
from devflow.agents.manager import AgentExecutionManager
from devflow.errors import AgentExecutionError, AgentInterrupted, AgentTimeout
from devflow.models import AgentRunStatus, NodeStatus


class StubAdapter(AgentAdapter):
    name = "stub"

    def __init__(self, behavior):
        self.behavior = behavior
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return self.behavior(request)


def running_node(store, create_workflow):
    workflow = create_workflow([{"id": "n"}])
    store.update_workflow_status(workflow.id, "INITIALIZING")
    store.update_workflow_status(workflow.id, "RUNNING")
    store.update_node_status("n", NodeStatus.READY)
    node = store.claim_node("n")
    return workflow, node


@pytest.mark.parametrize(
    "behavior,expected",
    [
        (lambda r: AgentResult(success=True, output="ok", exit_code=0), AgentRunStatus.SUCCEEDED),
        (lambda r: AgentResult(success=False, output="", exit_code=2, error="bad"), AgentRunStatus.FAILED),
        (lambda r: (_ for _ in ()).throw(AgentTimeout("slow")), AgentRunStatus.TIMEOUT),
        (lambda r: (_ for _ in ()).throw(AgentInterrupted("gone")), AgentRunStatus.INTERRUPTED),
        (lambda r: (_ for _ in ()).throw(RuntimeError("boom")), AgentRunStatus.FAILED),
    ],
)
def test_manager_classifies_runs(store, create_workflow, tmp_path, behavior, expected):
    workflow, node = running_node(store, create_workflow)
    manager = AgentExecutionManager(store, StubAdapter(behavior))
    run = manager.execute(
        workflow.id, node, "prompt", 1, workdir=str(tmp_path), timeout_seconds=5
    )
    assert run.status is expected
    assert store.get_agent_run(run.id).status is expected


def test_manager_persists_output_and_request_context(store, create_workflow, tmp_path):
    workflow, node = running_node(store, create_workflow)
    adapter = StubAdapter(lambda r: AgentResult(success=True, output="result", exit_code=0))
    manager = AgentExecutionManager(store, adapter)
    run = manager.execute(
        workflow.id,
        node,
        "do work",
        3,
        workdir=str(tmp_path),
        timeout_seconds=5,
        context={"goal": "x"},
    )
    assert run.output == "result"
    assert run.exit_code == 0
    request = adapter.requests[0]
    assert request.prompt == "do work"
    assert request.context == {"goal": "x"}
    assert request.agent_run_id == run.id


def test_claude_adapter_builds_safe_command(monkeypatch, tmp_path):
    import devflow.agents.claude_code as module

    captured = {}

    class Proc:
        returncode = 0
        stdout = '{"session_id": "s1", "result": "ok"}'
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return Proc()

    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    request = AgentRequest(
        workflow_id="w",
        node_id="n",
        agent_run_id="r",
        prompt="fix it",
        workdir=str(tmp_path),
        timeout_seconds=5,
        metadata={"env": {"FOO": "bar"}},
    )
    result = ClaudeCodeAdapter().execute(request)

    assert result.success
    assert result.session_id == "s1"
    assert captured["command"][:2] == ["/usr/bin/claude", "-p"]
    assert "fix it" in captured["command"]
    assert captured["shell"] is False
    assert captured["cwd"] == str(tmp_path)
    assert captured["env"]["FOO"] == "bar"
    assert captured["timeout"] == 5


def test_claude_adapter_reports_missing_executable(monkeypatch, tmp_path):
    import devflow.agents.claude_code as module

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    request = AgentRequest(
        workflow_id="w",
        node_id="n",
        agent_run_id="r",
        prompt="x",
        workdir=str(tmp_path),
        timeout_seconds=5,
    )
    with pytest.raises(AgentExecutionError, match="not found"):
        ClaudeCodeAdapter().execute(request)


def test_registry_and_capabilities():
    adapter = StubAdapter(lambda r: AgentResult(success=True))
    registry = AdapterRegistry()
    registry.register(adapter)
    assert registry.get("stub") is adapter
    assert registry.names() == ["stub"]
    assert not adapter.supports(Capability.START)

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from devflow.agents.base import AgentAdapter, AgentRequest, AgentResult
from devflow.agents.claude_code import ClaudeCodeAdapter
from devflow.agents.manager import AgentExecutionManager
from devflow.config import DevFlowConfig
from devflow.engine import WorkflowEngine
from devflow.errors import AgentTimeout
from devflow.models import AgentRunStatus, NodeStatus, WorkflowStatus
from devflow.platform_proc import (
    process_alive,
    process_identity,
    terminate_process_tree,
)
from devflow.recovery import RecoveryManager
from devflow.scheduler import DAGScheduler
from devflow.verifier import DefaultVerifier


def new_group_kwargs():
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def wait_until(predicate, timeout=10.0, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def fake_claude(tmp_path):
    script = tmp_path / "fake_claude.py"
    pid_file = tmp_path / "fake_claude.pid"
    script.write_text(
        "import os, pathlib, time\n"
        "pathlib.Path(os.environ['FAKE_PID_FILE']).write_text(str(os.getpid()), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        executable = tmp_path / "fake_claude.cmd"
        executable.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}"\r\n', encoding="utf-8"
        )
    else:
        executable = tmp_path / "fake_claude.sh"
        executable.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}"\n', encoding="utf-8"
        )
        executable.chmod(0o755)
    return executable, pid_file


def test_current_process_identity_and_reuse_detection():
    pid = os.getpid()
    identity = process_identity(pid)
    assert identity
    assert process_alive(pid, identity)
    assert not process_alive(pid, "not-the-real-identity")


def test_terminate_process_tree_kills_children(tmp_path):
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import subprocess, sys, time, pathlib\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(p.pid), encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen([sys.executable, "-c", script], **new_group_kwargs())
    identity = process_identity(process.pid)
    try:
        assert wait_until(child_pid_file.exists)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        assert process_alive(process.pid, identity)

        assert terminate_process_tree(process.pid, identity, force=True)
        process.wait(timeout=10)
        assert wait_until(lambda: not process_alive(child_pid))
    finally:
        if process.poll() is None:
            process.kill()


def test_terminate_is_idempotent():
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], **new_group_kwargs()
    )
    identity = process_identity(process.pid)
    try:
        assert terminate_process_tree(process.pid, identity, force=True)
        process.wait(timeout=10)
        assert terminate_process_tree(process.pid, identity, force=True) is False
    finally:
        if process.poll() is None:
            process.kill()


def test_claude_adapter_persists_pid_and_kills_on_timeout(monkeypatch, tmp_path):
    import devflow.agents.claude_code as module

    executable, pid_file = fake_claude(tmp_path)
    monkeypatch.setattr(module.shutil, "which", lambda name: str(executable))
    started = []
    request = AgentRequest(
        workflow_id="w",
        node_id="n",
        agent_run_id="r",
        prompt="sleep",
        workdir=str(tmp_path),
        timeout_seconds=1,
        metadata={"env": {"FAKE_PID_FILE": str(pid_file)}},
        on_process_started=lambda pid, identity: started.append((pid, identity)),
    )
    adapter = ClaudeCodeAdapter(executable=str(executable))

    with pytest.raises(AgentTimeout):
        adapter.execute(request)

    assert started and started[0][1]
    assert wait_until(pid_file.exists)
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    assert wait_until(lambda: not process_alive(child_pid))


class CallbackAdapter(AgentAdapter):
    name = "callback"

    def execute(self, request):
        request.on_process_started(4242, "test:4242")
        return AgentResult(success=True)


class NoopAdapter(AgentAdapter):
    name = "noop"

    def execute(self, request):
        return AgentResult(success=True)


def running_node(store, create_workflow):
    workflow = create_workflow([{"id": "a"}])
    store.update_workflow_status(workflow.id, WorkflowStatus.INITIALIZING)
    store.update_workflow_status(workflow.id, WorkflowStatus.RUNNING)
    store.update_node_status("a", NodeStatus.READY)
    node = store.claim_node("a")
    return workflow, node


def test_manager_persists_process_identity(store, create_workflow, tmp_path):
    workflow, node = running_node(store, create_workflow)
    manager = AgentExecutionManager(store, CallbackAdapter())
    run = manager.execute(
        workflow.id, node, "prompt", 1, workdir=str(tmp_path), timeout_seconds=10
    )
    stored = store.get_agent_run(run.id)
    assert stored.pid == 4242
    assert stored.process_start_time == "test:4242"


def test_manager_terminate_is_identity_checked_and_idempotent(store, create_workflow):
    workflow, node = running_node(store, create_workflow)
    run = store.create_agent_run(workflow.id, "a", "noop", 1, timeout_seconds=60)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], **new_group_kwargs()
    )
    identity = process_identity(process.pid)
    store.set_agent_run_process(run.id, process.pid, identity)
    manager = AgentExecutionManager(store, NoopAdapter())
    try:
        assert manager.terminate(workflow.id, force=True) == [run.id]
        process.wait(timeout=10)
        assert store.get_agent_run(run.id).status is AgentRunStatus.CANCELLED
        assert manager.terminate(workflow.id, force=True) == []
    finally:
        if process.poll() is None:
            process.kill()


def test_recovery_distinguishes_live_and_dead_agent_process(store, create_workflow):
    workflow, node = running_node(store, create_workflow)
    run = store.create_agent_run(workflow.id, "a", "noop", 1, timeout_seconds=60)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], **new_group_kwargs()
    )
    identity = process_identity(process.pid)
    store.set_agent_run_process(run.id, process.pid, identity)
    store.increment_attempts("a")
    recovery = RecoveryManager(
        store, DAGScheduler(store), DefaultVerifier(), grace_seconds=0
    )
    try:
        assert recovery.recover_workflow(workflow.id).action == "active"
        assert store.get_node("a").status is NodeStatus.RUNNING
        assert recovery.find_orphans(workflow.id) == []

        terminate_process_tree(process.pid, identity, force=True)
        process.wait(timeout=10)
        assert wait_until(lambda: not process_alive(process.pid, identity))
        assert recovery.find_orphans(workflow.id)[0].id == run.id
        assert recovery.recover_workflow(workflow.id).action == "resumed"
        assert store.get_agent_run(run.id).status is AgentRunStatus.INTERRUPTED
        assert store.get_node("a").status is NodeStatus.READY
    finally:
        if process.poll() is None:
            process.kill()


def test_engine_cancel_terminates_live_agent_process(monkeypatch, tmp_path):
    import devflow.agents.claude_code as module

    executable, pid_file = fake_claude(tmp_path)
    monkeypatch.setattr(module.shutil, "which", lambda name: str(executable))
    engine = WorkflowEngine(
        DevFlowConfig(
            db_path=tmp_path / "devflow.db",
            workdir=tmp_path,
            max_parallel=1,
            agent_timeout=60,
            recovery_grace=1,
        ),
        ClaudeCodeAdapter(executable=str(executable)),
    )
    workflow = engine.create_workflow(
        "cancel live",
        "cancel a running agent",
        [
            {
                "id": "slow",
                "execution_config": {
                    "timeout_seconds": 60,
                    "agent_metadata": {"env": {"FAKE_PID_FILE": str(pid_file)}},
                },
            }
        ],
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(engine.start, workflow.id)
        assert wait_until(
            lambda: any(
                run.pid for run in engine.store.list_agent_runs(workflow.id)
            )
            and pid_file.exists()
        )
        engine.cancel(workflow.id, "test cancel")
        result = future.result(timeout=20)

    assert result.status is WorkflowStatus.CANCELLED
    assert engine.store.get_node("slow").status is NodeStatus.CANCELLED
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    assert wait_until(lambda: not process_alive(child_pid))

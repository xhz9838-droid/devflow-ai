import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from devflow.agents.base import AgentAdapter, AgentResult
from devflow.config import DevFlowConfig
from devflow.db import Database
from devflow.engine import WorkflowEngine
from devflow.errors import EngineLocked
from devflow.models import AgentRunStatus, NodeStatus, WorkflowStatus
from devflow.platform_proc import process_alive, terminate_process_tree
from devflow.state import StateStore

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "tests" / "helpers" / "hanging_engine.py"


def child_env():
    return {**os.environ, "PYTHONPATH": str(ROOT)}


class FastAdapter(AgentAdapter):
    name = "fast"

    def __init__(self):
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        return AgentResult(success=True, output="fast done", exit_code=0)


def make_runtime(tmp_path, adapter):
    return WorkflowEngine(
        DevFlowConfig(
            db_path=tmp_path / "devflow.db",
            workdir=tmp_path / "workspace",
            max_parallel=1,
            agent_timeout=0,
            recovery_grace=0,
            engine_lease_seconds=2,
        ),
        adapter,
    )


def wait_until(predicate, timeout=20.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def resume_when_lease_free(engine, workflow_id, timeout=20.0):
    deadline = time.time() + timeout
    while True:
        try:
            return engine.resume(workflow_id)
        except EngineLocked:
            if time.time() > deadline:
                raise
            time.sleep(0.2)


def test_engine_crash_then_restart_recovers(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    process = subprocess.Popen(
        [sys.executable, str(HELPER), str(tmp_path / "devflow.db"), str(workspace), "fake"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=child_env(),
    )
    try:
        workflow_id = process.stdout.readline().strip()
        assert workflow_id
        store = StateStore(Database(tmp_path / "devflow.db"))
        assert wait_until(
            lambda: store.get_node("slow").status is NodeStatus.RUNNING
            and store.list_agent_runs(workflow_id=workflow_id, status=AgentRunStatus.RUNNING)
        )
        process.kill()
        process.wait(timeout=10)

        adapter = FastAdapter()
        engine = make_runtime(tmp_path, adapter)
        result = resume_when_lease_free(engine, workflow_id)

        assert result.status is WorkflowStatus.COMPLETED
        assert engine.store.get_node("slow").status is NodeStatus.SUCCEEDED
        assert adapter.calls == 1
        runs = engine.store.list_agent_runs(workflow_id)
        assert runs[0].status is AgentRunStatus.INTERRUPTED
        assert runs[-1].status is AgentRunStatus.SUCCEEDED

        # Repeated resume of a terminal workflow must not run anything again.
        assert engine.resume(workflow_id).status is WorkflowStatus.COMPLETED
        assert adapter.calls == 1
    finally:
        if process.poll() is None:
            process.kill()


def fake_agent(tmp_path):
    script = tmp_path / "fake_agent.py"
    pid_file = tmp_path / "fake_agent.pid"
    script.write_text(
        "import os, pathlib, time\n"
        "pathlib.Path(os.environ['FAKE_PID_FILE']).write_text(str(os.getpid()), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        executable = tmp_path / "fake_agent.cmd"
        executable.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}"\r\n', encoding="utf-8"
        )
    else:
        executable = tmp_path / "fake_agent.sh"
        executable.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}"\n', encoding="utf-8"
        )
        executable.chmod(0o755)
    return executable, pid_file


def test_live_agent_survives_engine_crash_and_recovery_waits(tmp_path):
    executable, pid_file = fake_agent(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    process = subprocess.Popen(
        [
            sys.executable,
            str(HELPER),
            str(tmp_path / "devflow.db"),
            str(workspace),
            "agent",
            str(executable),
            str(pid_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=child_env(),
    )
    orphan_pid = None
    orphan_identity = None
    try:
        workflow_id = process.stdout.readline().strip()
        assert workflow_id
        store = StateStore(Database(tmp_path / "devflow.db"))
        assert wait_until(
            lambda: store.list_agent_runs(
                workflow_id=workflow_id, status=AgentRunStatus.RUNNING
            )
            and store.list_agent_runs(workflow_id=workflow_id)[0].pid
            and pid_file.exists()
        )
        run = store.list_agent_runs(workflow_id=workflow_id)[0]
        orphan_pid = run.pid
        orphan_identity = run.process_start_time
        assert process_alive(orphan_pid, orphan_identity)

        process.kill()
        process.wait(timeout=10)

        adapter = FastAdapter()
        engine = make_runtime(tmp_path, adapter)
        result = resume_when_lease_free(engine, workflow_id)

        # The agent process is still alive: recovery must not start a duplicate.
        assert result.status is WorkflowStatus.RUNNING
        assert engine.store.get_node("slow").status is NodeStatus.RUNNING
        assert adapter.calls == 0
        assert engine.recovery.find_orphans(workflow_id) == []

        assert terminate_process_tree(orphan_pid, orphan_identity, force=True)
        assert wait_until(lambda: not process_alive(orphan_pid, orphan_identity))

        result = resume_when_lease_free(engine, workflow_id)
        assert result.status is WorkflowStatus.COMPLETED
        assert engine.store.get_node("slow").status is NodeStatus.SUCCEEDED
        assert adapter.calls == 1
    finally:
        if orphan_pid and process_alive(orphan_pid, orphan_identity):
            terminate_process_tree(orphan_pid, orphan_identity, force=True)
        if process.poll() is None:
            process.kill()


def test_verifier_failure_marks_node_failed(make_engine):
    engine = make_engine()
    engine.adapter.handlers["verify"] = lambda request, count: AgentResult(
        success=True, output="agent ran", exit_code=0
    )
    workflow = engine.create_workflow(
        "verifier fail",
        "artifact is required",
        [
            {
                "id": "verify",
                "retry_policy": {"max_retries": 0},
                "execution_config": {
                    "acceptance": [
                        {"type": "artifact_exists", "path": "missing.txt"},
                    ]
                },
            }
        ],
    )
    result = engine.start(workflow.id)
    assert result.status is WorkflowStatus.FAILED
    node = engine.store.get_node("verify")
    assert node.status is NodeStatus.FAILED
    assert "verification failed" in node.error
    assert node.output["verification"]["passed"] is False


def test_external_workdir_change_does_not_rerun_completed_node(make_engine, tmp_path):
    def handler(request, count):
        (tmp_path / "artifact.txt").write_text("original", encoding="utf-8")
        return AgentResult(success=True, output="done", exit_code=0)

    engine = make_engine()
    engine.adapter.handlers["write"] = handler
    workflow = engine.create_workflow(
        "external change",
        "workdir may change after completion",
        [
            {
                "id": "write",
                "execution_config": {
                    "acceptance": [
                        {"type": "artifact_contains", "path": "artifact.txt", "value": "original"}
                    ]
                },
            }
        ],
    )
    assert engine.start(workflow.id).status is WorkflowStatus.COMPLETED
    assert engine.adapter.call_count("write") == 1

    (tmp_path / "artifact.txt").write_text("modified externally", encoding="utf-8")
    assert engine.resume(workflow.id).status is WorkflowStatus.COMPLETED
    assert engine.adapter.call_count("write") == 1

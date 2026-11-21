import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from devflow.agents.claude_code import ClaudeCodeAdapter
from devflow.config import DevFlowConfig
from devflow.db import Database
from devflow.engine import WorkflowEngine
from devflow.errors import EngineLocked
from devflow.models import AgentRunStatus, NodeStatus, WorkflowStatus
from devflow.platform_proc import process_alive, terminate_process_tree
from devflow.state import StateStore

pytestmark = pytest.mark.real_agent

requires_claude = pytest.mark.skipif(
    not os.environ.get("DEVFLOW_REAL_CLAUDE"),
    reason="set DEVFLOW_REAL_CLAUDE=1 to run the real Claude CLI test",
)

ROOT = Path(__file__).resolve().parents[2]


def runtime_env():
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.setdefault("DEVFLOW_CLAUDE_EXTRA_ARGS", "--permission-mode acceptEdits")
    env["DEVFLOW_ENGINE_LEASE_SECONDS"] = "3"
    return env


def cli(db: Path, workdir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "devflow",
            "--db",
            str(db),
            "--workdir",
            str(workdir),
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=runtime_env(),
        cwd=ROOT,
    )


def wait_until(predicate, timeout=60.0, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def resume_when_lease_free(engine, workflow_id, timeout=30.0):
    deadline = time.time() + timeout
    while True:
        try:
            return engine.resume(workflow_id)
        except EngineLocked:
            if time.time() > deadline:
                raise
            time.sleep(0.2)


@requires_claude
def test_p05_final_real_e2e(tmp_path):
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
    db = tmp_path / "devflow.db"
    token = f"FINAL-{uuid.uuid4().hex[:10].upper()}"

    engine = WorkflowEngine(
        DevFlowConfig(
            db_path=db,
            workdir=workdir,
            max_parallel=1,
            agent_timeout=240,
            recovery_grace=1,
            engine_lease_seconds=3,
            log_level="INFO",
        ),
        ClaudeCodeAdapter(extra_args=["--permission-mode", "acceptEdits"]),
    )
    workflow = engine.create_workflow(
        "P0.5 final E2E",
        "Real Claude DAG with crash recovery, handoff and dynamic human decision.",
        [
            {
                "id": "research",
                "name": "research",
                "execution_config": {
                    "timeout_seconds": 240,
                    "prompt": (
                        "Reply with exactly the token "
                        f"{token} and nothing else. Do not create files. Do not use tools."
                    ),
                    "acceptance": [{"type": "output_contains", "value": token}],
                },
            },
            {
                "id": "handoff",
                "name": "handoff",
                "dependencies": ["research"],
                "execution_config": {
                    "timeout_seconds": 240,
                    "prompt": (
                        "Two phases. Inspect the continuation context.\n"
                        "Phase 1: if it does NOT contain approved_decision, you MUST NOT create "
                        "handoff.txt. Write the human decision request JSON to the path in the "
                        "Human decision protocol section, with question 'Approve the handoff?', "
                        "decision_type 'approval', options [{'id':'approve','label':'Approve'}], "
                        "reason 'Human approval is required', recommended 'approve', risk 'low'. "
                        "Then stop.\n"
                        "Phase 2: if it contains approved_decision with selected_option approve, "
                        "find the token starting with FINAL- in the completed research output and "
                        "write exactly that token to handoff.txt using the Write tool, then stop."
                    ),
                    "acceptance": [
                        {"type": "exit_code", "equals": 0},
                        {"type": "artifact_contains", "path": "handoff.txt", "value": token},
                    ],
                },
            },
            {
                "id": "finalize",
                "name": "finalize",
                "dependencies": ["handoff"],
                "execution_config": {
                    "timeout_seconds": 240,
                    "prompt": (
                        "Write exactly the text FINAL-OK to final.txt using the Write tool, "
                        "then stop."
                    ),
                    "acceptance": [
                        {"type": "artifact_contains", "path": "final.txt", "value": "FINAL-OK"}
                    ],
                },
            },
        ],
    )

    # Run the first engine in a separate process, then force-kill it mid agent run.
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "devflow",
            "--db",
            str(db),
            "--workdir",
            str(workdir),
            "start",
            workflow.id,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=runtime_env(),
        cwd=ROOT,
    )
    first_run = None
    try:
        store = StateStore(Database(db))

        def research_running():
            runs = store.list_agent_runs(workflow_id=workflow.id)
            return (
                runs
                and runs[0].node_id == "research"
                and runs[0].status is AgentRunStatus.RUNNING
                and runs[0].pid
                and runs[0].process_start_time
            )

        assert wait_until(research_running, timeout=120)
        first_run = store.list_agent_runs(workflow_id=workflow.id)[0]
        assert process_alive(first_run.pid, first_run.process_start_time)

        child.kill()
        child.wait(timeout=10)
        assert terminate_process_tree(
            first_run.pid, first_run.process_start_time, force=True
        )
        assert wait_until(
            lambda: not process_alive(first_run.pid, first_run.process_start_time)
        )
    finally:
        if child.poll() is None:
            child.kill()

    evidence_dir = os.environ.get("DEVFLOW_EVIDENCE_DIR")
    if evidence_dir:
        target = Path(evidence_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "phase-crash-inspect.json").write_text(
            json.dumps(engine.inspect(workflow.id), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    # Restart: recovery sees the dead agent process, retries research, then parks handoff.
    resumed = resume_when_lease_free(engine, workflow.id)
    decision = engine.decisions.pending(workflow.id)
    if evidence_dir:
        target = Path(evidence_dir)
        (target / "phase-waiting-inspect.json").write_text(
            json.dumps(engine.inspect(workflow.id), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        (target / "phase-waiting-resume.txt").write_text(
            f"status={resumed.status.value}\n", encoding="utf-8"
        )

    assert resumed.status is WorkflowStatus.WAITING_HUMAN
    assert engine.store.get_node("research").status is NodeStatus.SUCCEEDED
    assert engine.store.get_node("handoff").status is NodeStatus.WAITING_HUMAN
    assert engine.store.get_node("finalize").status is NodeStatus.BLOCKED
    assert len(decision) == 1
    assert not (workdir / "handoff.txt").exists()

    resolved = cli(db, workdir, "resolve", decision[0].id, "--option", "approve")
    assert resolved.returncode == 0, resolved.stderr
    resumed_twice = cli(db, workdir, "resume", workflow.id)

    final = engine.store.get_workflow(workflow.id)
    runs = engine.store.list_agent_runs(workflow.id)

    if evidence_dir:
        target = Path(evidence_dir)
        (target / "final-inspect.json").write_text(
            json.dumps(engine.inspect(workflow.id), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        (target / "token.txt").write_text(token, encoding="utf-8")
        for name, path in (("handoff.txt", workdir / "handoff.txt"), ("final.txt", workdir / "final.txt")):
            if path.exists():
                (target / name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        (target / "resolve.txt").write_text(
            f"rc={resolved.returncode}\nstdout={resolved.stdout}\nstderr={resolved.stderr}\n",
            encoding="utf-8",
        )
        (target / "resume.txt").write_text(
            f"rc={resumed_twice.returncode}\nstdout={resumed_twice.stdout}\n"
            f"stderr={resumed_twice.stderr}\n",
            encoding="utf-8",
        )
        (target / "runs.json").write_text(
            json.dumps(
                [
                    {
                        "node_id": run.node_id,
                        "status": run.status.value,
                        "exit_code": run.exit_code,
                        "pid": run.pid,
                        "process_start_time": run.process_start_time,
                        "output": run.output,
                    }
                    for run in runs
                ],
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )

    assert final.status is WorkflowStatus.COMPLETED
    assert engine.store.get_node("research").status is NodeStatus.SUCCEEDED
    assert engine.store.get_node("handoff").status is NodeStatus.SUCCEEDED
    assert engine.store.get_node("finalize").status is NodeStatus.SUCCEEDED
    assert (workdir / "handoff.txt").read_text(encoding="utf-8").strip() == token
    assert (workdir / "final.txt").read_text(encoding="utf-8").strip() == "FINAL-OK"

    research_runs = [run for run in runs if run.node_id == "research"]
    handoff_runs = [run for run in runs if run.node_id == "handoff"]
    assert len(research_runs) >= 2
    assert research_runs[0].status is AgentRunStatus.INTERRUPTED
    assert handoff_runs[0].status is AgentRunStatus.SUCCEEDED
    assert len(handoff_runs) == 2
    assert '"selected_option": "approve"' not in handoff_runs[0].prompt
    assert '"selected_option": "approve"' in handoff_runs[1].prompt
    event_types = {event.event_type for event in engine.store.list_events(workflow.id)}
    assert {
        "agent.started",
        "node.interrupted",
        "node.retry_scheduled",
        "human_decision.agent_requested",
        "human_decision.resolved",
        "workflow.recovered",
        "workflow.completed",
    } <= event_types
    assert len(engine.store.list_checkpoints(workflow.id)) >= 5

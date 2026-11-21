import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from devflow.agent_protocol import decision_request_path
from devflow.agents.claude_code import ClaudeCodeAdapter
from devflow.config import DevFlowConfig
from devflow.engine import WorkflowEngine
from devflow.models import AgentRunStatus, NodeStatus, WorkflowStatus

pytestmark = pytest.mark.real_agent

requires_claude = pytest.mark.skipif(
    not os.environ.get("DEVFLOW_REAL_CLAUDE"),
    reason="set DEVFLOW_REAL_CLAUDE=1 to run the real Claude CLI test",
)


def cli(db: Path, workdir: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(Path.cwd())}
    env.setdefault("DEVFLOW_CLAUDE_EXTRA_ARGS", "--permission-mode acceptEdits")
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
        env=env,
        cwd=Path.cwd(),
    )


@requires_claude
def test_real_agent_requests_human_decision_and_resumes(tmp_path):
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
    db = tmp_path / "devflow.db"
    engine = WorkflowEngine(
        DevFlowConfig(
            db_path=db,
            workdir=workdir,
            max_parallel=1,
            agent_timeout=240,
            recovery_grace=5,
            engine_lease_seconds=300,
            log_level="INFO",
        ),
        ClaudeCodeAdapter(extra_args=["--permission-mode", "acceptEdits"]),
    )
    workflow = engine.create_workflow(
        "real dynamic HITL",
        "The agent must ask a human before writing the result.",
        [
            {
                "id": "dynamic",
                "name": "dynamic approval",
                "execution_config": {
                    "timeout_seconds": 240,
                    "prompt": (
                        "You are executing in two phases. Inspect the continuation context.\n"
                        "Phase 1: if the context does NOT contain approved_decision, you MUST NOT "
                        "create result.txt. Instead write the human decision request JSON to the "
                        "exact path given in the Human decision protocol section below, with "
                        "question 'Approve writing the result?', decision_type 'approval', "
                        "options [{'id':'approve','label':'Approve'}], reason 'Human approval is "
                        "required before writing', recommended 'approve', risk 'low'. Then stop.\n"
                        "Phase 2: if the context contains approved_decision with selected_option "
                        "approve, write exactly the text APPROVED to result.txt using the Write "
                        "tool, then stop."
                    ),
                    "acceptance": [
                        {"type": "exit_code", "equals": 0},
                        {"type": "artifact_contains", "path": "result.txt", "value": "APPROVED"},
                    ],
                },
            }
        ],
    )

    first = engine.start(workflow.id)
    decision = engine.decisions.pending(workflow.id)
    evidence_dir = os.environ.get("DEVFLOW_EVIDENCE_DIR")
    if evidence_dir:
        target = Path(evidence_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "phase1-inspect.json").write_text(
            json.dumps(engine.inspect(workflow.id), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        (target / "decision.json").write_text(
            json.dumps([d.__dict__ for d in decision], indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    assert first.status is WorkflowStatus.WAITING_HUMAN
    assert engine.store.get_node("dynamic").status is NodeStatus.WAITING_HUMAN
    assert len(decision) == 1
    assert decision[0].question == "Approve writing the result?"
    assert [o["id"] for o in decision[0].options] == ["approve"]
    assert not (workdir / "result.txt").exists()
    assert not decision_request_path(workdir, "dynamic").exists()  # archived

    resolved = cli(db, workdir, "resolve", decision[0].id, "--option", "approve")
    assert resolved.returncode == 0, resolved.stderr
    resumed = cli(db, workdir, "resume", workflow.id)

    final = engine.store.get_workflow(workflow.id)
    runs = engine.store.list_agent_runs(workflow.id)
    result_path = workdir / "result.txt"

    if evidence_dir:
        target = Path(evidence_dir)
        (target / "final-inspect.json").write_text(
            json.dumps(engine.inspect(workflow.id), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        (target / "resolve.txt").write_text(
            f"rc={resolved.returncode}\nstdout={resolved.stdout}\nstderr={resolved.stderr}\n",
            encoding="utf-8",
        )
        (target / "resume.txt").write_text(
            f"rc={resumed.returncode}\nstdout={resumed.stdout}\nstderr={resumed.stderr}\n",
            encoding="utf-8",
        )
        (target / "runs.json").write_text(
            json.dumps(
                [
                    {
                        "id": run.id,
                        "status": run.status.value,
                        "prompt_has_approved": "approved_decision" in run.prompt,
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
        if result_path.exists():
            (target / "result.txt").write_text(
                result_path.read_text(encoding="utf-8"), encoding="utf-8"
            )

    assert final.status is WorkflowStatus.COMPLETED
    assert engine.store.get_node("dynamic").status is NodeStatus.SUCCEEDED
    assert result_path.read_text(encoding="utf-8").strip() == "APPROVED"
    assert len(runs) == 2
    assert all(run.status is AgentRunStatus.SUCCEEDED for run in runs)
    assert '"selected_option": "approve"' not in runs[0].prompt
    assert '"selected_option": "approve"' in runs[1].prompt

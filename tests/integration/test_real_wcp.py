import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

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
def test_real_wcp_handoff_across_agent_runs(tmp_path):
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
    db = tmp_path / "devflow.db"
    token = f"WCP-{uuid.uuid4().hex[:10].upper()}"

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
        "real WCP handoff",
        "Produce a token, pause for approval, then hand the token to the next agent run.",
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
                    "human_decision": {
                        "question": "Continue with the handoff?",
                        "options": [{"id": "approve", "label": "Approve"}],
                    },
                    "prompt": (
                        "You are resuming a workflow. The continuation context contains a "
                        "completed node named research and its output. Find the token that "
                        "starts with WCP- and write exactly that token to handoff.txt using "
                        "the Write tool. Do not read any other files and do not use Bash."
                    ),
                    "acceptance": [
                        {"type": "exit_code", "equals": 0},
                        {"type": "artifact_exists", "path": "handoff.txt"},
                        {
                            "type": "artifact_contains",
                            "path": "handoff.txt",
                            "value": token,
                        },
                    ],
                },
            },
        ],
    )

    first = engine.start(workflow.id)
    assert first.status is WorkflowStatus.WAITING_HUMAN
    assert engine.store.get_node("research").status is NodeStatus.SUCCEEDED
    assert engine.store.get_node("handoff").status is NodeStatus.WAITING_HUMAN
    decision = engine.decisions.pending(workflow.id)[0]

    resolved = cli(db, workdir, "resolve", decision.id, "--option", "approve")
    assert resolved.returncode == 0, resolved.stderr
    resumed = cli(db, workdir, "resume", workflow.id)

    final = engine.store.get_workflow(workflow.id)
    runs = {run.node_id: run for run in engine.store.list_agent_runs(workflow.id)}
    handoff_path = workdir / "handoff.txt"

    evidence_dir = os.environ.get("DEVFLOW_EVIDENCE_DIR")
    if evidence_dir:
        target = Path(evidence_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "inspect.json").write_text(
            json.dumps(engine.inspect(workflow.id), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        if handoff_path.exists():
            (target / "handoff.txt").write_text(
                handoff_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        (target / "token.txt").write_text(token, encoding="utf-8")
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
                {
                    node_id: {
                        "id": run.id,
                        "status": run.status.value,
                        "prompt_contains_token": token in run.prompt,
                        "output": run.output,
                    }
                    for node_id, run in runs.items()
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )

    assert final.status is WorkflowStatus.COMPLETED
    assert engine.store.get_node("handoff").status is NodeStatus.SUCCEEDED
    assert handoff_path.read_text(encoding="utf-8").strip() == token

    assert set(runs) == {"research", "handoff"}
    assert len(engine.store.list_agent_runs(workflow.id)) == 2
    assert all(run.status is AgentRunStatus.SUCCEEDED for run in runs.values())
    assert token in runs["handoff"].prompt

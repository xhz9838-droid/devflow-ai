import json
import os
import shutil
import subprocess
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


@requires_claude
def test_real_claude_creates_and_verifies_an_artifact(tmp_path):
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)

    adapter = ClaudeCodeAdapter(extra_args=["--permission-mode", "acceptEdits"])
    engine = WorkflowEngine(
        DevFlowConfig(
            db_path=tmp_path / "devflow.db",
            workdir=workdir,
            max_parallel=1,
            agent_timeout=240,
            recovery_grace=5,
            log_level="INFO",
        ),
        adapter,
    )
    workflow = engine.create_workflow(
        "real claude smoke",
        "Create the requested artifact and let the engine verify it.",
        [
            {
                "id": "write-artifact",
                "name": "write artifact",
                "execution_config": {
                    "timeout_seconds": 240,
                    "prompt": (
                        "Use the Write tool to create a file named artifact.txt in the "
                        "current working directory. The file content must be exactly "
                        "DF-012-OK with no extra lines. Do not use Bash. Then reply with "
                        "one short confirmation."
                    ),
                    "acceptance": [
                        {"type": "exit_code", "equals": 0},
                        {"type": "artifact_exists", "path": "artifact.txt"},
                        {
                            "type": "artifact_contains",
                            "path": "artifact.txt",
                            "value": "DF-012-OK",
                        },
                    ],
                },
            }
        ],
    )

    result = engine.start(workflow.id)

    assert result.status is WorkflowStatus.COMPLETED
    node = engine.store.get_node("write-artifact")
    assert node.status is NodeStatus.SUCCEEDED
    assert node.output["verification"]["passed"] is True
    assert (workdir / "artifact.txt").read_text(encoding="utf-8").strip() == "DF-012-OK"

    runs = engine.store.list_agent_runs(workflow.id)
    assert len(runs) == 1
    assert runs[0].status is AgentRunStatus.SUCCEEDED
    assert runs[0].adapter == "claude-code"
    assert runs[0].exit_code == 0
    assert runs[0].pid
    assert runs[0].process_start_time
    parsed = runs[0].metadata.get("parsed") or {}
    assert parsed.get("session_id")

    evidence_dir = os.environ.get("DEVFLOW_EVIDENCE_DIR")
    if evidence_dir:
        target = Path(evidence_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "inspect.json").write_text(
            json.dumps(engine.inspect(workflow.id), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        (target / "agent-run.json").write_text(
            json.dumps(
                {
                    "run": {
                        "id": runs[0].id,
                        "status": runs[0].status.value,
                        "adapter": runs[0].adapter,
                        "exit_code": runs[0].exit_code,
                        "prompt": runs[0].prompt,
                        "output": runs[0].output,
                        "metadata": runs[0].metadata,
                    }
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
        shutil.copyfile(workdir / "artifact.txt", target / "artifact.txt")
        (target / "workflow-status.txt").write_text(
            f"workflow={result.status.value}\nnode={node.status.value}\n"
            f"verification_passed={node.output['verification']['passed']}\n",
            encoding="utf-8",
        )

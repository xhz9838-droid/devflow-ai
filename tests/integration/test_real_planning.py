import json
import os
from pathlib import Path

import pytest

from devflow.agents.claude_code import ClaudeCodeAdapter
from devflow.config import DevFlowConfig
from devflow.db import Database
from devflow.engine import WorkflowEngine
from devflow.models import NodeStatus, WorkflowStatus
from devflow.planning.manager import PlanManager
from devflow.planning.models import ClarificationStatus, PlanStatus
from devflow.planning.planner import ClaudeCodePlanner
from devflow.planning.store import PlanStore

pytestmark = pytest.mark.real_agent

requires_claude = pytest.mark.skipif(
    not os.environ.get("DEVFLOW_REAL_CLAUDE"),
    reason="set DEVFLOW_REAL_CLAUDE=1 to run the real Claude CLI test",
)


@requires_claude
def test_real_planning_investigation_and_execution(tmp_path):
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    (workdir / "README.md").write_text(
        "# Calculator project\n\n"
        "A minimal Python project. Use pytest for tests. No external dependencies.\n",
        encoding="utf-8",
    )
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
    db = tmp_path / "devflow.db"
    db_obj = Database(db)
    db_obj.migrate()

    manager = PlanManager(
        PlanStore(db_obj),
        ClaudeCodePlanner(
            ClaudeCodeAdapter(extra_args=["--permission-mode", "plan"]),
            timeout_seconds=300,
        ),
        workdir=str(workdir),
        planner_timeout=300,
    )
    plan = manager.create(
        "Add calculator.py with add(a, b) and subtract(a, b), and "
        "tests/test_calculator.py with pytest tests. Keep the change minimal and "
        "do not modify other files.",
        title="Calculator implementation",
        authorizations=[
            "You may decide ordinary implementation details, naming, and test style.",
            "Do not ask about ordinary technical choices.",
        ],
    )
    manager.generate(plan.id)

    for _ in range(3):
        pending = manager.store.list_clarifications(plan.id, ClarificationStatus.PENDING)
        if not pending:
            break
        for clarification in pending:
            manager.answer(
                plan.id,
                clarification.id,
                clarification.recommended or "Use the recommended option.",
                refine=False,
            )
        manager.refine(plan.id)

    plan = manager.store.get_plan(plan.id)
    assert plan.status is PlanStatus.PLAN_READY, plan.error
    manager.validate(plan.id)
    snapshot = manager.confirm(plan.id, confirmed_by="real-test")

    evidence = os.environ.get("DEVFLOW_EVIDENCE_DIR")
    if evidence:
        target = Path(evidence)
        target.mkdir(parents=True, exist_ok=True)
        (target / "plan.json").write_text(
            json.dumps(plan.plan_json, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        (target / "snapshot.json").write_text(
            json.dumps(
                {
                    "id": snapshot.id,
                    "version": snapshot.version,
                    "content_hash": snapshot.content_hash,
                    "plan": snapshot.plan_json,
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
        (target / "clarifications.json").write_text(
            json.dumps(
                [
                    {
                        "question": item.question,
                        "options": item.options,
                        "recommended": item.recommended,
                        "answer": item.answer,
                        "status": item.status.value,
                    }
                    for item in manager.store.list_clarifications(plan.id)
                ],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    engine = WorkflowEngine(
        DevFlowConfig(
            db_path=db,
            workdir=workdir,
            max_parallel=2,
            agent_timeout=300,
            recovery_grace=5,
            engine_lease_seconds=300,
        ),
        ClaudeCodeAdapter(
            extra_args=[
                "--permission-mode",
                "acceptEdits",
                "--allowedTools",
                "Bash",
            ]
        ),
    )
    workflow = engine.start_workflow_from_plan(plan.id)

    if evidence:
        target = Path(evidence)
        (target / "inspect.json").write_text(
            json.dumps(engine.inspect(workflow.id), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        for path in (workdir / "calculator.py", workdir / "tests" / "test_calculator.py"):
            if path.exists():
                (target / path.name).write_text(
                    path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8"
                )

    assert workflow.status is WorkflowStatus.COMPLETED
    calculator = workdir / "calculator.py"
    tests = workdir / "tests" / "test_calculator.py"
    assert calculator.exists()
    assert tests.exists()
    text = calculator.read_text(encoding="utf-8")
    assert "def add" in text
    assert "def subtract" in text
    assert "def test" in tests.read_text(encoding="utf-8")
    assert manager.store.get_plan(plan.id).status is PlanStatus.EXECUTION_READY
    for node in engine.store.list_nodes(workflow.id):
        assert node.status is NodeStatus.SUCCEEDED

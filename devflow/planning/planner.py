"""Planner interface and repository-aware Claude Code planner."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..agents.base import AgentAdapter, AgentRequest, AgentResult
from ..errors import AgentExecutionError, DevFlowError
from ..models import new_id


class PlannerError(DevFlowError):
    pass


@dataclass(frozen=True)
class PlanningRequest:
    objective: str
    workdir: str
    project_context: dict[str, Any] = field(default_factory=dict)
    resolved_answers: list[dict[str, Any]] = field(default_factory=list)
    previous_plan: dict[str, Any] | None = None
    previous_errors: str | None = None
    authorizations: list[str] = field(default_factory=list)
    timeout_seconds: float = 900.0


@dataclass
class PlanningResult:
    plan: dict[str, Any]
    raw_output: str
    session_id: str | None = None


class Planner:
    def plan(self, request: PlanningRequest) -> PlanningResult:
        raise NotImplementedError


class RepositoryContext:
    """Small, non-secret project snapshot for the planner."""

    @staticmethod
    def collect(workdir: str | Path) -> dict[str, Any]:
        root = Path(workdir)
        if not root.exists():
            return {"workdir": str(root), "exists": False}
        files = sorted(p.name for p in root.iterdir() if p.is_file())[:60]
        dirs = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))[:40]
        context: dict[str, Any] = {
            "workdir": str(root),
            "files": files,
            "directories": dirs,
            "docs": sorted(p.name for p in (root / "docs").glob("*"))[:40]
            if (root / "docs").is_dir()
            else [],
        }
        try:
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            context["git_branch"] = branch.stdout.strip() if branch.returncode == 0 else None
            status = subprocess.run(
                ["git", "status", "--short"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            context["git_status"] = status.stdout.strip()[:2000] if status.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            context["git_branch"] = None
            context["git_status"] = None
        for name in ("README.md", "pyproject.toml", "CLAUDE.md", "package.json"):
            path = root / name
            if path.is_file():
                try:
                    context[name] = path.read_text(encoding="utf-8", errors="replace")[:3000]
                except OSError:
                    pass
        return context


PLAN_SCHEMA = {
    "title": "string",
    "objective": "string",
    "project_context": {},
    "scope": {"included": ["string"], "excluded": ["string"]},
    "assumptions": ["string"],
    "decisions": [
        {"level": "L1|L2", "subject": "string", "choice": "string", "reason": "string"}
    ],
    "risks": ["string"],
    "clarification_candidates": [
        {
            "question": "string",
            "level": "L0|L1|L2|L3",
            "reason": "string",
            "research": "evidence from repository/docs",
            "impact": "why this matters",
            "options": [{"id": "string", "label": "string"}],
            "recommended": "option id",
        }
    ],
    "nodes": [
        {
            "id": "string",
            "name": "string",
            "description": "string",
            "type": "agent",
            "agent": {"adapter": "claude-code", "role": "string"},
            "dependencies": ["node id"],
            "inputs": {},
            "expected_outputs": ["path"],
            "acceptance_criteria": [
                {"type": "artifact_exists|artifact_contains|output_contains|exit_code", "...": "..."}
            ],
            "retry_policy": {"max_retries": 2},
            "execution_config": {"prompt": "string"},
        }
    ],
    "acceptance_criteria": [
        {"type": "artifact_exists|artifact_contains|output_contains|exit_code", "...": "..."}
    ],
    "delivery_requirements": ["string"],
    "metadata": {"created_at": "iso", "created_by": "planner"},
}


class ClaudeCodePlanner(Planner):
    """Planning agent via the existing Agent Adapter. No workflow writes."""

    def __init__(self, adapter: AgentAdapter, *, timeout_seconds: float = 900.0) -> None:
        self.adapter = adapter
        self.timeout_seconds = timeout_seconds

    def plan(self, request: PlanningRequest) -> PlanningResult:
        prompt = self._prompt(request)
        agent_request = AgentRequest(
            workflow_id="planning",
            node_id="planning",
            agent_run_id=new_id("planrun"),
            prompt=prompt,
            workdir=request.workdir,
            timeout_seconds=request.timeout_seconds or self.timeout_seconds,
            context={"objective": request.objective},
        )
        try:
            result: AgentResult = self.adapter.execute(agent_request)
        except AgentExecutionError as exc:
            raise PlannerError(f"planning agent failed: {exc}") from exc
        if not result.success:
            raise PlannerError(
                f"planning agent exited with {result.exit_code}: {result.error or ''}"
            )
        plan = extract_plan(result.output)
        return PlanningResult(plan=plan, raw_output=result.output, session_id=result.session_id)

    def _prompt(self, request: PlanningRequest) -> str:
        context = dict(request.project_context)
        parts = [
            "You are the Planning Agent for DevFlow AI. Investigate the repository and "
            "produce a structured Execution Plan. Do not modify files. Do not create a "
            "workflow. Return ONLY one JSON object matching the schema.",
            f"Objective: {request.objective}",
            "Repository context:\n" + json.dumps(context, ensure_ascii=False, indent=2)[:12000],
            "Decision policy (enforced by code):",
            "- L0/L1/L2 must NOT be asked as questions; record them in decisions.",
            "- Only L3 questions with repository research evidence, impact, 2-4 options and "
            "a recommended option may be listed in clarification_candidates.",
            "- Ask at most 3 questions (5 for complex work). Batch them. Do not repeat "
            "already resolved questions.",
            "- Prefer the existing project conventions; do not ask about naming or ordinary "
            "implementation details.",
            "Every node type must be 'agent'. Every node needs machine-checkable "
            "acceptance_criteria. Supported types: exit_code (agent exit code, or a "
            "command via {'type':'exit_code','command':'python -m pytest tests -q','equals':0}), "
            "output_contains, output_not_contains, artifact_exists, artifact_contains. "
            "The top-level acceptance_criteria must reuse conditions that appear in "
            "node acceptance_criteria.",
            "Schema:\n" + json.dumps(PLAN_SCHEMA, ensure_ascii=False, indent=2),
        ]
        if request.resolved_answers:
            parts.append(
                "Resolved user answers:\n"
                + json.dumps(request.resolved_answers, ensure_ascii=False, indent=2)
            )
        if request.previous_plan:
            parts.append(
                "Previous plan to refine:\n"
                + json.dumps(request.previous_plan, ensure_ascii=False, indent=2)[:16000]
            )
        if request.previous_errors:
            parts.append("Fix these validation errors:\n" + request.previous_errors[:4000])
        return "\n\n".join(parts)


def extract_plan(stdout: str) -> dict[str, Any]:
    text = stdout
    try:
        outer = json.loads(stdout)
        if isinstance(outer, dict):
            if isinstance(outer.get("plan"), dict):
                return outer["plan"]
            if isinstance(outer.get("result"), str):
                text = outer["result"]
    except json.JSONDecodeError:
        pass
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    if start < 0:
        raise PlannerError("planning output did not contain a JSON object")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise PlannerError(f"planning output is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise PlannerError("planning output must be a JSON object")
    return value

"""Node acceptance and workflow completion verification."""

from __future__ import annotations

import os
import shlex
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .models import Node, NodeStatus, VerifyReport, Workflow, utcnow


class Verifier(ABC):
    @abstractmethod
    def verify(
        self,
        workflow: Workflow,
        node: Node,
        agent_result: Any | None,
        context: dict[str, Any] | None = None,
    ) -> VerifyReport:
        raise NotImplementedError


def _lookup(agent_result: Any, name: str) -> Any:
    if agent_result is None:
        return None
    if isinstance(agent_result, dict):
        return agent_result.get(name)
    return getattr(agent_result, name, None)


class DefaultVerifier(Verifier):
    """Evaluates `execution_config.acceptance` conditions.

    With no conditions, a successful agent run passes. The verifier never
    trusts an exit code alone: every configured condition is evaluated and
    recorded as evidence.
    """

    def verify(
        self,
        workflow: Workflow,
        node: Node,
        agent_result: Any | None,
        context: dict[str, Any] | None = None,
    ) -> VerifyReport:
        conditions = node.execution_config.get("acceptance") or []
        success = bool(_lookup(agent_result, "success"))
        output = str(_lookup(agent_result, "output") or "")
        exit_code = _lookup(agent_result, "exit_code")
        evidence: list[dict[str, Any]] = []
        failed: list[str] = []

        if not conditions:
            evidence.append(
                {"condition": "agent_success", "passed": success, "detail": f"exit_code={exit_code}"}
            )
            if not success:
                failed.append(f"agent execution failed (exit_code={exit_code})")
            return VerifyReport(
                passed=success,
                summary="agent run succeeded" if success else "agent run failed",
                evidence=evidence,
                failed_conditions=failed,
                verified_at=utcnow(),
            )

        for raw in conditions:
            if not isinstance(raw, dict) or "type" not in raw:
                failed.append(f"invalid acceptance condition: {raw!r}")
                evidence.append({"condition": raw, "passed": False, "detail": "invalid condition"})
                continue
            kind = raw["type"]
            passed = False
            detail = ""
            if kind == "exit_code":
                expected = raw.get("equals", raw.get("expected", 0))
                command = raw.get("command")
                if command:
                    passed, detail = self._run_command(
                        str(command), expected, context or {}
                    )
                else:
                    passed = exit_code == expected
                    detail = f"agent exit_code={exit_code}, expected={expected}"
            elif kind == "output_contains":
                needle = str(raw.get("value", ""))
                passed = needle in output
                detail = f"output contains {needle!r}: {passed}"
            elif kind == "output_not_contains":
                needle = str(raw.get("value", ""))
                passed = needle not in output
                detail = f"output excludes {needle!r}: {passed}"
            elif kind == "artifact_exists":
                raw_path = str(raw.get("path", ""))
                base = Path((context or {}).get("workdir") or ".")
                target = Path(raw_path)
                if not target.is_absolute():
                    target = base / target
                passed = bool(raw_path) and target.exists()
                detail = f"artifact {target} exists: {passed}"
            elif kind == "artifact_contains":
                raw_path = str(raw.get("path", ""))
                needle = str(raw.get("value", ""))
                base = Path((context or {}).get("workdir") or ".")
                target = Path(raw_path)
                if not target.is_absolute():
                    target = base / target
                try:
                    content = target.read_text(encoding="utf-8", errors="replace")
                    passed = bool(needle) and needle in content
                except OSError as exc:
                    passed = False
                    detail = f"artifact {target} unreadable: {exc}"
                detail = detail or f"artifact {target} contains {needle!r}: {passed}"
            else:
                detail = f"unknown condition type: {kind}"
            evidence.append(
                {"condition": kind, "config": raw, "passed": passed, "detail": detail}
            )
            if not passed:
                failed.append(detail)

        return VerifyReport(
            passed=not failed,
            summary="all acceptance conditions passed" if not failed else "; ".join(failed),
            evidence=evidence,
            failed_conditions=failed,
            verified_at=utcnow(),
        )

    def _run_command(
        self, command: str, expected: int, context: dict[str, Any]
    ) -> tuple[bool, str]:
        base = Path(context.get("workdir") or ".")
        try:
            args = shlex.split(command, posix=os.name != "nt")
            result = subprocess.run(
                args,
                cwd=base,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=float(context.get("command_timeout_seconds") or 300),
                shell=False,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return False, f"command {command!r} failed to run: {exc}"
        passed = result.returncode == expected
        output = ((result.stdout or "") + (result.stderr or "")).strip()[-1000:]
        return passed, f"command {command!r} exit={result.returncode}, expected={expected}; {output}"


class CompletionVerifier:
    """Workflow completion is a Core decision, never an agent exit code."""

    def evaluate(
        self,
        workflow: Workflow,
        nodes: list[Node],
        pending_decisions: int,
    ) -> VerifyReport:
        evidence: list[dict[str, Any]] = []
        failed: list[str] = []

        required = [n for n in nodes if n.required]
        not_done = [n for n in required if n.status is not NodeStatus.SUCCEEDED]
        if not_done:
            failed.append(
                "required nodes not succeeded: "
                + ", ".join(f"{n.id}={n.status.value}" for n in not_done)
            )
        evidence.append(
            {
                "condition": "required_nodes_succeeded",
                "passed": not not_done,
                "detail": f"{len(required) - len(not_done)}/{len(required)}",
            }
        )

        no_verification = [
            n.id
            for n in nodes
            if n.status is NodeStatus.SUCCEEDED
            and not ((n.output or {}).get("verification") or {}).get("passed")
        ]
        if no_verification:
            failed.append("nodes succeeded without passing verification: " + ", ".join(no_verification))
        evidence.append(
            {
                "condition": "node_verification_passed",
                "passed": not no_verification,
                "detail": f"missing verification: {no_verification}",
            }
        )

        if pending_decisions:
            failed.append(f"{pending_decisions} human decision(s) still pending")
        evidence.append(
            {
                "condition": "no_pending_decisions",
                "passed": pending_decisions == 0,
                "detail": f"pending={pending_decisions}",
            }
        )

        running = [n.id for n in nodes if n.status is NodeStatus.RUNNING]
        if running:
            failed.append("nodes still running: " + ", ".join(running))
        evidence.append(
            {"condition": "no_running_nodes", "passed": not running, "detail": str(running)}
        )

        return VerifyReport(
            passed=not failed,
            summary="workflow complete" if not failed else "; ".join(failed),
            evidence=evidence,
            failed_conditions=failed,
            verified_at=utcnow(),
        )

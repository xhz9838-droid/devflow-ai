"""Claude Code CLI adapter. One concrete adapter; Core stays unaware of it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from ..errors import AgentExecutionError, AgentTimeout
from .base import AgentAdapter, AgentRequest, AgentResult, Capability


class ClaudeCodeAdapter(AgentAdapter):
    name = "claude-code"
    capabilities = frozenset(
        {
            Capability.START,
            Capability.RESUME_SESSION,
            Capability.STRUCTURED_OUTPUT,
            Capability.TOOL_CALLS,
        }
    )

    def __init__(self, executable: str = "claude", extra_args: list[str] | None = None) -> None:
        self.executable = executable
        self.extra_args = list(extra_args or [])

    def execute(self, request: AgentRequest) -> AgentResult:
        executable = shutil.which(self.executable) or self.executable
        if not shutil.which(self.executable):
            raise AgentExecutionError(f"agent executable not found: {self.executable}")

        command = [executable, "-p", request.prompt, "--output-format", "json"]
        if request.session_id:
            command.extend(["--resume", request.session_id])
        command.extend(self.extra_args)

        env = os.environ.copy()
        env.update({k: str(v) for k, v in request.metadata.get("env", {}).items()})
        try:
            proc = subprocess.run(
                command,
                cwd=request.workdir or None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=request.timeout_seconds or None,
                env=env,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentTimeout(f"claude timed out after {request.timeout_seconds}s") from exc
        except OSError as exc:
            raise AgentExecutionError(f"could not start claude: {exc}") from exc

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        session_id = None
        metadata: dict[str, object] = {"stderr": stderr}
        try:
            parsed = json.loads(stdout) if stdout else None
            if isinstance(parsed, dict):
                session_id = parsed.get("session_id")
                metadata["parsed"] = parsed
        except json.JSONDecodeError:
            metadata["parsed"] = None

        return AgentResult(
            success=proc.returncode == 0,
            output=stdout,
            exit_code=proc.returncode,
            error=None if proc.returncode == 0 else stderr or f"exit code {proc.returncode}",
            session_id=session_id,
            metadata=metadata,
        )

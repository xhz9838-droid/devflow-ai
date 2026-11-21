"""Claude Code CLI adapter. One concrete adapter; Core stays unaware of it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from ..errors import AgentExecutionError, AgentTimeout
from ..platform_proc import process_alive, process_identity, terminate_process_tree
from .base import AgentAdapter, AgentRequest, AgentResult, Capability


class ClaudeCodeAdapter(AgentAdapter):
    name = "claude-code"
    capabilities = frozenset(
        {
            Capability.START,
            Capability.RESUME_SESSION,
            Capability.STRUCTURED_OUTPUT,
            Capability.TOOL_CALLS,
            Capability.PROCESS_CONTROL,
        }
    )

    def __init__(self, executable: str = "claude", extra_args: list[str] | None = None) -> None:
        self.executable = executable
        self.extra_args = list(extra_args or [])

    def execute(self, request: AgentRequest) -> AgentResult:
        executable = shutil.which(self.executable) or self.executable
        if not shutil.which(self.executable):
            raise AgentExecutionError(f"agent executable not found: {self.executable}")

        command = [executable, "-p", "--output-format", "json"]
        if request.session_id:
            command.extend(["--resume", request.session_id])
        command.extend(self.extra_args)

        env = os.environ.copy()
        env.update({k: str(v) for k, v in request.metadata.get("env", {}).items()})

        popen_kwargs: dict[str, object] = {
            "cwd": request.workdir or None,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": env,
            "shell": False,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(command, **popen_kwargs)
        except OSError as exc:
            raise AgentExecutionError(f"could not start claude: {exc}") from exc

        identity = process_identity(process.pid)
        if request.on_process_started:
            request.on_process_started(process.pid, identity)

        try:
            stdout, stderr = process.communicate(
                input=request.prompt, timeout=request.timeout_seconds or None
            )
        except subprocess.TimeoutExpired as exc:
            self._stop(process, identity)
            stdout, stderr = self._drain(process)
            raise AgentTimeout(
                f"claude timed out after {request.timeout_seconds}s"
            ) from exc

        stdout = (stdout or "").strip()
        stderr = (stderr or "").strip()
        session_id = None
        metadata: dict[str, object] = {"stderr": stderr, "pid": process.pid}
        try:
            parsed = json.loads(stdout) if stdout else None
            if isinstance(parsed, dict):
                session_id = parsed.get("session_id")
                metadata["parsed"] = parsed
        except json.JSONDecodeError:
            metadata["parsed"] = None

        return AgentResult(
            success=process.returncode == 0,
            output=stdout,
            exit_code=process.returncode,
            error=None
            if process.returncode == 0
            else stderr or f"exit code {process.returncode}",
            session_id=session_id,
            metadata=metadata,
        )

    def _stop(self, process: subprocess.Popen, identity: str | None) -> None:
        terminate_process_tree(process.pid, identity, force=False)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and process.poll() is None:
            if not process_alive(process.pid, identity):
                break
            time.sleep(0.1)
        if process.poll() is None:
            terminate_process_tree(process.pid, identity, force=True)
            try:
                process.kill()
            except OSError:
                pass

    def _drain(self, process: subprocess.Popen) -> tuple[str, str]:
        try:
            return process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                return process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                return "", "agent process did not exit after termination"

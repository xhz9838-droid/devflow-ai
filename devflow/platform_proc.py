"""Cross-platform process identity, liveness and tree termination.

PIDs alone are not identity: they can be reused. Every check compares the
current process creation token with the recorded one before acting.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:  # pragma: no cover - exercised on Windows
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _PROCESS_TERMINATE = 0x0001
    _STILL_ACTIVE = 259

    class _FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
    ]
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]


@dataclass(frozen=True)
class ProcessStatus:
    pid: int
    start_time: str | None
    alive: bool


def _windows_identity(pid: int) -> str | None:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        exit_code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        if exit_code.value != _STILL_ACTIVE:
            return None
        created, exited, kernel, user = (_FILETIME() for _ in range(4))
        if not _kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        value = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return f"win:{value}"
    finally:
        _kernel32.CloseHandle(handle)


def _posix_identity(pid: int) -> str | None:
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
            fields = stat.rsplit(")", 1)[1].split()
            if fields[0] == "Z":  # zombie: already exited
                return None
            return f"linux:{fields[19]}"
        except (OSError, IndexError):
            return None
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.strip()
    return f"ps:{text}" if result.returncode == 0 and text else None


def process_identity(pid: int) -> str | None:
    if pid <= 0:
        return None
    return _windows_identity(pid) if IS_WINDOWS else _posix_identity(pid)


def process_alive(pid: int, expected_start_time: str | None = None) -> bool:
    identity = process_identity(pid)
    if identity is None:
        return False
    return expected_start_time is None or identity == expected_start_time


def process_status(pid: int, expected_start_time: str | None = None) -> ProcessStatus:
    identity = process_identity(pid)
    return ProcessStatus(
        pid=pid,
        start_time=identity,
        alive=identity is not None
        and (expected_start_time is None or identity == expected_start_time),
    )


def _windows_terminate(pid: int, expected: str | None, force: bool) -> bool:
    identity = _windows_identity(pid)
    if identity is None or (expected and identity != expected):
        return False
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    try:
        subprocess.run(command, capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        pass
    if _windows_identity(pid) is None:
        return True
    if not force:
        return False
    handle = _kernel32.OpenProcess(
        _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return _windows_identity(pid) is None
    try:
        return bool(_kernel32.TerminateProcess(handle, 1))
    finally:
        _kernel32.CloseHandle(handle)


def _posix_terminate(pid: int, expected: str | None, force: bool) -> bool:
    identity = _posix_identity(pid)
    if identity is None or (expected and identity != expected):
        return False
    signum = signal.SIGKILL if force else signal.SIGTERM
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    try:
        if pgid == pid:
            os.killpg(pgid, signum)
        else:
            os.kill(pid, signum)
        return True
    except OSError:
        return False


def terminate_process_tree(
    pid: int, expected_start_time: str | None = None, force: bool = False
) -> bool:
    if pid <= 0:
        return False
    if IS_WINDOWS:
        return _windows_terminate(pid, expected_start_time, force)
    return _posix_terminate(pid, expected_start_time, force)

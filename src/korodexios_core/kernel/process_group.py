"""Ending a child process and everything it started, on every platform, in bounded time.

``context.run_process`` puts each child in a group of its own (a new session on POSIX, a new
process group on Windows), so a timeout or a cancel can reach the grandchildren too:

1. polite: SIGTERM to the group (POSIX), CTRL_BREAK_EVENT to the group (Windows);
2. after ``grace`` seconds, for good: SIGKILL to the group, ``taskkill /T /F`` on Windows;
3. last resort: ``Popen.kill()`` on the child itself.

:func:`reap` then waits a bounded time, so a child that survives everything (a zombie held by
a stuck kernel call, a failed taskkill) can never hang the caller; it reports exit code ``-1``.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from typing import Any, Final

TERM_GRACE: Final[float] = 3.0
"""Seconds between the polite request and the forced end."""
REAP_WAIT: Final[float] = 10.0
"""Seconds :func:`reap` waits for the exit status before giving up."""
TASKKILL_TIMEOUT: Final[float] = 10.0


def group_flags() -> dict[str, Any]:
    """Return Popen keyword arguments that put the child in a group of its own."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def end_group(process: subprocess.Popen[Any], grace: float = TERM_GRACE) -> None:
    """End *process* and everything it started: polite first, then for good."""
    _polite(process)
    try:
        process.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    _forced(process)
    with contextlib.suppress(OSError):
        process.kill()  # the child itself, should the group call have missed it


def reap(process: subprocess.Popen[Any], timeout: float = REAP_WAIT, unknown: int = -1) -> int:
    """Return the exit code, waiting at most *timeout* seconds; *unknown* when it never came."""
    try:
        return int(process.wait(timeout=timeout))
    except subprocess.TimeoutExpired:
        return unknown


def _polite(process: subprocess.Popen[Any]) -> None:
    if os.name == "nt":
        # A child in its own process group receives CTRL_BREAK like a Ctrl+Break at a console.
        with contextlib.suppress(OSError, ValueError):
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        _killpg(process.pid, signal.SIGTERM)


def _forced(process: subprocess.Popen[Any]) -> None:
    if os.name == "nt":
        # taskkill /T walks the child tree, which Windows has no process group call for.
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=TASKKILL_TIMEOUT,
            )
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        _killpg(process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def _killpg(pgid: int, sig: int) -> None:
    """Signal a process group (POSIX only; getattr keeps the Windows type check quiet)."""
    getattr(os, "killpg")(pgid, sig)  # noqa: B009 - os.killpg does not exist on Windows


__all__ = ["REAP_WAIT", "TASKKILL_TIMEOUT", "TERM_GRACE", "end_group", "group_flags", "reap"]

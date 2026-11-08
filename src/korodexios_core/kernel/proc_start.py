"""When a process was created: the way to tell a live worker from a reused process id.

After a crash the operating system may hand the dead worker's pid to an unrelated process. A
pid that is alive says nothing about who owns it; a process created after the work began cannot
be the one that began it. Where the creation time cannot be read, the answer is None and the
caller keeps its old, pid-only judgement.
"""

from __future__ import annotations

import os
import sys
import time

_EPOCH_DELTA_SECONDS = 11_644_473_600  # 1601-01-01 (FILETIME) to 1970-01-01
_QUERY_LIMITED = 0x1000


def _windows_created_at(pid: int) -> float | None:
    """Return when process *pid* was created, from GetProcessTimes; None elsewhere or on error."""
    created_at: float | None = None
    if sys.platform == "win32":  # folded away by the type checker on other platforms
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(_QUERY_LIMITED, False, pid)
        if handle:
            try:
                created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
                ok = kernel.GetProcessTimes(
                    handle, ctypes.byref(created), ctypes.byref(exited),
                    ctypes.byref(kernel_time), ctypes.byref(user_time),
                )
                if ok:
                    ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
                    created_at = ticks / 10_000_000 - _EPOCH_DELTA_SECONDS
            finally:
                kernel.CloseHandle(handle)
    return created_at


def _linux_created_at(pid: int) -> float | None:
    with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
        stat = handle.read()
    # The command name sits in parentheses and may hold spaces; the fields follow the last ')'.
    start_ticks = int(stat.rsplit(")", 1)[1].split()[19])
    with open("/proc/stat", encoding="utf-8") as handle:
        boot = next(int(line.split()[1]) for line in handle if line.startswith("btime "))
    per_second = float(getattr(os, "sysconf", lambda _name: 100)("SC_CLK_TCK"))
    return boot + start_ticks / per_second


def process_created_at(pid: int) -> float | None:
    """Return the epoch time process *pid* was created at, or None when it cannot be read."""
    platform = sys.platform  # a variable, so the type checker keeps both branches
    try:
        if platform == "win32":
            return _windows_created_at(int(pid))
        if platform.startswith("linux"):
            return _linux_created_at(int(pid))
    except (OSError, ValueError, IndexError, StopIteration, AttributeError):
        return None
    return None


def started_after(pid: int, moment: float, slack: float = 2.0) -> bool:
    """Return True when process *pid* is known to have been created after *moment*.

    *slack* absorbs the difference between the clock that stamped *moment* and the one the
    operating system uses for process creation.
    """
    created = process_created_at(pid)
    return created is not None and created > float(moment) + slack and created <= time.time() + 60

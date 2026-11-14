"""Ending a child group in bounded time: polite, then forced, and a reap that never hangs."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from korodexios_core.kernel.process_group import end_group, group_flags, reap

STUBBORN = (
    "import signal, subprocess, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(child.pid, flush=True)\n"
    "time.sleep(60)\n"
)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.posix
def test_a_child_that_ignores_sigterm_ends_with_its_grandchild_after_the_grace() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", STUBBORN], stdout=subprocess.PIPE, text=True, **group_flags()
    )
    assert process.stdout is not None
    grandchild = int(process.stdout.readline())
    started = time.monotonic()
    end_group(process, grace=0.5)
    code = reap(process, timeout=5)
    assert code != 0 and code != -1 and time.monotonic() - started < 5
    deadline = time.monotonic() + 5
    while _alive(grandchild) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(grandchild)


@pytest.mark.posix
def test_a_polite_child_ends_on_the_first_request() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                               **group_flags())
    started = time.monotonic()
    end_group(process, grace=5)
    assert reap(process, timeout=2) == -15 and time.monotonic() - started < 4


class _Stuck:
    pid = 0

    def wait(self, timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired("stuck", timeout or 0)


def test_reap_gives_up_instead_of_hanging() -> None:
    assert reap(_Stuck(), timeout=0.01) == -1  # type: ignore[arg-type]
    assert reap(_Stuck(), timeout=0.01, unknown=99) == 99  # type: ignore[arg-type]

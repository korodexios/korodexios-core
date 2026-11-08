"""A real daemon process stops cleanly on a signal; a second signal forces it (review X5, R3)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from korodexios_core.kernel import signals as signals_module
from korodexios_core.kernel.signals import FORCE_AFTER_SECONDS, SignalCoordinator
from tests.conftest import PROJECT_CONFIG

WINDOWS = os.name == "nt"


class _Forced(Exception):
    """Stands in for os._exit, which must not end the test process."""


class _Log:
    def __getattr__(self, name: str):
        return lambda *args, **kwargs: None


def start_daemon(root: Path) -> subprocess.Popen[str]:
    enabled = PROJECT_CONFIG.replace("  ipc:\n    enabled: false", "  ipc:\n    enabled: true")
    (root / "kdx.yml").write_text(enabled, encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0  # type: ignore[attr-defined]
    process = subprocess.Popen(
        [sys.executable, "-m", "korodexios_core", "--home", str(root), "--no-log-file",
         "--no-color", "--ipc-port", "0", "run"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags,
    )
    discovery = root / "var" / "chassis.ipc"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not discovery.is_file():
        assert process.poll() is None, process.communicate()
        time.sleep(0.1)
    assert discovery.is_file(), "the daemon never announced itself"
    time.sleep(0.3)
    return process


@pytest.mark.parametrize(
    ("name", "expected"),
    [("interrupt", 130)] if WINDOWS else [("interrupt", 130), ("terminate", 143)],
)
def test_a_signal_stops_the_daemon_with_its_code_and_no_traceback(
    tmp_path: Path, name: str, expected: int
) -> None:
    process = start_daemon(tmp_path)
    if WINDOWS:
        process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
    else:
        process.send_signal(signal.SIGINT if name == "interrupt" else signal.SIGTERM)
    try:
        _, err = process.communicate(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
    assert process.returncode == expected, err
    assert "Traceback" not in err, err


def test_a_second_signal_after_a_moment_ends_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    exits: list[int] = []

    def force(code: int) -> None:
        exits.append(code)
        raise _Forced

    coordinator = SignalCoordinator(_Log(), force_exit=force)  # type: ignore[arg-type]
    handler = coordinator._build_handler(int(signal.SIGINT))
    handler(int(signal.SIGINT), None)
    assert coordinator.shutdown_requested and exits == []
    handler(int(signal.SIGINT), None)  # a duplicate delivery within the grace is ignored
    assert exits == []
    real = time.monotonic
    monkeypatch.setattr(signals_module.time, "monotonic", lambda: real() + FORCE_AFTER_SECONDS + 1)
    with pytest.raises(_Forced):
        handler(int(signal.SIGINT), None)
    assert exits == [130]


def _no_exit(code: int) -> None:
    raise AssertionError(f"the process would have exited with {code}")


def test_no_force_without_an_earlier_stop_request() -> None:
    coordinator = SignalCoordinator(_Log(), force_exit=_no_exit)  # type: ignore[arg-type]
    coordinator.force_if_repeated(int(signal.SIGINT))  # nothing was asked yet: no exit
    coordinator.request_clean_stop()
    coordinator.force_if_repeated(int(signal.SIGINT))  # a clean stop is not a signal either

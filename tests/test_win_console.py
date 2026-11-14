"""Windows console close, log-off and shutdown ask for the clean stop (review 2026-11-08, V7)."""

from __future__ import annotations

import sys

import pytest

from korodexios_core.kernel.win_console import (
    CTRL_CLOSE_EVENT,
    CTRL_LOGOFF_EVENT,
    CTRL_SHUTDOWN_EVENT,
    console_event_handler,
    install_console_handler,
)


@pytest.mark.parametrize("event", [CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT])
def test_an_ending_event_requests_the_stop_and_holds_windows_off(event: int) -> None:
    stops: list[int] = []
    slept: list[float] = []
    handle = console_event_handler(lambda: stops.append(1), hold=0.5, sleep=slept.append)
    assert handle(event) is True
    assert stops == [1] and sum(slept) == pytest.approx(0.5)


@pytest.mark.parametrize("event", [0, 1])  # Ctrl+C, Ctrl+Break: Python's own signals
def test_ctrl_c_and_ctrl_break_are_left_to_python(event: int) -> None:
    stops: list[int] = []
    assert console_event_handler(lambda: stops.append(1), sleep=lambda s: None)(event) is False
    assert stops == []


@pytest.mark.skipif(sys.platform == "win32", reason="registers a real handler on Windows")
def test_nothing_is_registered_off_windows() -> None:
    assert install_console_handler(lambda: None) is None


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="SetConsoleCtrlHandler exists only on Windows")
def test_the_handler_registers_and_is_removed_again() -> None:
    remove = install_console_handler(lambda: None)
    if remove is None:
        pytest.skip("no console attached to this process")
    remove()

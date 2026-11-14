"""Windows console close, log-off and shutdown: ask for the clean stop before Windows ends us.

Python turns Ctrl+C and Ctrl+Break into SIGINT and SIGBREAK, but closing the console window,
logging off and shutting down arrive as console control events Python never delivers: Windows
calls the registered handlers on a thread of its own and ends the process as soon as they
return (or after a few seconds). Without a handler the chassis skips ``stop()`` entirely.

:func:`install_console_handler` registers one with ``SetConsoleCtrlHandler``. For those three
events it records a SIGTERM-like stop request (the daemon's usual clean path) and then keeps the
handler busy while the main thread stops, because returning early would let Windows end the
process at once. When the main thread finishes, the process exits and takes this thread along.
Everything here is a no-op on other platforms; ``ctypes.WinDLL`` is touched only on Windows.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any, Final

CTRL_CLOSE_EVENT: Final[int] = 2
CTRL_LOGOFF_EVENT: Final[int] = 5
CTRL_SHUTDOWN_EVENT: Final[int] = 6
ENDING_EVENTS: Final[frozenset[int]] = frozenset(
    {CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT}
)
HOLD_SECONDS: Final[float] = 20.0
"""The longest the handler holds Windows off; Windows itself may end the process sooner."""

_installed: list[Any] = []  # the callback object must outlive the registration


def _platform() -> str:
    """Return ``sys.platform`` through a call, so mypy checks the Windows branch everywhere."""
    return sys.platform


def console_event_handler(
    request_stop: Callable[[], None],
    *,
    hold: float = HOLD_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[int], bool]:
    """Return the handler body: True (handled) for an ending event after the hold."""

    def handle(event: int) -> bool:
        if int(event) not in ENDING_EVENTS:
            return False  # Ctrl+C and Ctrl+Break go on to Python's own SIGINT/SIGBREAK
        request_stop()
        waited = 0.0
        while waited < hold:
            sleep(0.1)
            waited += 0.1
        return True

    return handle


def install_console_handler(request_stop: Callable[[], None]) -> Callable[[], None] | None:
    """Register the handler on Windows; return the function that removes it (None elsewhere)."""
    if _platform() != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    routine_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)  # type: ignore[attr-defined,unused-ignore]
    handle = console_event_handler(request_stop)
    routine = routine_type(lambda event: bool(handle(int(event))))
    if not kernel32.SetConsoleCtrlHandler(routine, True):
        return None
    _installed.append(routine)

    def remove() -> None:
        kernel32.SetConsoleCtrlHandler(routine, False)
        if routine in _installed:
            _installed.remove(routine)

    return remove


__all__ = [
    "CTRL_CLOSE_EVENT",
    "CTRL_LOGOFF_EVENT",
    "CTRL_SHUTDOWN_EVENT",
    "ENDING_EVENTS",
    "console_event_handler",
    "install_console_handler",
]

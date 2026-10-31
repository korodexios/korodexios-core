"""Per-call stdout capture for delegated commands.

A module command writes its answer to stdout, the UI channel. Run locally, that is the
operator's terminal; run inside a daemon over /api/v1/rpc, it is the daemon's own
console, which nobody who asked is looking at. The capture routes what one delegated
call writes into a buffer that travels back in the RPC reply, while every other thread
keeps writing to the real stream.

The route is a context variable, not a global swap: the control plane answers each
request on its own thread, so two concurrent calls never read each other's output, and
the daemon's heartbeat or log lines never land in a caller's reply. A captured call is
also never interactive - a prompt would block on the daemon's keyboard, not the caller's.
"""

from __future__ import annotations

import contextlib
import io
import sys
import threading
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any, Final

MAX_CAPTURED_CHARS: Final[int] = 1024 * 1024
"""Most output one delegated call returns; the rest is cut with a note (1 MiB)."""

_BUFFER: ContextVar[io.StringIO | None] = ContextVar("kdx_rpc_output", default=None)
_INSTALL_LOCK = threading.Lock()


class _RoutedStream:
    """A stdout stand-in: the active capture buffer for this context, else the real stream."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    @property
    def inner(self) -> Any:
        """Return the stream this one writes to when nothing is captured."""
        return self._inner

    def write(self, text: str) -> int:
        buffer = _BUFFER.get()
        if buffer is None:
            return int(self._inner.write(text))
        return buffer.write(text)

    def flush(self) -> None:
        if _BUFFER.get() is None:
            self._inner.flush()

    def isatty(self) -> bool:
        if _BUFFER.get() is not None:
            return False
        return bool(getattr(self._inner, "isatty", lambda: False)())

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def capture_active() -> bool:
    """Return True while the calling context runs a delegated, captured command."""
    return _BUFFER.get() is not None


def _install() -> None:
    """Route sys.stdout through the context-aware stream, once per replaced stdout."""
    with _INSTALL_LOCK:
        if not isinstance(sys.stdout, _RoutedStream):
            sys.stdout = _RoutedStream(sys.stdout)


@contextlib.contextmanager
def capture_output(buffer: io.StringIO | None = None) -> Iterator[io.StringIO]:
    """Collect what this context writes to stdout until the block ends.

    Pass *buffer* to watch the output grow from another thread while the call runs.

    The routed stream stays installed afterwards: with no capture active it is a plain
    pass-through, and removing it while another thread still captures would cut that
    thread's output loose.
    """
    _install()
    buffer = io.StringIO() if buffer is None else buffer
    token = _BUFFER.set(buffer)
    try:
        yield buffer
    finally:
        _BUFFER.reset(token)


def captured_text(buffer: io.StringIO) -> str:
    """Return the buffer's text, cut to MAX_CAPTURED_CHARS with a note when longer."""
    text = buffer.getvalue()
    if len(text) <= MAX_CAPTURED_CHARS:
        return text
    dropped = len(text) - MAX_CAPTURED_CHARS
    return text[:MAX_CAPTURED_CHARS] + f"\n[!] output cut: {dropped} more characters\n"

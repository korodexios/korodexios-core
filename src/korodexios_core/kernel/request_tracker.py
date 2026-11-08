"""An HTTP server that knows how many requests it is answering, so a stop can wait for them.

The control plane serves each request on a daemon thread, which the interpreter cuts off
mid-write when the process exits. `wait_idle` lets the shutdown give the requests that are
being answered (an RPC, a module route) a chance to finish inside the shutdown budget.
Long-lived streams (server-sent events, WebSocket) are not waited for: they end on their own
account when the daemon stops.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from http.server import ThreadingHTTPServer
from typing import Any


class TrackedHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that counts the requests being served."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._busy = 0
        self._idle = threading.Condition()
        super().__init__(*args, **kwargs)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        with self._idle:
            self._busy += 1
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._idle:
                self._busy -= 1
                self._idle.notify_all()

    @property
    def busy(self) -> int:
        """Return how many requests (streams included) are being served right now."""
        with self._idle:
            return self._busy

    def wait_idle(self, streams: Callable[[], int], timeout: float) -> bool:
        """Wait up to *timeout* seconds until only streams are left; return whether it did."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._idle:
            while self._busy - streams() > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(min(remaining, 0.1))
        return True

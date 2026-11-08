"""The live event feed of the control plane: `GET /api/v1/events`, server-sent events.

`kdx events` follows the daemon's bus from a terminal, so the feed is the chassis' own and does
not depend on a module (the web console keeps its route under `/api/v1/ui/events` for the
browser). Every connected reader gets a bounded queue: one that reads too slowly loses events
instead of slowing the module that published them.
"""

from __future__ import annotations

import contextlib
import json
import queue
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from .ipc import IpcRequest, IpcRoute, IpcStream

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..events.bus import Event, EventBus

EVENTS_PATH = "/api/v1/events"
SUBSCRIBER_NAME = "core.events.feed"
QUEUE_SIZE = 500
KEEPALIVE_SECONDS = 15.0
POLL_SECONDS = 0.5
"""How often an idle feed looks at *closing*, so a stopping daemon ends it within a moment."""


def frames(
    bus: EventBus,
    closing: Callable[[], bool],
    keepalive: float = KEEPALIVE_SECONDS,
    poll: float = POLL_SECONDS,
) -> Iterator[bytes]:
    """Yield server-sent event frames for every bus event until *closing* turns True."""
    feed: queue.Queue[Event] = queue.Queue(maxsize=QUEUE_SIZE)

    def deliver(event: Event) -> None:
        with contextlib.suppress(queue.Full):
            feed.put_nowait(event)

    subscription = bus.subscribe("*", deliver, name=SUBSCRIBER_NAME)
    idle = 0.0
    try:
        yield b"retry: 3000\n: connected\n\n"
        while not closing():
            try:
                event = feed.get(timeout=poll)
            except queue.Empty:
                idle += poll
                if idle >= keepalive:
                    idle = 0.0
                    yield b": keepalive\n\n"
                continue
            idle = 0.0
            frame = {
                "name": event.name,
                "source": event.source,
                "at": round(event.at, 3),
                "sequence": event.sequence,
                "payload": event.payload,
            }
            yield f"data: {json.dumps(frame, default=str)}\n\n".encode()
    finally:
        subscription.unsubscribe(bus)


def event_route(bus: EventBus, closing: Callable[[], bool]) -> IpcRoute:
    """Return the core route that streams *bus* to a terminal or a script (role viewer)."""

    def serve(request: IpcRequest) -> IpcStream:
        return IpcStream(frames(bus, closing))

    return IpcRoute(
        path=EVENTS_PATH,
        handler=serve,
        module="core",
        summary="live bus events as server-sent events (what 'kdx events' follows)",
        content_type="text/event-stream",
    )

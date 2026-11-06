"""Realtime module: one WebSocket for live events and commands ('/api/v1/ws').

A dashboard, a chat bot, a Node-RED or n8n flow, or an agent keeps one connection open
and speaks small JSON messages on it:

    -> {"type": "subscribe", "pattern": "core.job.**"}      events matching the pattern
    -> {"type": "unsubscribe", "pattern": "core.job.**"}
    -> {"type": "rpc", "id": 7, "command": "heartbeat status", "args": {}}
    -> {"type": "rpc", "id": 8, "command": "backup run", "background": true}
    -> {"type": "publish", "event": "deploy.done", "payload": {...}}   -> 'inbound.deploy.done'
    -> {"type": "ping"}
    <- {"type": "hello" | "event" | "result" | "subscribed" | "published" | "pong" | "error"}

Every message is authorized as the connection's caller: subscribing needs a viewer,
publishing a contributor, and a command the role the command declares - the same rules
as the HTTP routes, and every command is audited.
"""

from __future__ import annotations

import queue
import re
import threading
from collections.abc import Mapping
from typing import Any

from ...config.schema import ConfigField, ConfigKind, ConfigSchema
from ...events.bus import Event, Subscription
from ...kernel.contract import Module
from ...kernel.ipc import IpcRequest, IpcRoute
from ...kernel.jobs import JobLimitError
from ...kernel.users import Caller, acting_as
from ...kernel.websocket import IpcWebSocket, WebSocket, WebSocketClosed
from ...version import __version__

WS_PATH = "/api/v1/ws"
INBOUND_PREFIX = "inbound"
MAX_SUBSCRIPTIONS = 16
OUTBOX_SIZE = 500
KEEPALIVE_SECONDS = 25.0
EVENT_NAME: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class RealtimeModule(Module):
    """Serves the WebSocket channel and ends every connection when the chassis stops."""

    name = "realtime"
    description = "WebSocket channel on /api/v1/ws: live events, commands, and publishing."
    version = "0.1.0"

    def __init__(self) -> None:
        super().__init__()
        self._sockets: set[WebSocket] = set()
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self.connections = 0
        self.messages = 0

    def config_schema(self) -> ConfigSchema:
        """Declare the one key this module reads."""
        return ConfigSchema(
            module=self.name,
            fields=(
                ConfigField(
                    "publish",
                    kind=ConfigKind.BOOL,
                    default=True,
                    description="Let contributors publish 'inbound.*' events over the socket.",
                ),
            ),
        )

    def ipc_routes(self) -> tuple[IpcRoute, ...]:
        """Accept WebSocket upgrades from any viewer; each message checks its own role."""
        return (
            IpcRoute(
                path=WS_PATH,
                handler=self._route,
                methods=("GET",),
                module=self.name,
                role="viewer",
                summary="WebSocket: subscribe to events, run commands, publish events",
            ),
        )

    def start(self) -> None:
        """Accept connections."""
        self._closing.clear()
        super().start()

    def stop(self) -> None:
        """Close every open socket, so no client holds a server thread past shutdown."""
        self._closing.set()
        with self._lock:
            sockets = list(self._sockets)
        for socket_ in sockets:
            socket_.close(1001, "the chassis is stopping")
        super().stop()

    def health(self) -> dict[str, Any]:
        """Report open and total connections beside the lifecycle block."""
        data = super().health()
        with self._lock:
            data["open"] = len(self._sockets)
        data["connections"] = self.connections
        data["messages"] = self.messages
        return data

    def _route(self, request: IpcRequest) -> IpcWebSocket:
        caller = request.caller

        def session(socket_: WebSocket) -> None:
            Session(self, socket_, caller).run()

        return IpcWebSocket(session=session)

    def track(self, socket_: WebSocket, live: bool) -> None:
        """Remember (or forget) an open socket so stop() can close it."""
        with self._lock:
            if live:
                self._sockets.add(socket_)
                self.connections += 1
            else:
                self._sockets.discard(socket_)


class Session:
    """One connection: a reader on the handler thread, a writer thread for the outbox."""

    def __init__(self, module: RealtimeModule, socket_: WebSocket, caller: Caller | None) -> None:
        self.module = module
        self.socket = socket_
        self.caller = caller
        self.outbox: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=OUTBOX_SIZE)
        self.subscriptions: dict[str, Subscription] = {}
        self.dropped = 0

    def run(self) -> None:
        """Serve the connection until the client leaves or the chassis stops."""
        module = self.module
        if module._closing.is_set():
            self.socket.close(1001, "the chassis is stopping")
            return
        module.track(self.socket, True)
        writer = threading.Thread(target=self._write, name="kdx-ws-writer", daemon=True)
        writer.start()
        self.send(
            {
                "type": "hello",
                "version": __version__,
                "user": self.caller.name if self.caller else None,
                "role": self.caller.role if self.caller else None,
            }
        )
        try:
            with acting_as(self.caller):
                self._read()
        finally:
            for subscription in self.subscriptions.values():
                subscription.unsubscribe(module.bus)
            self.subscriptions.clear()
            module.track(self.socket, False)
            self.outbox.put(None)
            writer.join(timeout=2.0)

    def send(self, message: dict[str, Any]) -> None:
        """Queue *message* for the writer; a full outbox drops it and counts the loss."""
        try:
            self.outbox.put_nowait(message)
        except queue.Full:
            self.dropped += 1

    def _write(self) -> None:
        while True:
            try:
                message = self.outbox.get(timeout=KEEPALIVE_SECONDS)
            except queue.Empty:
                try:
                    self.socket.ping()
                except WebSocketClosed:
                    return
                continue
            if message is None:
                return
            try:
                self.socket.send_json(message)
            except WebSocketClosed:
                return

    def _read(self) -> None:
        while not self.module._closing.is_set():
            try:
                message = self.socket.receive_json()
            except WebSocketClosed:
                return
            except ValueError:
                self.send({"type": "error", "error": "messages are JSON objects"})
                continue
            self.module.messages += 1
            if not isinstance(message, Mapping):
                self.send({"type": "error", "error": "messages are JSON objects"})
                continue
            self._handle(message)

    def _allowed(self, role: str, message: Mapping[str, Any]) -> bool:
        if self.caller is not None and self.caller.allows(role):
            return True
        self.send(
            {
                "type": "error",
                "id": message.get("id"),
                "error": f"needs the {role} role",
            }
        )
        return False

    def _handle(self, message: Mapping[str, Any]) -> None:
        kind = str(message.get("type") or "")
        if kind == "ping":
            self.send({"type": "pong", "id": message.get("id")})
        elif kind == "subscribe":
            self._subscribe(message)
        elif kind == "unsubscribe":
            pattern = str(message.get("pattern") or "*")
            found = self.subscriptions.pop(pattern, None)
            if found is not None:
                found.unsubscribe(self.module.bus)
            self.send({"type": "unsubscribed", "pattern": pattern, "id": message.get("id")})
        elif kind == "rpc":
            self._rpc(message)
        elif kind == "publish":
            self._publish(message)
        else:
            self.send(
                {
                    "type": "error",
                    "id": message.get("id"),
                    "error": "type must be ping, subscribe, unsubscribe, rpc, or publish",
                }
            )

    def _subscribe(self, message: Mapping[str, Any]) -> None:
        if not self._allowed("viewer", message):
            return
        pattern = str(message.get("pattern") or "*").strip() or "*"
        if pattern not in self.subscriptions:
            if len(self.subscriptions) >= MAX_SUBSCRIPTIONS:
                self.send({"type": "error", "id": message.get("id"),
                           "error": f"at most {MAX_SUBSCRIPTIONS} subscriptions"})
                return

            def deliver(event: Event) -> None:
                self.send(
                    {
                        "type": "event",
                        "name": event.name,
                        "source": event.source,
                        "at": round(event.at, 3),
                        "sequence": event.sequence,
                        "payload": event.payload,
                    }
                )

            self.subscriptions[pattern] = self.module.bus.subscribe(
                pattern, deliver, name="realtime.ws"
            )
        self.send({"type": "subscribed", "pattern": pattern, "id": message.get("id")})

    def _rpc(self, message: Mapping[str, Any]) -> None:
        request_id = message.get("id")
        if not self._allowed("operator", message):
            return
        chassis = self.module.require_context().chassis
        command = str(message.get("command") or "").strip()
        raw_args = message.get("args")
        args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
        background = bool(message.get("background"))
        status, body = 200, {}
        try:
            if chassis is None:
                raise LookupError("this process has no chassis to run commands in")
            if background:
                status, body = 202, {"job": chassis.submit_job(command, args)}
            else:
                body = {"result": chassis.dispatch_rpc(command, args)}
        except PermissionError as exc:
            status, body = 403, {"error": str(exc)}
        except (LookupError, TypeError) as exc:
            status, body = 400, {"error": str(exc)}
        except JobLimitError as exc:
            status, body = 429, {"error": str(exc)}
        except Exception as exc:
            status, body = 500, {"error": f"{type(exc).__name__}: {exc}"}
        audit = getattr(chassis, "audit", None)
        if audit is not None:
            audit.record(
                "ws-rpc", caller=self.caller, outcome=f"status {status}", command=command,
                detail=args,
            )
        self.send({"type": "result", "id": request_id, "status": status,
                   "ok": status < 400, **body})

    def _publish(self, message: Mapping[str, Any]) -> None:
        if not self._allowed("contributor", message):
            return
        if not self.module.config.get_bool("publish", True):
            self.send({"type": "error", "id": message.get("id"),
                       "error": "publishing is off (modules.realtime.publish: false)"})
            return
        name = str(message.get("event") or "").strip().lower()
        if not EVENT_NAME.match(name):
            self.send({"type": "error", "id": message.get("id"),
                       "error": "event must be 1-64 characters: a-z, 0-9, '.', '_', '-'"})
            return
        full = f"{INBOUND_PREFIX}.{name}"
        handlers = self.module.bus.publish(
            full,
            {"payload": message.get("payload"), "by": self.caller.name if self.caller else None},
            source="realtime",
        )
        self.send({"type": "published", "event": full, "handlers": handlers,
                   "id": message.get("id")})


MODULE = RealtimeModule()

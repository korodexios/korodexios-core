"""WebSocket (RFC 6455) on the control plane, standard library only.

Server-sent events carry one direction. A dashboard, a bot, or an automation that wants to
subscribe to events *and* run commands on one long-lived connection needs both; that is a
WebSocket. The control plane owns the socket: a route answers with :class:`IpcWebSocket`,
the chassis performs the upgrade (after the same token, role, Origin, and Host checks as
any other route), and the route's *session* callable then talks to a :class:`WebSocket`.

Only what a server needs is implemented: text and binary messages, fragmentation,
ping/pong, and the close handshake. Client frames must be masked, as the RFC demands;
a message larger than the limit, invalid UTF-8 in a text message, or a protocol violation
closes the connection with the matching status code.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import socket
import struct
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO, Final

__all__ = [
    "MAX_MESSAGE_BYTES",
    "WS_GUID",
    "IpcWebSocket",
    "WebSocket",
    "WebSocketClosed",
    "accept_key",
    "offered_protocols",
]

WS_GUID: Final[str] = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE_BYTES: Final[int] = 1024 * 1024
"""Largest message (after reassembly) a client may send: 1 MiB."""

OP_CONTINUATION: Final[int] = 0x0
OP_TEXT: Final[int] = 0x1
OP_BINARY: Final[int] = 0x2
OP_CLOSE: Final[int] = 0x8
OP_PING: Final[int] = 0x9
OP_PONG: Final[int] = 0xA

CLOSE_NORMAL: Final[int] = 1000
CLOSE_GOING_AWAY: Final[int] = 1001
CLOSE_PROTOCOL_ERROR: Final[int] = 1002
CLOSE_INVALID_DATA: Final[int] = 1007
CLOSE_POLICY: Final[int] = 1008
CLOSE_TOO_BIG: Final[int] = 1009

TOKEN_PROTOCOL_PREFIX: Final[str] = "kdx.token."
"""A browser cannot set headers on a WebSocket, so it may offer its token as a subprotocol."""


def accept_key(key: str) -> str:
    """Return the Sec-WebSocket-Accept value for a client's Sec-WebSocket-Key."""
    digest = hashlib.sha1((str(key).strip() + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def offered_protocols(headers: Mapping[str, str] | Any) -> list[str]:
    """Return the subprotocols a client offered, in order."""
    raw = str(headers.get("Sec-WebSocket-Protocol") or "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def protocol_token(headers: Mapping[str, str] | Any) -> str:
    """Return a token offered as the 'kdx.token.<TOKEN>' subprotocol, or ''."""
    for item in offered_protocols(headers):
        if item.startswith(TOKEN_PROTOCOL_PREFIX):
            return item[len(TOKEN_PROTOCOL_PREFIX) :]
    return ""


class WebSocketClosed(Exception):
    """Raised by :meth:`WebSocket.receive` once the connection is closed."""

    def __init__(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        super().__init__(f"websocket closed ({code}) {reason}".strip())
        self.code = int(code)
        self.reason = str(reason)


@dataclass(frozen=True, slots=True)
class IpcWebSocket:
    """What a route returns to accept a WebSocket; *session* runs on the connection.

    The session runs on the handler thread and owns the connection until it returns;
    the chassis then closes the socket. *protocols* are the subprotocols the route speaks;
    the first one the client also offered is selected.
    """

    session: Callable[[WebSocket], None]
    protocols: tuple[str, ...] = ("kdx",)


class WebSocket:
    """One server-side WebSocket over an upgraded control-plane connection.

    :meth:`receive` is for the session's own thread; :meth:`send_text`, :meth:`send_json`
    and :meth:`close` may be called from any thread - a writer lock keeps frames whole.
    """

    def __init__(
        self,
        reader: BinaryIO,
        writer: BinaryIO,
        connection: socket.socket | None = None,
        *,
        max_message: int = MAX_MESSAGE_BYTES,
        subprotocol: str = "",
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._connection = connection
        self._max = int(max_message)
        self._write_lock = threading.Lock()
        self._closed = threading.Event()
        self.subprotocol = subprotocol
        self.close_code: int | None = None

    @property
    def closed(self) -> bool:
        """Return True once either side closed the connection."""
        return self._closed.is_set()

    # -- sending -------------------------------------------------------------------

    def send_text(self, text: str) -> None:
        """Send one text message; raises WebSocketClosed after a close."""
        self._send(OP_TEXT, str(text).encode("utf-8"))

    def send_bytes(self, data: bytes) -> None:
        """Send one binary message."""
        self._send(OP_BINARY, bytes(data))

    def send_json(self, payload: Any) -> None:
        """Send *payload* as one JSON text message."""
        self.send_text(json.dumps(payload, default=str))

    def ping(self, data: bytes = b"") -> None:
        """Send a ping; the client answers with a pong the reader swallows."""
        self._send(OP_PING, bytes(data)[:125])

    def close(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        """Send a close frame (once) and wake a reader blocked on the socket."""
        if self._closed.is_set():
            return
        self._closed.set()
        self.close_code = int(code)
        body = struct.pack("!H", int(code)) + str(reason).encode("utf-8")[:120]
        with contextlib.suppress(OSError, ValueError):
            self._write_frame(OP_CLOSE, body)
        if self._connection is not None:
            with contextlib.suppress(OSError):
                self._connection.shutdown(socket.SHUT_RDWR)

    def _send(self, opcode: int, payload: bytes) -> None:
        if self._closed.is_set():
            raise WebSocketClosed(self.close_code or CLOSE_NORMAL, "already closed")
        try:
            self._write_frame(opcode, payload)
        except (OSError, ValueError) as exc:
            self._closed.set()
            raise WebSocketClosed(CLOSE_GOING_AWAY, str(exc)) from exc

    def _write_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | (opcode & 0x0F)])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 1 << 16:
            header.append(126)
            header += struct.pack("!H", length)
        else:
            header.append(127)
            header += struct.pack("!Q", length)
        with self._write_lock:
            self._writer.write(bytes(header) + payload)
            self._writer.flush()

    # -- receiving -----------------------------------------------------------------

    def receive(self) -> str | bytes:
        """Return the next message (str for text, bytes for binary).

        Pings are answered and pongs swallowed on the way. Raises WebSocketClosed when
        the client closes, the socket ends, or the client breaks the protocol.
        """
        message = bytearray()
        kind: int | None = None
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == OP_CLOSE:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else CLOSE_NORMAL
                reason = payload[2:].decode("utf-8", "replace")
                self.close(CLOSE_NORMAL)
                raise WebSocketClosed(code, reason)
            if opcode == OP_PING:
                with contextlib.suppress(WebSocketClosed):
                    self._send(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode in (OP_TEXT, OP_BINARY):
                if kind is not None:
                    self._fail(CLOSE_PROTOCOL_ERROR, "a new message began inside a fragment")
                kind = opcode
            elif opcode == OP_CONTINUATION:
                if kind is None:
                    self._fail(CLOSE_PROTOCOL_ERROR, "continuation without a message")
            else:
                self._fail(CLOSE_PROTOCOL_ERROR, f"unknown opcode {opcode}")
            message += payload
            if len(message) > self._max:
                self._fail(CLOSE_TOO_BIG, f"message over {self._max} bytes")
            if fin:
                break
        if kind == OP_BINARY:
            return bytes(message)
        try:
            return message.decode("utf-8")
        except UnicodeDecodeError:
            self._fail(CLOSE_INVALID_DATA, "text message is not UTF-8")
        raise AssertionError("unreachable")  # pragma: no cover - _fail always raises

    def receive_json(self) -> Any:
        """Return the next message decoded as JSON; a non-JSON message raises ValueError."""
        message = self.receive()
        text = message.decode("utf-8") if isinstance(message, bytes) else message
        return json.loads(text)

    def _fail(self, code: int, reason: str) -> None:
        self.close(code, reason)
        raise WebSocketClosed(code, reason)

    def _read_exact(self, count: int) -> bytes:
        data = b""
        while len(data) < count:
            try:
                chunk = self._reader.read(count - len(data))
            except (OSError, ValueError) as exc:
                self._closed.set()
                raise WebSocketClosed(CLOSE_GOING_AWAY, str(exc)) from exc
            if not chunk:
                self._closed.set()
                raise WebSocketClosed(CLOSE_GOING_AWAY, "connection ended")
            data += chunk
        return data

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exact(2)
        fin = bool(first & 0x80)
        if first & 0x70:
            self._fail(CLOSE_PROTOCOL_ERROR, "reserved bits set without an extension")
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        if opcode >= OP_CLOSE and (length > 125 or not fin):
            self._fail(CLOSE_PROTOCOL_ERROR, "control frames are short and unfragmented")
        if not masked:
            self._fail(CLOSE_PROTOCOL_ERROR, "client frames must be masked")
        if length > self._max:
            self._fail(CLOSE_TOO_BIG, f"frame over {self._max} bytes")
        mask = self._read_exact(4)
        data = self._read_exact(length)
        if not length:
            return fin, opcode, b""
        # One big-integer XOR instead of a Python loop per byte: a 1 MiB frame stays cheap.
        key = (mask * (length // 4 + 1))[:length]
        plain = int.from_bytes(data, "big") ^ int.from_bytes(key, "big")
        return fin, opcode, plain.to_bytes(length, "big")

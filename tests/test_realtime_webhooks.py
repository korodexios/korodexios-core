"""WebSocket transport, the realtime channel, and signed webhooks in and out."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.events.bus import Event
from korodexios_core.kernel.chassis import Chassis, ChassisOptions
from korodexios_core.kernel.cli_contract import CliRequest
from korodexios_core.kernel.ipc import presented_token
from korodexios_core.kernel.websocket import (
    WebSocket,
    WebSocketClosed,
    accept_key,
    offered_protocols,
)
from korodexios_core.modules.realtime.module import WS_PATH
from korodexios_core.modules.webhooks.module import (
    INBOUND_PATH,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    WebhooksModule,
    event_body,
    parse_target,
    sign,
    verify,
)
from korodexios_core.paths import AppPaths

# -- frames -----------------------------------------------------------------------------


def client_frame(opcode: int, payload: bytes, *, fin: bool = True, masked: bool = True) -> bytes:
    """Encode one frame the way a client must: masked."""
    head = bytearray([(0x80 if fin else 0) | opcode])
    length = len(payload)
    mark = 0x80 if masked else 0
    if length < 126:
        head.append(mark | length)
    elif length < 1 << 16:
        head.append(mark | 126)
        head += struct.pack("!H", length)
    else:
        head.append(mark | 127)
        head += struct.pack("!Q", length)
    if not masked:
        return bytes(head) + payload
    mask = os.urandom(4)
    return bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def server_frames(raw: bytes) -> list[tuple[int, bytes]]:
    """Decode unmasked server frames from *raw*."""
    frames, index = [], 0
    while index < len(raw):
        opcode, length = raw[index] & 0x0F, raw[index + 1] & 0x7F
        index += 2
        if length == 126:
            length = struct.unpack("!H", raw[index : index + 2])[0]
            index += 2
        elif length == 127:
            length = struct.unpack("!Q", raw[index : index + 8])[0]
            index += 8
        frames.append((opcode, raw[index : index + length]))
        index += length
    return frames


def socket_over(data: bytes, *, max_message: int = 1024) -> tuple[WebSocket, io.BytesIO]:
    out = io.BytesIO()
    return WebSocket(io.BytesIO(data), out, None, max_message=max_message), out


def test_accept_key_matches_the_rfc_example() -> None:
    assert accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    assert offered_protocols({"Sec-WebSocket-Protocol": "kdx, kdx.token.abc"}) == [
        "kdx", "kdx.token.abc"
    ]
    assert presented_token({"Sec-WebSocket-Protocol": "kdx, kdx.token.abc"}) == "abc"


def test_messages_fragments_pings_and_close() -> None:
    data = (
        client_frame(0x1, "héllo".encode())
        + client_frame(0x9, b"hi")  # ping, answered with a pong
        + client_frame(0x1, b"frag", fin=False)
        + client_frame(0xA, b"")  # a pong in between is swallowed
        + client_frame(0x0, b"ment")
        + client_frame(0x2, b"\x00\x01")
        + client_frame(0x1, b"x" * 300)
        + client_frame(0x8, struct.pack("!H", 1000) + b"bye")
    )
    ws, out = socket_over(data)
    assert ws.receive() == "héllo"
    assert ws.receive() == "fragment"
    assert ws.receive() == b"\x00\x01"
    assert ws.receive() == "x" * 300
    with pytest.raises(WebSocketClosed) as closed:
        ws.receive()
    assert closed.value.code == 1000 and closed.value.reason == "bye"
    assert ws.closed
    frames = server_frames(out.getvalue())
    assert frames[0] == (0xA, b"hi")
    assert frames[-1][0] == 0x8
    with pytest.raises(WebSocketClosed):
        ws.send_text("after close")


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (client_frame(0x1, b"x", masked=False), 1002),
        (client_frame(0x1, b"x" * 2000), 1009),
        (client_frame(0x1, b"\xff\xfe"), 1007),
        (client_frame(0x0, b"x"), 1002),
        (client_frame(0x1, b"a", fin=False) + client_frame(0x1, b"b"), 1002),
        (client_frame(0x3, b"x"), 1002),
        (client_frame(0x9, b"x" * 130), 1002),
        (bytes([0xC1, 0x80]) + b"\x00" * 4, 1002),
        (b"\x81", 1001),
    ],
)
def test_protocol_violations_close_with_the_matching_code(data: bytes, code: int) -> None:
    ws, _out = socket_over(data)
    with pytest.raises(WebSocketClosed) as closed:
        ws.receive()
    assert closed.value.code == code


def test_large_frames_and_json() -> None:
    big = json.dumps({"x": "y" * 70000}).encode()
    ws, out = socket_over(client_frame(0x1, big) + client_frame(0x1, b"[1]"), max_message=1 << 20)
    assert ws.receive_json()["x"] == "y" * 70000
    ws.send_json({"ok": True})
    ws.send_bytes(b"z" * 200)
    ws.send_text("q" * 70000)
    ws.ping(b"p")
    frames = server_frames(out.getvalue())
    assert json.loads(frames[0][1]) == {"ok": True}
    assert frames[1] == (0x2, b"z" * 200)
    assert len(frames[2][1]) == 70000 and frames[3] == (0x9, b"p")
    assert ws.receive_json() == [1]


# -- a live chassis ---------------------------------------------------------------------


@pytest.fixture()
def live(project_root: Path) -> Any:
    paths = AppPaths.discover(root=str(project_root), environ={})
    chassis = Chassis.create(
        ChassisOptions(
            home=paths.root,
            environ={},
            cli_overrides={
                "core.log.file_enabled": False,
                "core.ipc.enabled": True,
                "core.ipc.port": 0,
            },
            install_signals=False,
        )
    )
    chassis.initialize()
    chassis.start(ipc=True)
    try:
        yield chassis
    finally:
        chassis.shutdown()


class Client:
    """A minimal WebSocket client over a raw socket."""

    def __init__(self, port: int, headers: dict[str, str]) -> None:
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        key = headers.get("Sec-WebSocket-Key", "")
        lines = [
            f"GET {WS_PATH} HTTP/1.1",
            f"Host: 127.0.0.1:{port}",
            *(f"{name}: {value}" for name, value in headers.items()),
        ]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        self.reader = self.sock.makefile("rb")
        self.status = int(self.reader.readline().split()[1])
        self.headers: dict[str, str] = {}
        while True:
            line = self.reader.readline().decode().strip()
            if not line:
                break
            name, _, value = line.partition(":")
            self.headers[name.strip().lower()] = value.strip()
        self.key = key

    def send(self, message: Any) -> None:
        self.sock.sendall(client_frame(0x1, json.dumps(message).encode()))

    def receive(self) -> Any:
        while True:
            first, second = self.reader.read(2)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self.reader.read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.reader.read(8))[0]
            payload = self.reader.read(length)
            opcode = first & 0x0F
            if opcode == 0x8:
                raise WebSocketClosed(struct.unpack("!H", payload[:2])[0])
            if opcode == 0x1:
                return json.loads(payload)

    def until(self, kind: str) -> Any:
        for _ in range(50):
            message = self.receive()
            if message.get("type") == kind:
                return message
        raise AssertionError(f"no {kind} message")

    def close(self) -> None:
        self.sock.close()


def upgrade_headers(token: str | None = None, **extra: str) -> dict[str, str]:
    headers = {
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Key": base64.b64encode(os.urandom(16)).decode(),
        "Sec-WebSocket-Version": "13",
    }
    if token is not None:
        headers["X-Kdx-Token"] = token
    headers.update(extra)
    return headers


def test_websocket_subscribes_runs_commands_and_publishes(live: Chassis) -> None:
    port = live.ipc.discovery.port  # type: ignore[union-attr]
    client = Client(port, upgrade_headers(live.ipc.discovery_token))  # type: ignore[union-attr]
    try:
        assert client.status == 101
        expected = accept_key(client.key)
        assert client.headers["sec-websocket-accept"] == expected
        hello = client.until("hello")
        assert hello["user"] == "owner" and hello["role"] == "admin"
        client.send({"type": "subscribe", "pattern": "inbound.**", "id": 1})
        assert client.until("subscribed")["pattern"] == "inbound.**"
        client.send({"type": "publish", "event": "deploy.done", "payload": {"v": 2}, "id": 2})
        event = client.until("event")
        assert event["name"] == "inbound.deploy.done" and event["payload"]["payload"] == {"v": 2}
        client.send({"type": "rpc", "id": 3, "command": "heartbeat status"})
        result = client.until("result")
        assert result["id"] == 3 and result["ok"] and result["result"]["exit_code"] == 0
        client.send({"type": "rpc", "id": 4, "command": "nosuch"})
        assert client.until("result")["status"] == 400
        client.send({"type": "rpc", "id": 5, "command": "heartbeat status", "background": True})
        assert client.until("result")["status"] == 202
        client.send({"type": "ping", "id": 6})
        assert client.until("pong")["id"] == 6
        client.send({"type": "publish", "event": "BAD NAME"})
        assert "event must be" in client.until("error")["error"]
        client.send({"type": "what"})
        assert "type must be" in client.until("error")["error"]
        client.send(["not", "an", "object"])
        assert "JSON objects" in client.until("error")["error"]
        client.sock.sendall(client_frame(0x1, b"{broken"))
        assert "JSON objects" in client.until("error")["error"]
        client.send({"type": "unsubscribe", "pattern": "inbound.**"})
        assert client.until("unsubscribed")["pattern"] == "inbound.**"
        module = live.registry.get("realtime").module  # type: ignore[union-attr]
        assert module.health()["open"] == 1
    finally:
        client.close()


def test_a_viewer_may_watch_but_not_act(live: Chassis) -> None:
    port = live.ipc.discovery.port  # type: ignore[union-attr]
    token = live.users.add("watcher", "viewer")  # type: ignore[union-attr]
    offered = {"Sec-WebSocket-Protocol": f"kdx, kdx.token.{token}"}
    client = Client(port, upgrade_headers(None, **offered))
    try:
        assert client.status == 101 and client.headers["sec-websocket-protocol"] == "kdx"
        assert client.until("hello")["role"] == "viewer"
        client.send({"type": "rpc", "id": 1, "command": "heartbeat status"})
        assert "operator" in client.until("error")["error"]
        client.send({"type": "publish", "event": "x"})
        assert "contributor" in client.until("error")["error"]
        for index in range(16):
            client.send({"type": "subscribe", "pattern": f"p{index}"})
            client.until("subscribed")
        client.send({"type": "subscribe", "pattern": "one.more"})
        assert "at most" in client.until("error")["error"]
    finally:
        client.close()


def test_the_handshake_is_checked(live: Chassis) -> None:
    port = live.ipc.discovery.port  # type: ignore[union-attr]
    token = live.ipc.discovery_token  # type: ignore[union-attr]
    assert Client(port, {"X-Kdx-Token": token}).status == 426
    assert Client(port, upgrade_headers(token, **{"Sec-WebSocket-Version": "8"})).status == 426
    assert Client(port, upgrade_headers(None)).status == 401
    foreign = upgrade_headers(token, Origin="http://evil.example")
    assert Client(port, foreign).status == 403


def test_stop_closes_open_sockets(live: Chassis) -> None:
    port = live.ipc.discovery.port  # type: ignore[union-attr]
    client = Client(port, upgrade_headers(live.ipc.discovery_token))  # type: ignore[union-attr]
    client.until("hello")
    live.registry.get("realtime").module.stop()  # type: ignore[union-attr]
    with pytest.raises((WebSocketClosed, OSError, ValueError)):
        for _ in range(5):
            client.receive()
    client.close()


# -- webhooks ---------------------------------------------------------------------------


def test_targets_signatures_and_bodies() -> None:
    assert parse_target("https://n8n.example/webhook/abc").pattern == "*"
    target = parse_target("core.job.** http://127.0.0.1:5678/hook")
    assert target.pattern == "core.job.**" and target.masked() == "http://127.0.0.1:5678/..."
    for bad in ("", "ftp://x/y", "a b c", "https://"):
        with pytest.raises(ValueError):
            parse_target(bad)
    body = event_body(Event(name="a.b", payload={"x": 1}, source="s", at=1.0), "d1")
    assert json.loads(body)["delivery"] == "d1"
    stamp = str(int(time.time()))
    signature = sign("k", stamp, body)
    assert signature.startswith("sha256=")
    assert verify("k", stamp, body, signature)
    assert not verify("other", stamp, body, signature)
    assert not verify("k", str(int(time.time()) - 3600), body, sign("k", "0", body))
    assert not verify("k", "soon", body, signature)


class Receiver:
    """A local HTTP endpoint that records deliveries and answers a scripted status."""

    def __init__(self, statuses: list[int] | None = None) -> None:
        self.received: list[tuple[dict[str, str], bytes]] = []
        self.statuses = list(statuses or [])
        self.arrived = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.received.append((dict(self.headers.items()), body))
                status = outer.statuses.pop(0) if outer.statuses else 200
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                if status == 200:
                    outer.arrived.set()

            def log_message(self, *_args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def webhook_chassis(project_root: Path, targets: list[str], **extra: Any) -> Chassis:
    overrides = {
        "core.log.file_enabled": False,
        "core.ipc.enabled": True,
        "core.ipc.port": 0,
        "modules.webhooks.targets": targets,
        "modules.webhooks.secret": "s3cret",
        "modules.webhooks.retries": 3,
        **extra,
    }
    chassis = Chassis.create(
        ChassisOptions(home=project_root, environ={}, cli_overrides=overrides,
                       install_signals=False)
    )
    chassis.initialize()
    return chassis


def module_of(chassis: Chassis) -> WebhooksModule:
    module = chassis.registry.get("webhooks").module  # type: ignore[union-attr]
    assert isinstance(module, WebhooksModule)
    return module


def test_matching_events_are_delivered_signed(project_root: Path) -> None:
    receiver = Receiver()
    chassis = webhook_chassis(project_root, [f"deploy.** {receiver.url}"])
    chassis.start(ipc=True)
    try:
        chassis.bus.publish("other.event", {"x": 0}, source="test")
        chassis.bus.publish("deploy.done", {"version": 7}, source="test")
        assert receiver.arrived.wait(10)
        deadline = time.monotonic() + 5
        while module_of(chassis).delivered < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        headers, body = receiver.received[-1]
        payload = json.loads(body)
        assert payload["event"] == "deploy.done" and payload["payload"] == {"version": 7}
        assert verify("s3cret", headers[TIMESTAMP_HEADER], body, headers[SIGNATURE_HEADER])
        assert len(receiver.received) == 1  # 'other.event' matched no target
        health = module_of(chassis).health()
        assert health["delivered"] == 1 and health["targets"] == 1
        assert "kdx-webhooks" in module_of(chassis).workers()
        rows = {item["name"]: item for item in
                (check.as_dict() for check in module_of(chassis).diagnostics())}
        assert "plain-http" not in rows  # loopback http is fine
    finally:
        chassis.shutdown()
        receiver.close()


def test_retries_on_5xx_and_gives_up_on_4xx(project_root: Path) -> None:
    receiver = Receiver([500, 200, 404])
    chassis = webhook_chassis(project_root, [receiver.url])
    module = module_of(chassis)
    try:
        target = parse_target(receiver.url)
        event = Event(name="x", payload=None, source="t", at=time.time())
        assert module.deliver(target, event) == (True, 200, "")
        ok, status, error = module.deliver(target, event)
        assert (ok, status) == (False, 404) and "404" in error
        assert len(receiver.received) == 3  # 500, then 200; the 404 was not retried
        dead = parse_target("http://127.0.0.1:9/never")
        assert module.deliver(dead, event)[0] is False
        assert module.health()["failed"] == 2
    finally:
        chassis.shutdown()
        receiver.close()


def test_inbound_route_publishes_on_the_bus(project_root: Path) -> None:
    chassis = webhook_chassis(project_root, [])
    chassis.start(ipc=True)
    seen: list[Event] = []
    chassis.bus.subscribe("inbound.**", seen.append)
    base = chassis.ipc.discovery.base_url()  # type: ignore[union-attr]
    token = chassis.users.add("n8n", "contributor")  # type: ignore[union-attr]

    def post(query: str, body: Any, auth: str = token) -> int:
        request = urllib.request.Request(
            base + INBOUND_PATH + query, data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {auth}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return int(response.status)
        except urllib.error.HTTPError as exc:
            return int(exc.code)

    try:
        assert post("?event=order.paid", {"id": 9}) == 202
        assert post("", {"event": "lead.new", "payload": {"name": "Ada"}}) == 202
        assert post("?event=Bad%20Name", {}) == 400
        assert post("", {"no": "event"}) == 400
        assert post("?event=x", {}, auth="wrong") == 401
        viewer = chassis.users.add("eyes", "viewer")  # type: ignore[union-attr]
        assert post("?event=x", {}, auth=viewer) == 403
        names = [event.name for event in seen]
        assert names == ["inbound.order.paid", "inbound.lead.new"]
        assert seen[0].payload == {"payload": {"id": 9}, "by": "n8n"}
        assert module_of(chassis).health()["received"] == 2
    finally:
        chassis.shutdown()


def test_diagnostics_and_cli(project_root: Path, capsys) -> None:
    receiver = Receiver([200, 500, 500, 500])
    chassis = webhook_chassis(
        project_root, ["https://hooks.example/x", "not a url at all", receiver.url],
        **{"modules.webhooks.secret": "", "modules.webhooks.retries": 1},
    )
    module = module_of(chassis)

    def request(current: Chassis, **options: Any) -> CliRequest:
        return CliRequest(context=current.context, command="webhooks",
                          namespace=argparse.Namespace(**options))

    try:
        names = [check.name for check in module.diagnostics()]
        assert "target" in names and "unsigned" in names
        assert module._list_command(request(chassis, as_json=True)) == 3
        assert json.loads(capsys.readouterr().out)["invalid"]
    finally:
        chassis.shutdown()
    # The module object is the module's singleton: each chassis below rebinds it.
    plain = webhook_chassis(project_root, ["http://nas.lan/x"])
    try:
        assert "plain-http" in [check.name for check in module_of(plain).diagnostics()]
    finally:
        plain.shutdown()
    empty = webhook_chassis(project_root, [])
    try:
        assert module_of(empty)._list_command(request(empty, as_json=False)) == 0
        assert module_of(empty)._test_command(request(empty, event="x", as_json=False)) == 0
        assert "no webhook targets" in capsys.readouterr().out
    finally:
        empty.shutdown()
    live = webhook_chassis(project_root, [receiver.url], **{"modules.webhooks.retries": 1})
    try:
        current = module_of(live)
        assert current._list_command(request(live, as_json=False)) == 0
        assert "http://127.0.0.1" in capsys.readouterr().out
        assert current._test_command(request(live, event="webhooks.test", as_json=True)) == 0
        assert json.loads(capsys.readouterr().out)["results"][0]["ok"] is True
        assert current._test_command(request(live, event="webhooks.test", as_json=False)) == 1
    finally:
        live.shutdown()
        receiver.close()


def test_secret_targets_are_masked_in_config_reports(project_root: Path) -> None:
    chassis = webhook_chassis(project_root, ["https://n8n.example/webhook/very-secret"])
    try:
        rows = {key: value for key, value, _ in chassis.config.describe_rows(redact=True)}
        assert "very-secret" not in rows["modules.webhooks.targets"]
        assert "s3cret" not in rows["modules.webhooks.secret"]
    finally:
        chassis.shutdown()

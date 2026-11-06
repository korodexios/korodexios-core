"""Control-plane hardening: acting routes keep their token, browsers cannot drive the daemon."""

from __future__ import annotations

import http.client
import json
import socket
from pathlib import Path

import pytest

from korodexios_core.kernel import ipc as ipc_module
from korodexios_core.kernel.ipc import (
    RELOAD_PATH,
    RPC_PATH,
    STATUS_PATH,
    TOKEN_HEADER,
    IpcOptions,
    IpcServer,
)


def start(
    tmp_path: Path,
    *,
    host: str = "127.0.0.1",
    token: str = "sekret",
    auth: bool = True,
) -> tuple[IpcServer, list[str]]:
    """Start a control plane whose RPC hook records every command it is asked to run."""
    ran: list[str] = []

    def rpc(command: str, args: object) -> dict[str, object]:
        ran.append(command)
        return {"command": command}

    server = IpcServer(
        status_fn=lambda: {"app": {"state": "running"}},
        shutdown_fn=lambda: None,
        state_fn=lambda: "running",
        options=IpcOptions(
            host=host,
            port=0,
            discovery_path=tmp_path / "chassis.ipc",
            token=token,
            auth=auth,
        ),
        rpc_fn=rpc,
        reload_fn=lambda: {"changed": []},
    )
    server.start()
    return server, ran


def raw_call(
    server: IpcServer,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, object]]:
    """Send one request with exactly the headers given, Host included."""
    assert server.discovery is not None
    connection = http.client.HTTPConnection("127.0.0.1", server.discovery.port, timeout=5)
    try:
        connection.putrequest(method, path, skip_host=True)
        for key, value in (headers or {}).items():
            connection.putheader(key, value)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        return response.status, (json.loads(raw) if raw else {})
    finally:
        connection.close()


def test_auth_off_still_refuses_an_anonymous_rpc(tmp_path: Path) -> None:
    server, ran = start(tmp_path, auth=False)
    port = server.discovery.port  # type: ignore[union-attr]
    host = {"Host": f"127.0.0.1:{port}"}
    body = json.dumps({"command": "backup run", "args": {"source": "/"}}).encode()
    try:
        assert raw_call(server, STATUS_PATH, headers=host)[0] == 200
        code, payload = raw_call(server, RPC_PATH, method="POST", headers=host, body=body)
        assert code == 401
        assert TOKEN_HEADER in str(payload["error"])
        assert raw_call(server, RELOAD_PATH, method="POST", headers=host)[0] == 401
        assert ran == []
        authed = {**host, TOKEN_HEADER: "sekret"}
        assert raw_call(server, RPC_PATH, method="POST", headers=authed, body=body)[0] == 200
        assert ran == ["backup run"]
    finally:
        server.stop()


def test_an_empty_token_matches_nothing(tmp_path: Path) -> None:
    server, _ = start(tmp_path, token="")
    try:
        assert server.token_matches("") is False
        assert server.token_matches(None) is False
    finally:
        server.stop()


def test_a_foreign_origin_cannot_post_even_with_auth_off(tmp_path: Path) -> None:
    server, ran = start(tmp_path, auth=False)
    port = server.discovery.port  # type: ignore[union-attr]
    headers = {
        "Host": f"127.0.0.1:{port}",
        "Origin": "http://evil.example",
        "Content-Type": "text/plain",
        TOKEN_HEADER: "sekret",
    }
    try:
        code, payload = raw_call(
            server, RPC_PATH, method="POST", headers=headers, body=b'{"command": "x"}'
        )
        assert code == 403
        assert "own origin" in str(payload["error"])
        assert "hint" in payload
        assert ran == []
        null_origin = {**headers, "Origin": "null"}
        assert raw_call(server, STATUS_PATH, headers=null_origin)[0] == 403
    finally:
        server.stop()


def test_the_daemons_own_origin_is_served(tmp_path: Path) -> None:
    server, _ = start(tmp_path)
    port = server.discovery.port  # type: ignore[union-attr]
    try:
        for host in (f"127.0.0.1:{port}", f"localhost:{port}"):
            headers = {"Host": host, "Origin": f"http://{host}", TOKEN_HEADER: "sekret"}
            assert raw_call(server, STATUS_PATH, headers=headers)[0] == 200
    finally:
        server.stop()


def test_dns_rebinding_host_is_refused_on_loopback(tmp_path: Path) -> None:
    server, _ = start(tmp_path)
    try:
        code, payload = raw_call(server, "/health", headers={"Host": "rebind.example:18123"})
        assert code == 403
        assert "loopback" in str(payload["error"])
        assert raw_call(server, "/health", headers={"Host": "[::1]:18123"})[0] == 200
        assert raw_call(server, "/health", headers={"Host": "localhost"})[0] == 200
        assert raw_call(server, "/health", headers={})[0] == 200  # HTTP/1.0 style, no Host
    finally:
        server.stop()


def test_a_lan_bind_accepts_any_host_name(tmp_path: Path) -> None:
    server, _ = start(tmp_path, host="0.0.0.0")
    try:
        assert raw_call(server, "/health", headers={"Host": "nas.lan:18123"})[0] == 200
    finally:
        server.stop()


@pytest.mark.parametrize(
    ("header", "name"),
    [
        ("127.0.0.1:18123", "127.0.0.1"),
        ("LocalHost", "localhost"),
        ("[::1]:18123", "::1"),
        ("::1", "::1"),
        ("nas.lan", "nas.lan"),
    ],
)
def test_host_header_names_are_parsed(header: str, name: str) -> None:
    assert ipc_module._host_name(header) == name


def test_a_stalled_connection_is_dropped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ipc_module._ControlHandler, "timeout", 0.2)
    server, _ = start(tmp_path)
    port = server.discovery.port  # type: ignore[union-attr]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(b"GET /health HTTP/1.1\r\n")  # never finishes the headers
            assert client.recv(1024) == b""  # the server hung up instead of waiting
        assert raw_call(server, "/health", headers={})[0] == 200
    finally:
        server.stop()

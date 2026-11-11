"""Console token memory (policy, cookie session, one shared helper) and live allowed_hosts."""

from __future__ import annotations

import http.client
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.kernel.chassis import Chassis, ChassisOptions
from korodexios_core.kernel.ipc import TOKEN_HEADER, IpcRequest
from korodexios_core.modules.web_console.module import (
    STATIC_DIR,
)
from korodexios_core.modules.web_console.token_session import (
    SESSION_PATH,
    TOKEN_POLICY_PATH,
    token_policy,
)
from korodexios_core.paths import AppPaths

TOKEN = "t0ken-for-tests"


def build(project_root: Path, **overrides: Any) -> Chassis:
    paths = AppPaths.discover(root=str(project_root), environ={})
    chassis = Chassis.create(
        ChassisOptions(
            home=paths.root,
            environ={},
            cli_overrides={
                "core.log.file_enabled": False,
                "core.ipc.enabled": True,
                "core.ipc.port": 0,
                "core.ipc.token": TOKEN,
                **overrides,
            },
            install_signals=False,
        )
    )
    chassis.initialize()
    chassis.start()
    return chassis


def call(
    chassis: Chassis, method: str, path: str, headers: dict[str, str] | None = None,
    body: Any = None,
) -> tuple[int, dict[str, Any], http.client.HTTPResponse]:
    assert chassis.ipc is not None and chassis.ipc.discovery is not None
    connection = http.client.HTTPConnection("127.0.0.1", chassis.ipc.discovery.port, timeout=5)
    sent = dict(headers or {})
    payload = None
    if body is not None:
        payload = json.dumps(body)
        sent["Content-Type"] = "application/json"
    connection.request(method, path, body=payload, headers=sent)
    response = connection.getresponse()
    data = json.loads(response.read().decode("utf-8") or "{}")
    return response.status, data, response


def test_the_policy_defaults_to_remembering_for_good(project_root: Path) -> None:
    chassis = build(project_root)
    try:
        status, policy, _ = call(chassis, "GET", TOKEN_POLICY_PATH)  # no token needed
        assert status == 200
        assert policy == {"token_store": "always", "ttl_seconds": 0, "cookie": False}
    finally:
        chassis.shutdown()


@pytest.mark.parametrize(
    ("store", "ttl", "expected"),
    [
        ("ttl", "45m", ("ttl", 2700)),
        ("ttl", "", ("ttl", 12 * 3600)),
        ("always", "7d", ("always", 7 * 86400)),
        ("tab", "7d", ("tab", 0)),
        ("none", "", ("none", 0)),
        ("nonsense", "soon", ("always", 0)),
    ],
)
def test_the_policy_follows_the_config(
    project_root: Path, store: str, ttl: str, expected: tuple[str, int]
) -> None:
    chassis = build(
        project_root, **{"core.ipc.ui_token_store": store, "core.ipc.ui_token_ttl": ttl}
    )
    try:
        policy = token_policy(chassis.config)
        assert (policy["token_store"], policy["ttl_seconds"]) == expected
    finally:
        chassis.shutdown()


def test_the_cookie_session_replaces_the_token_without_script_access(project_root: Path) -> None:
    chassis = build(project_root, **{"core.ipc.ui_token_cookie": True})
    try:
        assert call(chassis, "GET", SESSION_PATH)[0] == 401
        assert call(chassis, "POST", SESSION_PATH, {TOKEN_HEADER: "wrong"}, {})[0] == 401
        status, _, response = call(chassis, "POST", SESSION_PATH, {TOKEN_HEADER: TOKEN}, {})
        assert status == 200
        cookie = response.getheader("Set-Cookie") or ""
        assert cookie.startswith("kdx_session=") and "HttpOnly" in cookie
        assert "SameSite=Lax" in cookie and "Max-Age=" in cookie
        pair = cookie.split(";", 1)[0]
        assert call(chassis, "GET", SESSION_PATH, {"Cookie": pair})[0] == 200
        assert call(chassis, "GET", "/api/v1/status", {"Cookie": pair})[0] == 200
        assert call(chassis, "GET", "/api/v1/status", {"Cookie": "kdx_session=bad"})[0] == 401
        _, _, cleared = call(chassis, "DELETE", SESSION_PATH)
        assert "Max-Age=0" in (cleared.getheader("Set-Cookie") or "")
    finally:
        chassis.shutdown()


def test_a_session_cookie_is_capped_by_the_ttl_and_dropped_for_a_tab(project_root: Path) -> None:
    chassis = build(
        project_root,
        **{"core.ipc.ui_token_cookie": True, "core.ipc.ui_token_ttl": "1h"},
    )
    try:
        _, _, capped = call(
            chassis, "POST", SESSION_PATH, {TOKEN_HEADER: TOKEN}, {"seconds": 99999}
        )
        assert "Max-Age=3600" in (capped.getheader("Set-Cookie") or "")
        _, _, tab = call(
            chassis, "POST", SESSION_PATH, {TOKEN_HEADER: TOKEN}, {"persistent": False}
        )
        assert "Max-Age" not in (tab.getheader("Set-Cookie") or "")
    finally:
        chassis.shutdown()


def test_the_cookie_session_is_refused_while_the_option_is_off(project_root: Path) -> None:
    chassis = build(project_root)
    try:
        assert call(chassis, "POST", SESSION_PATH, {TOKEN_HEADER: TOKEN}, {})[0] == 403
        assert call(chassis, "GET", "/api/v1/status", {"Cookie": f"kdx_session={TOKEN}"})[0] == 401
    finally:
        chassis.shutdown()


def test_both_consoles_share_one_token_helper() -> None:
    helper = (STATIC_DIR / "token-store.js").read_text(encoding="utf-8")
    assert "localStorage" in helper and "sessionStorage" in helper
    for page, script in (("console.html", "app.js"), ("shell.html", "shell.js")):
        html = (STATIC_DIR / page).read_text(encoding="utf-8")
        assert html.index("token-store.js") < html.index(script)
        assert 'id="token-keep"' in html and 'id="token-forget"' in html
        code = (STATIC_DIR / script).read_text(encoding="utf-8")
        assert "sessionStorage" not in code and "localStorage.setItem(STORE" not in code
        assert "KdxToken.remember" in code


def test_reload_applies_allowed_hosts_without_a_restart(project_root: Path) -> None:
    chassis = build(project_root, **{"core.ipc.host": "0.0.0.0"})
    try:
        assert chassis.ipc is not None
        assert chassis.ipc.cross_origin_refusal("nas.lan:18123", None) is not None
        local = chassis.paths.config_file
        local.write_text("core:\n  ipc:\n    allowed_hosts: [nas.lan]\n", encoding="utf-8")
        summary = chassis.reload_config()
        assert "core.ipc.allowed_hosts" in summary["changed"]
        assert chassis.ipc.cross_origin_refusal("nas.lan:18123", None) is None
        assert chassis.ipc.cross_origin_refusal("evil.example:18123", None) is not None
        # Listing a first domain keeps this machine's own name working.
        own = socket.gethostname().lower()
        assert chassis.ipc.cross_origin_refusal(f"{own}:18123", None) is None
        local.write_text("core:\n  ipc:\n    allowed_hosts: []\n", encoding="utf-8")
        chassis.reload_config()
        assert chassis.ipc.cross_origin_refusal("nas.lan:18123", None) is not None
    finally:
        chassis.shutdown()


def test_the_request_carries_headers_for_the_cookie_route() -> None:
    request = IpcRequest(method="GET", path=SESSION_PATH, query={}, headers={"Cookie": "a=b"})
    assert request.headers == {"Cookie": "a=b"}

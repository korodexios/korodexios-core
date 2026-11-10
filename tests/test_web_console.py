"""Web console: the page, its read-only helpers, and the 'kdx ui' launcher."""

from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from korodexios_core.cli.entry import main
from korodexios_core.constants import ExitCode
from korodexios_core.kernel.chassis import Chassis
from korodexios_core.kernel.cli_contract import CliArgument, CliRequest
from korodexios_core.kernel.ipc import IpcDiscovery, IpcRequest
from korodexios_core.modules.web_console import module as web_module
from korodexios_core.modules.web_console.module import (
    CATALOG_PATH,
    CONFIG_PATH,
    EVENTS_PATH,
    I18N_PATH,
    LOGS_PATH,
    STATIC_DIR,
    WebConsoleModule,
    console_url,
    describe_argument,
)

from .test_ipc import base_args, http_call, ipc_chassis


def console_of(chassis: Chassis) -> WebConsoleModule:
    record = chassis.registry.get("web_console")
    assert record is not None
    module = record.module
    assert isinstance(module, WebConsoleModule)
    return module


def request_for(path: str, **query: str) -> IpcRequest:
    return IpcRequest(
        method="GET", path=path, query={key: [value] for key, value in query.items()}
    )


def test_static_files_ship_with_the_module() -> None:
    for name in ("console.html", "shell.html", "app.js", "app.css"):
        assert (STATIC_DIR / name).is_file(), name
    page = (STATIC_DIR / "console.html").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in page
    assert "script-src 'self'" in page
    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script  # daemon data is only ever written as text


def test_describe_argument_maps_every_kind() -> None:
    flag = describe_argument(CliArgument(("--json",), dest="as_json", action="store_true"))
    assert (flag["dest"], flag["kind"], flag["positional"]) == ("as_json", "flag", False)
    choice = describe_argument(
        CliArgument(("--section",), choices=("os", "cpu"), default="os", help="pick")
    )
    assert choice["kind"] == "str"
    assert choice["choices"] == ["os", "cpu"]
    assert choice["default"] == "os"
    positional = describe_argument(CliArgument(("target",), type="path", default=Path("x")))
    assert positional["positional"] is True
    assert positional["kind"] == "path"
    assert positional["default"] == "x"


def test_console_url_keeps_the_token_in_the_fragment() -> None:
    discovery = IpcDiscovery.remote("127.0.0.1:18123", token="abc")
    assert console_url(discovery) == "http://127.0.0.1:18123/ui/#token=abc"
    anonymous = IpcDiscovery.remote("127.0.0.1:18123", token="")
    assert console_url(anonymous) == "http://127.0.0.1:18123/ui/"


def test_catalog_lists_module_commands_but_not_the_launcher(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        paths = [item["path"] for item in console_of(chassis).catalog()["commands"]]
        assert "heartbeat status" in paths
        assert "system info" in paths
        assert "backup run" in paths
        assert "ui" not in paths
        assert "heartbeat" not in paths  # a group is not runnable
    finally:
        chassis.shutdown()


def test_config_route_masks_secrets(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.config = chassis.config.with_cli_overrides({"core.ipc.token": "hunter2"})
    chassis.context.config = chassis.config
    chassis.initialize()
    try:
        entries = console_of(chassis)._config_route(request_for(CONFIG_PATH))["entries"]
        by_key = {entry["key"]: entry for entry in entries}
        assert by_key["core.ipc.token"]["secret"] is True
        assert by_key["core.ipc.token"]["value"] != "hunter2"
        assert by_key["core.ipc.auth"]["secret"] is False
        assert by_key["core.ipc.auth"]["source"]
    finally:
        chassis.shutdown()


def test_logs_route_reads_the_tail_or_says_why_not(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    module = console_of(chassis)
    try:
        silent = module._logs_route(request_for(LOGS_PATH))
        assert silent["lines"] == []
        assert "disabled" in silent["note"]
        log_file = project_root / "run.log"
        log_file.write_text("\n".join(f"line {n}" for n in range(10)) + "\n", encoding="utf-8")
        logs = chassis.context.logs
        original = logs._log_file  # type: ignore[attr-defined]
        logs._log_file = log_file  # type: ignore[attr-defined]
        try:
            tail = module._logs_route(request_for(LOGS_PATH, lines="3"))
            assert tail["lines"] == ["line 7", "line 8", "line 9"]
            assert len(module._logs_route(request_for(LOGS_PATH, lines="junk"))["lines"]) == 10
        finally:
            logs._log_file = original  # type: ignore[attr-defined]
    finally:
        chassis.shutdown()


def test_daemon_serves_the_page_and_guards_its_data(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    chassis.start()
    try:
        discovery = chassis.ipc.discovery  # type: ignore[union-attr]
        base = discovery.base_url()
        with urllib.request.urlopen(base + "/ui/", timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/html")
            assert b"Korodexios Shell" in response.read()
        with urllib.request.urlopen(base + "/ui/console.html", timeout=5) as response:
            assert b"Korodexios Console" in response.read()
        assert http_call(base, CATALOG_PATH)[0] == 401
        code, payload = http_call(base, CATALOG_PATH, token=discovery.token)
        assert code == 200
        assert payload["commands"]  # type: ignore[index]
    finally:
        chassis.shutdown()


def test_ui_opens_the_live_daemon(project_root, capsys, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(web_module.webbrowser, "open", lambda url: opened.append(url) or True)
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    chassis.start()
    try:
        discovery = chassis.ipc.discovery  # type: ignore[union-attr]
        assert main([*base_args(project_root), "ui"]) == int(ExitCode.SUCCESS)
        assert opened == [console_url(discovery)]
        out = capsys.readouterr().out
        assert "opened the web console" in out
        assert discovery.token not in out  # the printed line carries no secret

        assert main([*base_args(project_root), "ui", "--no-browser"]) == int(ExitCode.SUCCESS)
        assert len(opened) == 1
        assert console_url(discovery) in capsys.readouterr().out
    finally:
        chassis.shutdown()


def test_ui_boots_a_daemon_when_none_runs(project_root, capsys) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    module = console_of(chassis)
    request = CliRequest(
        context=chassis.context,
        command="ui",
        namespace=argparse.Namespace(no_browser=True),
        options={"chassis": chassis},
    )
    result: dict[str, Any] = {}
    runner = threading.Thread(target=lambda: result.update(code=module._ui_command(request)))
    runner.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (chassis.ipc and chassis.ipc.running):
        time.sleep(0.02)
    assert chassis.ipc is not None and chassis.ipc.running
    discovery = chassis.ipc.discovery
    assert discovery is not None
    while time.monotonic() < deadline and console_url(discovery) not in capsys.readouterr().out:
        time.sleep(0.02)
    chassis.ipc.request_shutdown()  # the operator stop, as /api/v1/shutdown sends it
    runner.join(timeout=10)
    assert result == {"code": 0}


def test_ui_refuses_when_the_control_plane_is_off(project_root, capsys) -> None:
    code = main([*base_args(project_root), "--no-ipc", "ui"])
    assert code == 1
    assert "control plane is disabled" in capsys.readouterr().out


def test_catalog_carries_the_permission_flags(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        commands = {item["path"]: item for item in console_of(chassis).catalog()["commands"]}
        assert commands["backup run"]["danger"] is True
        assert commands["backup run"]["remote"] is True
        assert commands["backup configure"]["remote"] is False
        assert commands["backup plan"]["danger"] is False
    finally:
        chassis.shutdown()


def test_event_feed_streams_bus_events_and_ends_on_stop(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    chassis.start()
    module = console_of(chassis)
    try:
        feed = module.event_feed(keepalive=0.05)
        assert next(feed).startswith(b"retry:")
        assert next(feed) == b": keepalive\n\n"
        chassis.bus.publish("demo.ping", {"n": 1}, source="test")
        frame = next(feed)
        while b"demo.ping" not in frame:
            frame = next(feed)
        payload = json.loads(frame.decode().removeprefix("data: "))
        assert payload["name"] == "demo.ping"
        assert payload["payload"] == {"n": 1}
        module.stop()
        remaining = list(feed)  # the feed ends instead of blocking
        assert all(b"demo.ping" not in chunk for chunk in remaining)
        assert module._feeds == set()
    finally:
        chassis.shutdown()


def test_events_route_streams_over_http(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    chassis.start()
    try:
        discovery = chassis.ipc.discovery  # type: ignore[union-attr]
        request = urllib.request.Request(
            discovery.base_url() + EVENTS_PATH, headers={"X-Kdx-Token": discovery.token}
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.headers["Content-Type"].startswith("text/event-stream")
            assert response.readline().startswith(b"retry:")
            chassis.bus.publish("demo.http", {}, source="test")
            seen = b""
            while b"demo.http" not in seen:
                seen += response.readline()
    finally:
        chassis.shutdown()


def test_i18n_route_translates_the_page(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        console = console_of(chassis)
        english = console._i18n_route(request_for(I18N_PATH))
        assert english["locale"] == "en"
        assert english["messages"]["Overview"] == "Overview"
        assert {"cs", "de", "en", "es", "fr", "pl", "sk"} <= set(english["locales"])
        slovak = console._i18n_route(request_for(I18N_PATH, locale="sk"))
        assert slovak["locale"] == "sk"
        assert slovak["messages"]["Overview"] == "Prehľad"
        assert console._i18n_route(request_for(I18N_PATH, locale="xx"))["messages"]["Run"] == "Run"
    finally:
        chassis.shutdown()


def test_every_static_text_of_the_page_has_a_message() -> None:
    """A text added to index.html without a catalogue entry would stay English."""
    import re

    from korodexios_core.kernel.i18n import parse_po
    from korodexios_core.modules.web_console.module import WEB_LOCALES_DIR

    known = {e.msgid for e in parse_po((WEB_LOCALES_DIR / "korodexios-web.pot").read_text("utf-8"))}
    page = (STATIC_DIR / "console.html").read_text(encoding="utf-8")
    for match in re.finditer(r'(?:placeholder|aria-label)="([^"]+)"', page):
        assert match.group(1) in known, match.group(1)
    for locale in ("sk", "cs", "de", "es", "fr", "pl"):
        text = (WEB_LOCALES_DIR / locale / "korodexios-web.po").read_text("utf-8")
        done = {e.msgid for e in parse_po(text) if e.translated}
        assert known <= done, (locale, sorted(known - done))


def test_the_frontend_key_picks_the_page_served_at_ui(project_root) -> None:
    from korodexios_core.kernel.chassis import ChassisOptions
    from korodexios_core.paths import AppPaths

    paths = AppPaths.discover(root=str(project_root), environ={})
    chassis = Chassis.create(
        ChassisOptions(
            home=paths.root,
            environ={},
            cli_overrides={
                "core.log.file_enabled": False,
                "core.ipc.enabled": True,
                "core.ipc.port": 0,
                "modules.web_console.frontend": "console",
            },
            install_signals=False,
        )
    )
    chassis.initialize()
    chassis.start()
    try:
        base = chassis.ipc.discovery.base_url()  # type: ignore[union-attr]
        with urllib.request.urlopen(base + "/ui/", timeout=5) as response:
            assert b"Korodexios Console" in response.read()
        with urllib.request.urlopen(base + "/ui/shell.html", timeout=5) as response:
            assert b"Korodexios Shell" in response.read()
    finally:
        chassis.shutdown()

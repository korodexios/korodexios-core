"""whoami, api schema and publish: the terminal twins of three HTTP routes (review T2)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.kernel.chassis import Chassis
from tests.conftest import PROJECT_CONFIG
from tests.test_ipc import base_args, ipc_chassis


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[tuple[Chassis, list[str]]]:
    (tmp_path / "kdx.yml").write_text(PROJECT_CONFIG, encoding="utf-8")
    chassis = ipc_chassis(tmp_path)
    chassis.initialize()
    chassis.start()
    try:
        yield chassis, base_args(tmp_path)
    finally:
        chassis.shutdown()


Daemon = tuple[Chassis, list[str]]


def run(daemon: Daemon, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = main([*daemon[1], *argv])
    return code, capsys.readouterr().out


def test_whoami_names_the_caller(daemon: Daemon, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(daemon, capsys, "whoami", "--json")
    payload = json.loads(out)
    assert code == 0 and payload["role"] == "admin" and payload["can"]
    code, out = run(daemon, capsys, "whoami")
    assert code == 0 and "admin" in out


def test_api_schema_lists_the_routes(daemon: Daemon, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(daemon, capsys, "api", "schema", "--json")
    payload = json.loads(out)
    assert code == 0 and {"routes", "module_routes", "core_api"} <= set(payload)
    code, out = run(daemon, capsys, "api", "schema")
    assert code == 0 and "/api/v1/events" in out and "/api/v1/whoami" in out


def test_publish_puts_an_inbound_event_on_the_bus(
    daemon: Daemon, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[Any] = []
    daemon[0].bus.subscribe("inbound.**", seen.append, name="test")
    code, out = run(daemon, capsys, "publish", "order.paid", "--payload", '{"id": 7}')
    assert code == 0 and "inbound.order.paid" in out
    assert [event.name for event in seen] == ["inbound.order.paid"]
    assert seen[0].payload["payload"] == {"id": 7}
    assert run(daemon, capsys, "publish", "order.paid", "--payload", "{nope")[0] == 2
    assert run(daemon, capsys, "publish", "BAD NAME")[0] == 1


def test_the_clients_say_when_no_daemon_runs(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["--home", str(project_root), "--no-log-file", "--no-color"]
    for argv in (["whoami", "--json"], ["api", "schema", "--json"], ["publish", "x", "--json"]):
        assert main([*base, *argv]) == 1
        assert json.loads(capsys.readouterr().out)["error"] == "no daemon is answering"


def test_the_daemon_group_reads_logs_config_and_audit(
    daemon: Daemon, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(daemon, capsys, "daemon", "config", "show", "core.lifecycle", "--json")
    keys = json.loads(out)
    assert code == 0 and "core.lifecycle.tick_interval" in keys
    assert run(daemon, capsys, "daemon", "audit", "--json")[0] == 0
    code, out = run(daemon, capsys, "daemon", "logs", "-n", "5", "--json")
    assert code == 0 and "lines" in json.loads(out)


def test_the_daemon_group_edits_the_configuration(
    daemon: Daemon, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    key = "core.lifecycle.tick_interval"
    code, out = run(daemon, capsys, "daemon", "config", "set", key, "0.2")
    assert code == 0, out
    assert "tick_interval: 0.2" in (tmp_path / "kdx.local.yml").read_text(encoding="utf-8")
    assert run(daemon, capsys, "daemon", "config", "set", key)[0] == 2
    assert run(daemon, capsys, "daemon", "config", "unset", key)[0] == 0
    assert "tick_interval" not in (tmp_path / "kdx.local.yml").read_text(encoding="utf-8")

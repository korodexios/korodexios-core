"""`kdx rpc` and `kdx --remote` exit with the daemon command's own code (review 2026-11-08, B5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.cli.rpc_exit import rpc_exit_code
from korodexios_core.kernel.cli_contract import CliCommand
from tests.conftest import PROJECT_CONFIG
from tests.test_ipc import base_args, ipc_chassis


def test_rpc_exit_code_rules() -> None:
    assert rpc_exit_code(200, {"result": {"exit_code": 0}}) == 0
    assert rpc_exit_code(200, {"result": {"exit_code": 4}}) == 4
    assert rpc_exit_code(200, {}) == 0
    assert rpc_exit_code(200, {"result": {"exit_code": True}}) == 0
    for status in (400, 401, 403, 404, 422, 500):
        assert rpc_exit_code(status, {"result": {"exit_code": 0}}) == 1


def test_the_daemon_exit_code_reaches_the_shell(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "kdx.yml").write_text(PROJECT_CONFIG, encoding="utf-8")
    chassis = ipc_chassis(tmp_path)
    chassis.initialize()
    chassis.registry.get("heartbeat").commands.append(  # type: ignore[union-attr]
        CliCommand(name="probe", subcommands=(
            CliCommand(name="ok", handler=lambda r: 0),
            CliCommand(name="fail", handler=lambda r: 1),
            CliCommand(name="verdict", handler=lambda r: 4),
        ))
    )
    chassis.start()
    try:
        base = base_args(tmp_path)
        assert main([*base, "rpc", "probe", "ok"]) == 0
        assert main([*base, "rpc", "probe", "fail"]) == 1
        assert main([*base, "rpc", "probe", "verdict"]) == 4
        assert main([*base, "--remote", "probe", "ok"]) == 0
        assert main([*base, "--remote", "probe", "fail"]) == 1
        assert main([*base, "--remote", "probe", "verdict"]) == 4
        assert main([*base, "rpc", "no-such-command"]) == 1
    finally:
        chassis.shutdown()
    capsys.readouterr()

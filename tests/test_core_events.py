"""`kdx events` follows the daemon without the web console (review 2026-11-08, T3)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.kernel.chassis import Chassis, ChassisOptions
from korodexios_core.paths import AppPaths
from tests.conftest import PROJECT_CONFIG


def test_events_work_with_the_web_console_disabled(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    off = "  ipc:" + chr(10) + "    enabled: false"
    on = "  ipc:" + chr(10) + "    enabled: true"
    enabled = PROJECT_CONFIG.replace(off, on)
    (tmp_path / "kdx.yml").write_text(enabled, encoding="utf-8")
    paths = AppPaths.discover(root=str(tmp_path), environ={})
    chassis = Chassis.create(
        ChassisOptions(
            home=paths.root,
            environ={},
            cli_overrides={"core.modules.disabled": ["web_console"], "core.ipc.port": 0},
            install_signals=False,
        )
    )
    chassis.initialize()
    chassis.start()
    result: dict[str, int] = {}
    argv = ["--home", str(tmp_path), "--no-log-file", "--no-color", "events", "demo.*", "-n", "1"]
    reader = threading.Thread(target=lambda: result.update(code=main(argv)))
    try:
        assert chassis.registry.get("web_console") is None
        reader.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(
            sub.name == "core.events.feed" for sub in chassis.bus._subscriptions
        ):
            time.sleep(0.05)
        chassis.bus.publish("demo.hello", {"n": 1}, source="test")
        reader.join(timeout=10)
    finally:
        chassis.shutdown()
    assert result == {"code": 0}
    assert "demo.hello" in capsys.readouterr().out
    assert not any(sub.name == "core.events.feed" for sub in chassis.bus._subscriptions)

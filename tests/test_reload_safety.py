"""A refused configuration never stops the daemon (review 2026-11-08, B4)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.kernel.chassis import Chassis, ChassisOptions
from korodexios_core.kernel.ipc import RELOAD_PATH

GOOD = """
core:
  name: first
  log: {level: WARNING, file_enabled: false}
  modules: {autodiscover: false, extra_paths: []}
  lifecycle: {tick_interval: 0.05, stop_timeout: 1.0}
  ipc: {enabled: true, port: 0}
  instance: {lock: false}
"""


@pytest.fixture
def running(tmp_path: Path) -> Iterator[tuple[Chassis, Path]]:
    config = tmp_path / "kdx.yml"
    config.write_text(GOOD, encoding="utf-8")
    chassis = Chassis.create(ChassisOptions(home=tmp_path, environ={}, install_signals=False))
    chassis.initialize()
    chassis.start()
    try:
        yield chassis, config
    finally:
        chassis.shutdown()


def post_reload(chassis: Chassis) -> tuple[int, dict[str, Any]]:
    discovery = chassis.ipc.discovery  # type: ignore[union-attr]
    request = urllib.request.Request(
        discovery.base_url() + RELOAD_PATH, method="POST", headers={"X-Kdx-Token": discovery.token}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return int(response.status), json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), json.loads(exc.read())


def test_a_typo_over_the_control_plane_is_refused_and_the_daemon_keeps_running(
    running: tuple[Chassis, Path],
) -> None:
    chassis, config = running
    config.write_text("core: [unclosed\n", encoding="utf-8")
    status, payload = post_reload(chassis)
    assert status == 422
    assert payload["reloaded"] is False and payload["error"]
    assert chassis.config.get_str("core.name") == "first"
    assert chassis.reloads == 0
    config.write_text(GOOD.replace("first", "second"), encoding="utf-8")
    status, payload = post_reload(chassis)
    assert status == 200 and payload["reloaded"] is True
    assert chassis.config.get_str("core.name") == "second"


def test_a_typo_on_sighup_does_not_end_the_tick_loop(running: tuple[Chassis, Path]) -> None:
    chassis, config = running
    config.write_text("core: [unclosed\n", encoding="utf-8")
    chassis.signals.request_reload()
    assert chassis.run_forever(duration=0.3) == 0
    assert chassis.state.value == "running"
    assert chassis.signals.reload_requested is False, "the request is consumed, not retried"
    assert chassis.config.get_str("core.name") == "first"


def test_a_refused_reload_is_announced_without_values(running: tuple[Chassis, Path]) -> None:
    chassis, config = running
    seen: list[Any] = []
    chassis.bus.subscribe("core.config.reload_failed", seen.append)
    config.write_text("core: [unclosed\n", encoding="utf-8")
    post_reload(chassis)
    assert len(seen) == 1
    assert "unclosed" not in json.dumps(getattr(seen[0], "payload", seen[0]), default=str)


def test_two_reloads_at_once_both_succeed(running: tuple[Chassis, Path]) -> None:
    chassis, _ = running
    errors: list[BaseException] = []

    def reload_once() -> None:
        try:
            post_reload(chassis)
        except BaseException as exc:  # the test reports whatever escaped
            errors.append(exc)

    threads = [threading.Thread(target=reload_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == []
    assert chassis.reloads == 2

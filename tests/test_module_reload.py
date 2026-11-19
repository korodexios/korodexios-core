"""Reloading one module of a running daemon: prepare, swap, roll back, never half-way."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from korodexios_core import Chassis, ChassisOptions
from korodexios_core.kernel.module_reload import reload_module

PLUGIN = '''
from korodexios_core import IpcRoute, Module


class Plug(Module):
    name = "plug"
    description = "reload fixture"
    version = "{version}"
    depends_on = {depends}

    def start(self):
        super().start()
        {start_extra}
        scope = self.require_context().scope(self.name)
        scope.subscribe("plug.ping", self._ping)

    def _ping(self, event):
        self.bus.publish("plug.pong", {{"version": self.version}}, source=self.name)

    def ipc_routes(self):
        return (IpcRoute("/api/v1/plug", lambda request: {{"version": self.version}}),)


MODULE = Plug
'''

CHILD = '''
from korodexios_core import Module


class Child(Module):
    name = "child"
    description = "depends on plug"
    version = "0.1.0"
    depends_on = ("plug",)
    starts = 0

    def start(self):
        super().start()
        type(self).starts += 1


MODULE = Child
'''


def _write(
    directory: Path, version: str, *, start_extra: str = "pass", depends: str = "()"
) -> None:
    (directory / "plug.py").write_text(
        PLUGIN.format(version=version, start_extra=start_extra, depends=depends), encoding="utf-8"
    )


@pytest.fixture()
def daemon(tmp_path: Path) -> Iterator[tuple[Chassis, Path]]:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(plugins, "0.1.0")
    (tmp_path / "kdx.yml").write_text("core:\n  name: reload\n", encoding="utf-8")
    chassis = Chassis.create(
        ChassisOptions(
            home=tmp_path,
            environ={},
            install_signals=False,
            instance_lock=False,
            extra_module_dirs=[plugins],
            cli_overrides={"core.log.file_enabled": False, "core.ipc.enabled": False},
        )
    )
    chassis.start(ipc=False)
    try:
        yield chassis, plugins
    finally:
        chassis.shutdown()


def _pongs(chassis: Chassis) -> list[str]:
    seen: list[str] = []
    chassis.bus.subscribe("plug.pong", lambda event: seen.append(event.payload["version"]))
    chassis.bus.publish("plug.ping")
    return seen


def test_reload_swaps_code_scope_and_routes(daemon: tuple[Chassis, Path]) -> None:
    chassis, plugins = daemon
    old = chassis.registry.require("plug").module
    _write(plugins, "0.2.0")
    report = reload_module(chassis, "plug")
    assert report.reloaded, report.error
    assert (report.version_before, report.version_after) == ("0.1.0", "0.2.0")
    record = chassis.registry.require("plug")
    assert record.module is not old and record.state.value == "running"
    assert _pongs(chassis) == ["0.2.0"]
    live = next(item for item in chassis.ipc_routes if item.path == "/api/v1/plug")
    assert live is record.routes[0]


def test_broken_new_code_leaves_the_running_module_alone(daemon: tuple[Chassis, Path]) -> None:
    chassis, plugins = daemon
    old = chassis.registry.require("plug").module
    (plugins / "plug.py").write_text("def broken(:\n", encoding="utf-8")
    report = reload_module(chassis, "plug")
    assert not report.reloaded and "new code was refused" in report.error
    record = chassis.registry.require("plug")
    assert record.module is old and record.state.value == "running"
    assert _pongs(chassis) == ["0.1.0"]


def test_failing_start_rolls_back_to_the_old_version(daemon: tuple[Chassis, Path]) -> None:
    chassis, plugins = daemon
    _write(plugins, "0.2.0", start_extra='raise RuntimeError("no start")')
    report = reload_module(chassis, "plug")
    assert not report.reloaded and report.rolled_back
    assert "did not come up" in report.error
    record = chassis.registry.require("plug")
    assert record.spec.version == "0.1.0" and record.state.value == "running"
    assert _pongs(chassis) == ["0.1.0"]


def test_running_dependents_need_cascade(daemon: tuple[Chassis, Path]) -> None:
    chassis, plugins = daemon
    (plugins / "child.py").write_text(CHILD, encoding="utf-8")
    chassis.shutdown()
    chassis2 = Chassis.create(
        ChassisOptions(
            home=plugins.parent,
            environ={},
            install_signals=False,
            instance_lock=False,
            extra_module_dirs=[plugins],
            cli_overrides={"core.log.file_enabled": False, "core.ipc.enabled": False},
        )
    )
    chassis2.start(ipc=False)
    try:
        refused = reload_module(chassis2, "plug")
        assert not refused.reloaded and "child" in refused.error
        _write(plugins, "0.2.0")
        done = reload_module(chassis2, "plug", cascade=True)
        assert done.reloaded, done.error
        assert done.stopped == ["child", "plug"] and done.restarted == ["child"]
        assert chassis2.registry.require("child").state.value == "running"
        assert _pongs(chassis2) == ["0.2.0"]
    finally:
        chassis2.shutdown()


def test_dependency_change_and_unknown_names_are_refused(daemon: tuple[Chassis, Path]) -> None:
    chassis, plugins = daemon
    _write(plugins, "0.2.0", depends='("system_info",)')
    changed = reload_module(chassis, "plug")
    assert not changed.reloaded and "dependency list" in changed.error
    assert chassis.registry.require("plug").spec.version == "0.1.0"
    assert "not loaded" in reload_module(chassis, "ghost").error
    assert "cannot reload itself" in reload_module(chassis, "module_admin").error


def test_reload_needs_a_running_chassis(tmp_path: Path) -> None:
    (tmp_path / "kdx.yml").write_text("core:\n  name: reload\n", encoding="utf-8")
    chassis = Chassis.create(
        ChassisOptions(home=tmp_path, environ={}, install_signals=False, instance_lock=False,
                       cli_overrides={"core.log.file_enabled": False, "core.ipc.enabled": False})
    )
    assert "not running" in reload_module(chassis, "heartbeat").error


def test_the_command_runs_through_the_daemon_dispatch(daemon: tuple[Chassis, Path]) -> None:
    chassis, plugins = daemon
    _write(plugins, "0.2.0")
    result = chassis.dispatch_rpc("module-reload", {"argv": ["plug", "--json"]})
    assert result["exit_code"] == 0
    assert '"reloaded": true' in str(result.get("output", result))
    assert chassis.registry.require("plug").spec.version == "0.2.0"
    refused = chassis.dispatch_rpc("module-reload", {"argv": ["module_admin"]})
    assert refused["exit_code"] == 1

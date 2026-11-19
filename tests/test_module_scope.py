"""Module scopes: every registration ends with the module, in order, without leaks."""

from __future__ import annotations

from pathlib import Path

import pytest

from korodexios_core import Chassis, ChassisOptions, Disposables, IpcRoute


def _chassis(root: Path) -> Chassis:
    chassis = Chassis.create(
        ChassisOptions(
            home=root,
            environ={},
            install_signals=False,
            cli_overrides={"core.log.file_enabled": False, "core.ipc.enabled": False},
        )
    )
    chassis.initialize()
    return chassis


def test_disposables_run_newest_first_once() -> None:
    order: list[str] = []
    stack = Disposables()
    stack.add(lambda: order.append("a"))
    stack.add(lambda: order.append("b"))
    assert stack.dispose() == []
    assert stack.dispose() == []
    assert order == ["b", "a"]
    assert stack.closed


def test_failing_disposer_never_blocks_the_others() -> None:
    order: list[str] = []

    def boom() -> None:
        raise RuntimeError("nope")

    stack = Disposables()
    stack.add(lambda: order.append("first"))
    stack.add(boom, "boom")
    stack.add(lambda: order.append("last"))
    failures = stack.dispose()
    assert order == ["last", "first"]
    assert len(failures) == 1 and "boom: RuntimeError: nope" in failures[0]


def test_late_registration_is_undone_at_once() -> None:
    ran: list[int] = []
    stack = Disposables()
    stack.dispose()
    stack.add(lambda: ran.append(1))
    assert ran == [1]
    assert len(stack) == 0


def test_handle_runs_one_disposer_early_and_only_once() -> None:
    ran: list[int] = []
    stack = Disposables()
    handle = stack.add(lambda: ran.append(1))
    handle()
    handle()
    stack.dispose()
    assert ran == [1]


def test_disposer_must_be_callable() -> None:
    with pytest.raises(TypeError):
        Disposables().add("nope")  # type: ignore[arg-type]


def test_scope_undoes_bus_tasks_and_routes(tmp_path: Path) -> None:
    (tmp_path / "kdx.yml").write_text("core:\n  name: scope\n", encoding="utf-8")
    chassis = _chassis(tmp_path)
    context = chassis.context
    scope = context.scope("demo")
    assert context.scope("demo") is scope
    seen: list[object] = []
    scope.subscribe("demo.ping", lambda event: seen.append(event.payload))
    task = scope.schedule(60.0, lambda: None, name="demo-sweep")
    route = IpcRoute("/api/v1/demo", lambda request: {"ok": True}, module="demo")
    assert scope.register_ipc_route(route)
    assert task is not None and task.module == "demo"
    context.publish("demo.ping", 1)
    assert seen == [1]
    assert chassis.scheduler is not None and chassis.scheduler.task("demo-sweep") is task
    assert any(item is route for item in chassis.ipc_routes)

    assert context.close_scope("demo") == []

    context.publish("demo.ping", 2)
    assert seen == [1]
    assert chassis.scheduler.task("demo-sweep") is None
    assert not any(item is route for item in chassis.ipc_routes)
    assert context.close_scope("demo") == []


def test_scope_keeps_a_route_another_module_replaced(tmp_path: Path) -> None:
    (tmp_path / "kdx.yml").write_text("core:\n  name: scope\n", encoding="utf-8")
    chassis = _chassis(tmp_path)
    context = chassis.context
    first = IpcRoute("/api/v1/shared", lambda request: {"who": "a"}, module="a")
    second = IpcRoute("/api/v1/shared", lambda request: {"who": "b"}, module="b")
    context.scope("a").register_ipc_route(first)
    context.scope("b").register_ipc_route(second)
    context.close_scope("a")
    assert any(item is second for item in chassis.ipc_routes)
    context.close_scope("b")
    assert not any(item.path == "/api/v1/shared" for item in chassis.ipc_routes)


def test_scope_needs_a_name(tmp_path: Path) -> None:
    (tmp_path / "kdx.yml").write_text("core:\n  name: scope\n", encoding="utf-8")
    chassis = _chassis(tmp_path)
    with pytest.raises(Exception, match="module name"):
        chassis.context.scope("  ")


def test_shutdown_closes_scopes_after_stop(tmp_path: Path) -> None:
    (tmp_path / "kdx.yml").write_text("core:\n  name: scope\n", encoding="utf-8")
    chassis = _chassis(tmp_path)
    scope = chassis.context.scope("demo")
    scope.subscribe("demo.ping", lambda event: None)
    assert len(scope.disposables) == 1
    chassis.shutdown()
    assert scope.closed
    assert len(scope.disposables) == 0

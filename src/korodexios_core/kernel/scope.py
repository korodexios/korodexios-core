"""Per-module scope: every registration a module makes comes with its own undo.

A module that subscribes to the bus, schedules a task, or publishes a route through its
scope never has to remember to withdraw them: the chassis closes the scope after the
module's ``stop()``, newest registration first, whether ``stop()`` succeeded, raised, or
timed out. Closing is idempotent, one failing disposer never blocks the others, and a
registration made after the scope closed is undone at once instead of leaking.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..events.bus import EventHandler, Subscription

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from .context import CoreContext
    from .ipc import IpcRoute, IpcStaticMount
    from .scheduler import ScheduledTask

Disposer = Callable[[], Any]


class Disposables:
    """A thread-safe stack of undo callbacks, run newest first, at most once each."""

    def __init__(self, *, logger: Any = None, owner: str = "") -> None:
        self._lock = threading.Lock()
        self._items: list[tuple[str, Disposer]] = []
        self._closed = False
        self._logger = logger
        self._owner = owner

    @property
    def closed(self) -> bool:
        """Return True once ``dispose`` has run; later additions are undone at once."""
        with self._lock:
            return self._closed

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def labels(self) -> tuple[str, ...]:
        """Return the labels of the pending disposers, oldest first."""
        with self._lock:
            return tuple(label for label, _ in self._items)

    def add(self, disposer: Disposer, label: str = "") -> Disposer:
        """Keep *disposer*; return a handle that runs it now and removes it from the stack.

        When the stack is already closed the disposer runs immediately: a late
        registration is undone rather than leaked past the module's life.
        """
        if not callable(disposer):
            raise TypeError("a disposer must be callable")
        item = (label or getattr(disposer, "__name__", "disposer"), disposer)
        with self._lock:
            if not self._closed:
                self._items.append(item)
                closed = False
            else:
                closed = True
        if closed:
            self._run(item)
            return _noop

        def _release() -> None:
            with self._lock:
                try:
                    self._items.remove(item)
                except ValueError:
                    return
            self._run(item)

        return _release

    def dispose(self) -> list[str]:
        """Run every pending disposer, newest first; return one line per failure.

        A disposer that raises is logged and counted, and the next one still runs.
        """
        with self._lock:
            self._closed = True
            pending = self._items[::-1]
            self._items = []
        failures: list[str] = []
        for item in pending:
            error = self._run(item)
            if error:
                failures.append(error)
        return failures

    def _run(self, item: tuple[str, Disposer]) -> str:
        label, disposer = item
        try:
            disposer()
        except Exception as exc:
            message = f"{label}: {type(exc).__name__}: {exc}"
            if self._logger is not None:
                self._logger.warning(
                    "module disposer failed", module=self._owner or None, disposer=message
                )
            return message
        return ""


def _noop() -> None:
    return None


class ModuleScope:
    """The registrations of one module, undone together when the module ends."""

    def __init__(self, name: str, context: CoreContext) -> None:
        self.name = str(name)
        self._context = context
        self.disposables = Disposables(logger=context.logger_for("scope"), owner=self.name)

    @property
    def closed(self) -> bool:
        """Return True once the chassis closed the scope."""
        return self.disposables.closed

    def on_dispose(self, disposer: Disposer, label: str = "") -> Disposer:
        """Run *disposer* when the scope closes; return a handle that runs it earlier."""
        return self.disposables.add(disposer, label)

    def subscribe(
        self,
        pattern: str,
        handler: EventHandler,
        *,
        name: str | None = None,
        once: bool = False,
    ) -> Subscription:
        """Subscribe on the shared bus; the subscription is withdrawn with the scope."""
        bus = self._context.bus
        subscription = bus.subscribe(pattern, handler, name=name or self.name, once=once)
        self.disposables.add(lambda: bus.unsubscribe(subscription), f"subscription {pattern}")
        return subscription

    def schedule(
        self, interval_seconds: float, callback: Callable[[], Any], **kwargs: Any
    ) -> ScheduledTask | None:
        """Schedule a periodic task owned by this module; withdrawn with the scope."""
        kwargs.setdefault("module", self.name)
        task = self._context.schedule(interval_seconds, callback, **kwargs)
        self._track_task(task)
        return task

    def schedule_at(
        self, spec: str, callback: Callable[[], Any], **kwargs: Any
    ) -> ScheduledTask | None:
        """Schedule a calendar task owned by this module; withdrawn with the scope."""
        kwargs.setdefault("module", self.name)
        task = self._context.schedule_at(spec, callback, **kwargs)
        self._track_task(task)
        return task

    def register_ipc_route(self, route: IpcRoute) -> bool:
        """Publish *route*; withdrawn with the scope unless another module replaced it."""
        registered = self._context.register_ipc_route(route)
        if registered:
            self.disposables.add(lambda: self._withdraw_route(route), f"route {route.path}")
        return registered

    def register_static_dir(
        self, route_prefix: str, directory: str | Path, **kwargs: Any
    ) -> IpcStaticMount | None:
        """Mount *directory*; withdrawn with the scope unless another module replaced it."""
        mount = self._context.register_static_dir(route_prefix, directory, **kwargs)
        if mount is not None:
            self.disposables.add(lambda: self._withdraw_mount(mount), f"mount {mount.prefix}")
        return mount

    def close(self) -> list[str]:
        """Undo everything, newest first; return one line per failed disposer."""
        return self.disposables.dispose()

    def _track_task(self, task: ScheduledTask | None) -> None:
        scheduler = self._context.scheduler
        if task is None or scheduler is None:
            return
        name = task.name
        self.disposables.add(lambda: _unschedule(scheduler, name, task), f"task {name}")

    def _withdraw_route(self, route: IpcRoute) -> None:
        chassis = self._context.chassis
        current = getattr(chassis, "ipc_routes", ())
        # Only withdraw a route that is still ours: a later registration of the same
        # path by another module must survive this module's end.
        if any(item is route for item in current):
            chassis.unregister_ipc_route(route.path)

    def _withdraw_mount(self, mount: IpcStaticMount) -> None:
        chassis = self._context.chassis
        current = getattr(chassis, "ipc_static", ())
        if any(item is mount for item in current):
            chassis.unregister_static_dir(mount.prefix)


def _unschedule(scheduler: Any, name: str, task: ScheduledTask) -> None:
    if scheduler.task(name) is task:
        scheduler.unschedule(name)


def close_scopes_when_modules_end(context: CoreContext) -> None:
    """Close a module's scope once its stop() ended, and every scope when the chassis does."""

    def module_ended(event: Any) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if str(event.name).endswith(".failed") and payload.get("phase") != "stop":
            return  # a failed init or start: the module is still stopped later
        context.close_scope(".".join(str(event.name).split(".")[1:-1]))

    def chassis_stopped(_event: Any) -> None:
        for name in list(context.scopes):
            context.close_scope(name)

    context.bus.subscribe("module.*.stopped", module_ended, name="scope-close")
    context.bus.subscribe("module.*.failed", module_ended, name="scope-close")
    context.bus.subscribe("core.stopped", chassis_stopped, name="scope-close")

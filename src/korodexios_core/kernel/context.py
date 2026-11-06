"""The context handed to every module: config, logger, event bus, paths, registry."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..config.layered import LayeredConfig, ScopedConfig
from ..events.bus import EventBus
from ..exceptions import ChassisError
from ..logging.engine import KorodexiosLogger, LoggerFactory
from ..paths import AppPaths
from ..version import CORE_API_VERSION, __version__
from .async_runtime import AsyncRuntime
from .i18n import CORE_DOMAIN, I18n
from .limiter import UNSET, LimiterRegistry, RateLimiter, _Unset
from .metrics import MetricsRegistry
from .process import ProcessResult, ProcessSupervisor
from .queue import JobQueue
from .scheduler import TaskScheduler
from .supervision import WorkerRecord, WorkerSupervisor

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .ipc import IpcRoute, IpcStaticMount
    from .locks import NamedLock
    from .registry import ModuleRegistry
    from .scheduler import ScheduledTask, TaskScheduler


@dataclass(slots=True)
class CoreContext:
    """Everything a module may legitimately reach for."""

    config: LayeredConfig
    paths: AppPaths
    logs: LoggerFactory
    bus: EventBus
    app_name: str = "korodexios"
    app_version: str = __version__
    core_api: str = CORE_API_VERSION
    started_at: float = field(default_factory=time.time)
    registry: ModuleRegistry | None = None
    chassis: Any = None
    async_runtime: AsyncRuntime | None = None
    supervisor: WorkerSupervisor | None = None
    scheduler: TaskScheduler | None = None
    limiters: LimiterRegistry | None = None
    processes: ProcessSupervisor | None = None
    queue: JobQueue | None = None
    i18n: I18n = field(default_factory=I18n)
    metrics: MetricsRegistry = field(default_factory=MetricsRegistry)
    """Counters, gauges, summaries and collectors; served by the 'metrics' module."""

    def translate(self, message: str, /, *, domain: str = CORE_DOMAIN, **fields: Any) -> str:
        """Return *message* in the active locale; the source string when untranslated."""
        return self.i18n.translate(message, domain=domain, **fields)

    def logger_for(self, name: str) -> KorodexiosLogger:
        """Return a logger namespaced below the chassis namespace."""
        return self.logs.get(name)

    def module_config(self, name: str) -> ScopedConfig:
        """Return the configuration subtree for module *name*."""
        return self.config.child(f"modules.{name}")

    def module_dir(self, name: str) -> Path:
        """Return (and create) the private data directory for module *name*."""
        return self.paths.module_dir(name)

    def durable_module_dir(self, name: str) -> Path:
        """Return (and create) the durable directory for module *name*.

        Durable state - a SQLite file, a ledger, an index - belongs to the
        project's history, not to one run, so it lives outside the state tree the
        next boot may throw away.
        """
        return self.paths.durable_module_dir(name)

    def schedule(
        self,
        interval_seconds: float,
        callback: Callable[[], Any],
        **kwargs: Any,
    ) -> ScheduledTask | None:
        """Declare a periodic task on the chassis scheduler; None when there is none.

        The chassis owns the clock and the shutdown, so a module declares an
        interval and a callback and never writes a thread, a stop flag, or a join.
        """
        scheduler = self.scheduler
        if scheduler is None:
            return None
        kwargs.setdefault("module", self.app_name)
        return scheduler.schedule(interval_seconds, callback, **kwargs)

    def schedule_at(
        self,
        spec: str,
        callback: Callable[[], Any],
        **kwargs: Any,
    ) -> ScheduledTask | None:
        """Declare a calendar task ('mon 03:00', 'mon 02:00-05:00'); None without a scheduler."""
        scheduler = self.scheduler
        if scheduler is None:
            return None
        kwargs.setdefault("module", self.app_name)
        return scheduler.schedule_at(spec, callback, **kwargs)

    def job_queue(self) -> JobQueue:
        """Return the durable work queue (``data/jobs/queue.sqlite3``), created on first use.

        ``queue.handle("render", fn)`` registers a handler; ``queue.submit("render",
        {...})`` queues work that survives a restart. The ``job_queue`` module runs the
        workers and serves ``/api/v1/queue`` and ``kdx queue``.
        """
        if self.queue is None:
            self.queue = JobQueue(self.paths.durable_dir / "jobs" / "queue.sqlite3")
        return self.queue

    def limiter(
        self,
        name: str,
        *,
        min_interval: float | None = None,
        jitter: float | None = None,
        daily_budget: int | _Unset | None = UNSET,
        declared: bool | None = None,
    ) -> RateLimiter:
        """Return the rate limiter *name* (a minimum gap, a daily budget, a cooldown).

        Call ``limiter.acquire()`` before each outbound call and
        ``limiter.cooldown(hours=24)`` after a 429. The state lives in the durable
        directory and is shared between processes; ``kdx limiter list`` shows it.

        Passing a limit declares it and stores it; ``context.limiter("name")`` with no
        limit is a handle that leaves the stored limits alone (use it in status and
        maintenance commands) while still enforcing them.
        """
        registry = self.limiters
        if registry is None:
            registry = self.limiters = LimiterRegistry(
                self.paths.durable_dir / "limiters", logger=self.logger_for("limiter"), bus=self.bus
            )
        return registry.get(
            name,
            min_interval=min_interval,
            jitter=jitter,
            daily_budget=daily_budget,
            declared=declared,
        )

    def run_process(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        timeout: float | None = None,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        name: str = "",
        check: bool = False,
        stdin: str | None = None,
    ) -> ProcessResult:
        """Run a child process under the chassis: time limit, logged output, clean stop.

        The command is an argument list (never a shell string). Output goes to the log,
        a timeout or a chassis stop kills the whole process group, and the outcome is
        published as ``core.process.finished``.
        """
        supervisor = self.processes
        if supervisor is None:
            supervisor = self.processes = ProcessSupervisor(
                logger=self.logger_for("process"), bus=self.bus
            )
        return supervisor.run(
            command, timeout=timeout, cwd=cwd, env=env, name=name, check=check, stdin=stdin
        )

    def run_json(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        timeout: float | None = 60.0,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        name: str = "",
        stdin: str | None = None,
    ) -> dict[str, Any]:
        """Run a child process and return its stdout parsed as one JSON object.

        Supervised like ``run_process``; a failure, a timeout, or output that is not a
        JSON object raises ProcessError naming the argument list, exit code, and stderr.
        """
        supervisor = self.processes
        if supervisor is None:
            supervisor = self.processes = ProcessSupervisor(
                logger=self.logger_for("process"), bus=self.bus
            )
        return supervisor.run_json(
            command, timeout=timeout, cwd=cwd, env=env, name=name, stdin=stdin
        )

    def acquire_lock(
        self,
        name: str,
        *,
        timeout: float | None = 0.0,
        blocking: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> NamedLock:
        """Take named lock *name* for this process; raise when it stays busy.

        Named locks isolate critical sections: two unrelated jobs never queue
        behind each other just because one process exists.
        """
        chassis = self.chassis
        acquire = getattr(chassis, "acquire_lock", None)
        if not callable(acquire):
            return self.paths.lock(name, timeout=timeout, blocking=blocking, metadata=metadata)
        return cast(
            "NamedLock",
            acquire(name, timeout=timeout, blocking=blocking, metadata=metadata),
        )

    def release_lock(self, name: str) -> bool:
        """Release named lock *name*; False when this process never held it."""
        chassis = self.chassis
        release = getattr(chassis, "release_lock", None)
        if not callable(release):
            return False
        return bool(release(name))

    def uptime(self) -> float:
        """Return seconds since the chassis started."""
        return max(0.0, time.time() - self.started_at)

    def publish(self, name: str, payload: Any = None, *, source: str | None = None) -> int:
        """Publish an event on the shared bus."""
        return self.bus.publish(name, payload, source=source)

    @property
    def async_available(self) -> bool:
        """Return True when the chassis is running a managed asyncio loop."""
        runtime = self.async_runtime
        return bool(runtime is not None and runtime.running)

    def submit_async(self, coro: Any) -> Any:
        """Schedule *coro* on the chassis loop; raise when the loop is off."""
        runtime = self.async_runtime
        if runtime is None or not runtime.running:
            raise ChassisError(
                "the chassis asyncio loop is not running",
                hint="Enable core.async.enabled (or kdx run --asyncio) before "
                "submitting coroutines.",
            )
        return runtime.submit(coro)

    def run_async(self, coro: Any, timeout: float | None = None) -> Any:
        """Run *coro* to completion, on the chassis loop when one exists.

        Without a managed loop the call runs the coroutine on a private loop, so
        a module never has to know whether the chassis was booted with asyncio
        enabled. Never call this from inside a running event loop.
        """
        runtime = self.async_runtime
        if runtime is not None and runtime.running:
            return runtime.run(coro, timeout=timeout)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                asyncio.wait_for(coro, timeout) if timeout else coro
            )
        finally:
            loop.close()

    def watch_worker(
        self,
        module: str,
        thread: threading.Thread,
        *,
        name: str = "",
        expected: bool = True,
    ) -> WorkerRecord | None:
        """Ask the chassis to supervise one of a module's background threads."""
        supervisor = self.supervisor
        if supervisor is None:
            return None
        return supervisor.watch(module, thread, name=name, expected=expected)

    def register_ipc_route(self, route: IpcRoute) -> bool:
        """Publish *route* on the live control plane; False when there is none."""
        chassis = self.chassis
        registrar = getattr(chassis, "register_ipc_route", None)
        if not callable(registrar):
            return False
        return bool(registrar(route))

    def register_static_dir(
        self,
        route_prefix: str,
        directory: str | Path,
        **kwargs: Any,
    ) -> IpcStaticMount | None:
        """Serve *directory* under *route_prefix* on the chassis control plane.

        The chassis owns the listener, so a module that ships a dashboard or an
        asset tree gets those bytes onto the same port as its JSON, with the right
        media type per file and no server of its own.
        """
        chassis = self.chassis
        registrar = getattr(chassis, "register_static_dir", None)
        if not callable(registrar):
            return None
        kwargs.setdefault("module", self.app_name)
        return cast("IpcStaticMount | None", registrar(route_prefix, directory, **kwargs))

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly view of the context."""
        return {
            "app_name": self.app_name,
            "app_version": self.app_version,
            "core_api": self.core_api,
            "uptime_seconds": round(self.uptime(), 3),
            "config_file": str(self.config.config_file) if self.config.config_file else None,
            "env_file": str(self.config.env_file) if self.config.env_file else None,
            "paths": self.paths.as_dict(),
            "logging": self.logs.stats(),
            "bus": self.bus.stats(),
            "async": self.async_runtime.as_dict() if self.async_runtime else {"running": False},
            "workers": self.supervisor.stats() if self.supervisor else {"watched": 0},
            "scheduler": self.scheduler.stats() if self.scheduler else {"scheduled": 0},
            "limiters": self.limiters.states() if self.limiters else [],
            "processes": self.processes.active() if self.processes else [],
            "locale": self.i18n.locale,
            "locks": {"names": list(self.paths.named_lock_files())},
        }

    def __repr__(self) -> str:
        return (
            f"CoreContext(app={self.app_name!r}, version={self.app_version!r}, "
            f"config_file={self.config.config_file!s})"
        )

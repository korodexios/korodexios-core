"""Reload one module of a running daemon: prepare first, swap second, roll back on failure.

The order is what makes it safe. The new code is imported and checked while the running
module is untouched; a syntax error, a broken contract, a renamed module or a changed
dependency list ends the attempt right there. Only then the module (and, with ``cascade``,
the modules that depend on it) is stopped, the new instance is brought up, and the routes,
mounts and workers it declares replace the old ones. If the new instance cannot come up,
the old code and instance are put back and started again.

A module that registers through ``context.scope(name)`` must do it in ``start()``: the
scope is closed with the module's ``stop()`` and a fresh one is opened for the next start.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import sys
import threading
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..constants import MANIFEST_NAME, MODULE_PACKAGE
from .chassis import ChassisState
from .contract import ModuleState
from .discovery import DiscoveryCandidate, DiscoveryReport, ModuleDiscovery, _slug
from .registry import RegisteredModule, _call_init, collect_workers

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .chassis import Chassis

RELOADED_EVENT = "core.module.reloaded"
PROTECTED = frozenset({"module_admin"})
"""Modules that run the reload itself; reloading them would cut the branch they sit on."""
_SERIAL = threading.Lock()


@dataclass(slots=True)
class ReloadReport:
    """What one reload did, in words an operator and a script can both read."""

    module: str
    reloaded: bool = False
    error: str = ""
    hint: str = ""
    rolled_back: bool = False
    version_before: str = ""
    version_after: str = ""
    stopped: list[str] = field(default_factory=list)
    restarted: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def refuse(self, error: str, hint: str = "") -> ReloadReport:
        """Record why nothing (or not everything) happened; return self."""
        self.reloaded, self.error, self.hint = False, error, hint
        return self

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly view."""
        return {
            "module": self.module,
            "reloaded": self.reloaded,
            "error": self.error,
            "hint": self.hint,
            "rolled_back": self.rolled_back,
            "version_before": self.version_before,
            "version_after": self.version_after,
            "stopped": list(self.stopped),
            "restarted": list(self.restarted),
            "failed": list(self.failed),
        }


@dataclass(slots=True)
class _Prepared:
    """New code that was imported and checked while the old module still runs."""

    candidate: DiscoveryCandidate
    base: str
    saved: dict[str, Any]


def reload_module(chassis: Chassis, name: str, *, cascade: bool = False) -> ReloadReport:
    """Reload module *name* inside the running *chassis*; never raises, always reports."""
    report = ReloadReport(module=str(name).strip())
    if not _SERIAL.acquire(blocking=False):
        return report.refuse("another module reload is running", "Retry in a moment.")
    try:
        return _reload(chassis, report, cascade)
    except Exception as exc:
        chassis.logger.error(f"module reload of {report.module} failed: {exc!r}")
        return report.refuse(
            f"reload failed unexpectedly: {type(exc).__name__}: {exc}",
            "Check 'kdx modules list'; 'kdx stop' and 'kdx run' restore a clean state.",
        )
    finally:
        _SERIAL.release()


def _reload(chassis: Chassis, report: ReloadReport, cascade: bool) -> ReloadReport:
    registry = chassis.registry
    record = registry.get(report.module)
    dependents = _running_dependents(chassis, report.module)
    problem = _refusal(chassis, record, dependents, cascade, report.module)
    if problem or record is None:
        return report.refuse(*(problem or ("unknown module", "")))
    prepared, why = _prepare(chassis, record)
    if prepared is None:
        return report.refuse(f"the new code was refused: {why}", "The running module is untouched.")
    report.version_before = record.spec.version
    report.version_after = prepared.candidate.spec.version
    return _swap(chassis, _Plan(record, prepared, dependents, report))


def _running_dependents(chassis: Chassis, name: str) -> list[RegisteredModule]:
    """Return the running modules that depend on *name*, transitively, in load order."""
    found = {name}
    chain: list[RegisteredModule] = []
    for record in chassis.registry.ordered():
        if record.spec.name not in found and found & set(record.spec.depends_on):
            found.add(record.spec.name)
            if record.state is ModuleState.RUNNING:
                chain.append(record)
    return chain


def _refusal(
    chassis: Chassis,
    record: RegisteredModule | None,
    dependents: list[RegisteredModule],
    cascade: bool,
    name: str,
) -> tuple[str, str] | None:
    """Return (error, hint) when the reload must not start, else None."""
    if chassis.state is not ChassisState.RUNNING:
        return "the chassis is not running", "A module reload needs a live daemon."
    if record is None:
        known = ", ".join(chassis.registry.names()) or "(none)"
        return f"module {name!r} is not loaded", f"Loaded modules: {known}."
    if name in PROTECTED:
        return f"module {name!r} runs the reload and cannot reload itself", "Restart the daemon."
    if record.state in (ModuleState.DISABLED, ModuleState.NEW) or record.spec.source is None:
        return f"module {name!r} is {record.state.value} and has no running code", ""
    if dependents and not cascade:
        names = ", ".join(item.spec.name for item in dependents)
        return f"running modules depend on {name!r}: {names}", "Add --cascade to restart them."
    return None


def _module_base(record: RegisteredModule) -> tuple[str, str, Path | None]:
    """Return (sys.modules prefix, import name, package directory) of a module's code."""
    source = Path(str(record.spec.source))
    package = source.parent if source.name == "__init__.py" else None
    name = package.name if package else source.stem
    if record.spec.origin == "builtin":
        return f"{MODULE_PACKAGE}.{name}", name, package
    holder = (package.parent if package else source.parent).name
    return f"korodexios_ext_{_slug(holder)}_{_slug(name)}", name, package


def _purge(base: str) -> dict[str, Any]:
    """Take the module's code out of sys.modules so the next import reads the files again."""
    saved = {key: value for key, value in sys.modules.items() if _under(key, base)}
    for key in saved:
        sys.modules.pop(key, None)
    return saved


def _under(key: str, base: str) -> bool:
    return key == base or key.startswith(base + ".")


def _drop_bytecode(source: Path, package: Path | None) -> None:
    """Delete the cached bytecode of the module's files.

    The cache is keyed on the source's mtime (whole seconds) and size, so a file rewritten
    within the same second to the same length would otherwise import as the old code.
    """
    for path in (package.rglob("*.py") if package else [source]):
        with contextlib.suppress(OSError, NotImplementedError):
            Path(importlib.util.cache_from_source(str(path))).unlink(missing_ok=True)


def _restore_code(base: str, saved: dict[str, Any]) -> None:
    """Put the previously imported code back, dropping whatever replaced it."""
    for key in [item for item in sys.modules if _under(item, base)]:
        sys.modules.pop(key, None)
    sys.modules.update(saved)


def _prepare(chassis: Chassis, record: RegisteredModule) -> tuple[_Prepared | None, str]:
    """Import and validate the new code; on any problem put the old code back."""
    base, name, package = _module_base(record)
    config = chassis.config
    scanner = ModuleDiscovery(
        chassis.paths,
        config=config,
        enabled=config.get_list("core.modules.enabled"),
        disabled=config.get_list("core.modules.disabled"),
        autodiscover=True,
        logger=chassis.logger,
    )
    manifest = package / MANIFEST_NAME if package else None
    outcome = DiscoveryReport()
    saved = _purge(base)
    _drop_bytecode(Path(str(record.spec.source)), package)
    importlib.invalidate_caches()
    try:
        scanner._load_candidate(
            name=name,
            package_dir=package,
            entry_file=Path(str(record.spec.source)),
            manifest_path=manifest if manifest and manifest.is_file() else None,
            origin=record.spec.origin,
            package_prefix=MODULE_PACKAGE if record.spec.origin == "builtin" else None,
            report=outcome,
        )
    except Exception as exc:
        _restore_code(base, saved)
        return None, f"{type(exc).__name__}: {exc}"
    problem = _candidate_problem(record, outcome)
    if problem:
        _restore_code(base, saved)
        return None, problem
    return _Prepared(outcome.candidates[0], base, saved), ""


def _candidate_problem(record: RegisteredModule, outcome: DiscoveryReport) -> str:
    """Return why the freshly loaded candidate cannot replace *record*, or ''."""
    if not outcome.candidates:
        found = [item.describe() for item in outcome.failures]
        found += [item.describe() for item in outcome.optional]
        found += [f"{name}: {reason}" for name, reason in outcome.skipped]
        return "; ".join(found) or "nothing was loaded"
    spec = outcome.candidates[0].spec
    if spec.name != record.spec.name:
        return f"the module is now called {spec.name!r}; restart the daemon for a rename"
    if tuple(spec.depends_on) != tuple(record.spec.depends_on):
        return "its dependency list changed; restart the daemon so the load order is rebuilt"
    return ""


def _fresh_scope(chassis: Chassis, name: str) -> None:
    """Close a module's scope and open a clean one for its next start."""
    chassis.context.close_scope(name)
    chassis.context.scopes.pop(name, None)


def _unwatch(chassis: Chassis, name: str) -> None:
    supervisor = chassis.supervisor
    if supervisor is None:
        return
    for worker in supervisor.records(name):
        supervisor.unwatch(name, worker.name)


def _rewatch(chassis: Chassis, record: RegisteredModule) -> None:
    supervisor = chassis.supervisor
    if supervisor is None:
        return
    for worker_name, thread in collect_workers(record.module):
        supervisor.watch(record.spec.name, thread, name=worker_name)


def _stop(chassis: Chassis, record: RegisteredModule) -> bool:
    """Stop one module the way a shutdown would; return whether it stopped cleanly."""
    timeout = chassis.config.get_float("core.lifecycle.stop_timeout", 5.0)
    name = record.spec.name
    _unwatch(chassis, name)
    chassis.registry._run(record, "stop", record.module.stop, timeout=timeout)
    _fresh_scope(chassis, name)
    return record.state is ModuleState.STOPPED


def _start(chassis: Chassis, record: RegisteredModule) -> bool:
    """Start one stopped module again with a fresh scope; return whether it runs."""
    _fresh_scope(chassis, record.spec.name)
    chassis.registry._run(record, "start", record.module.start)
    if record.state is ModuleState.RUNNING:
        _rewatch(chassis, record)
    return record.state is ModuleState.RUNNING


def _install(chassis: Chassis, old: RegisteredModule, prepared: _Prepared) -> RegisteredModule:
    """Replace the old record with a registration of the new instance."""
    registry = chassis.registry
    with registry._lock:
        del registry._records[old.spec.name]
        try:
            return registry.register(prepared.candidate.instance, prepared.candidate.spec)
        except Exception:
            registry._records[old.spec.name] = old
            raise


def _bring_up(chassis: Chassis, record: RegisteredModule) -> str:
    """Init and start the new instance; return '' or the reason it did not come up."""
    registry = chassis.registry
    registry._run(record, "init", partial(_call_init, record, chassis.context))
    if not record.initialized:
        return record.error or "init failed"
    registry.collect_declarations(record.spec.name)
    registry.collect_headless()
    if not _start(chassis, record):
        return record.error or "start failed"
    return ""


@dataclass(slots=True)
class _Plan:
    """Everything one reload works on: the old record, the new code, who restarts."""

    old: RegisteredModule
    prepared: _Prepared
    dependents: list[RegisteredModule]
    report: ReloadReport


def _swap(chassis: Chassis, plan: _Plan) -> ReloadReport:
    """Stop, replace, start; undo everything when the new instance cannot run."""
    old, report = plan.old, plan.report
    for record in (*reversed(plan.dependents), old):
        report.stopped.append(record.spec.name)
        if not _stop(chassis, record):
            _restore_code(plan.prepared.base, plan.prepared.saved)
            report.failed.append(record.spec.name)
            return report.refuse(
                f"module {record.spec.name!r} did not stop cleanly: {record.error}",
                "It is left as it is; restart the daemon to recover.",
            )
    try:
        new = _install(chassis, old, plan.prepared)
        problem = _bring_up(chassis, new)
    except Exception as exc:
        new, problem = None, f"{type(exc).__name__}: {exc}"
    if problem or new is None:
        return _roll_back(chassis, plan, new, problem or "no instance")
    _publish_declarations(chassis, old, new)
    for record in plan.dependents:
        (report.restarted if _start(chassis, record) else report.failed).append(record.spec.name)
    report.reloaded = True
    chassis.bus.publish(RELOADED_EVENT, report.as_dict(), source="chassis")
    chassis.logger.info(
        f"module {old.spec.name} reloaded", before=report.version_before,
        after=report.version_after, restarted=report.restarted, failed=report.failed,
    )
    return report


def _roll_back(
    chassis: Chassis, plan: _Plan, new: RegisteredModule | None, problem: str
) -> ReloadReport:
    """Put the old code and instance back after the new one failed to come up."""
    old, report = plan.old, plan.report
    if new is not None:
        _stop(chassis, new)
    _restore_code(plan.prepared.base, plan.prepared.saved)
    with chassis.registry._lock:
        chassis.registry._records[old.spec.name] = old
    back = _start(chassis, old)
    report.rolled_back = back
    for record in plan.dependents:
        if not _start(chassis, record):
            report.failed.append(record.spec.name)
    if not back:
        report.failed.append(old.spec.name)
    chassis.logger.error(f"module reload of {old.spec.name} rolled back: {problem}")
    return report.refuse(
        f"the new instance did not come up: {problem}",
        "The previous version runs again." if back else "The previous version did not restart.",
    )


def _publish_declarations(
    chassis: Chassis, old: RegisteredModule, new: RegisteredModule
) -> None:
    """Move the control plane from the old module's routes and mounts to the new one's."""
    for route in new.routes:
        chassis.register_ipc_route(route)
    fresh_paths = {route.path for route in new.routes}
    live = chassis.ipc_routes
    for route in old.routes:
        if route.path not in fresh_paths and any(item is route for item in live):
            chassis.unregister_ipc_route(route.path)
    server = chassis.ipc
    for mount in new.statics:
        kept = tuple(item for item in chassis.ipc_static if item.prefix != mount.prefix)
        chassis.ipc_static = (*kept, mount)
        if server is not None and server.running:
            server.add_static_dir(mount)
    fresh_prefixes = {mount.prefix for mount in new.statics}
    for mount in old.statics:
        if mount.prefix not in fresh_prefixes and any(m is mount for m in chassis.ipc_static):
            chassis.unregister_static_dir(mount.prefix)

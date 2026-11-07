"""'kdx config set|unset|schema': edit the local layer from the terminal or the console.

The terminal is the whole feature; the web console calls the same ``apply_edits`` over its
admin-only route, so a value accepted in one place is accepted in the other, and the same
audit line records who changed it.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..config.editor import (
    LOCAL_FILE_NAME,
    ConfigEditError,
    Declared,
    declared_keys,
    plan_edits,
    write_local,
)
from ..config.layered import ConfigSource, redact_for_display
from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.contract import Module
from ..kernel.ipc import RELOAD_PATH
from ..kernel.users import Caller
from .render import dump_json, print_key_values, print_table, tr, write_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

_JSON = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
_MODULE = CliArgument(
    ("--module",), metavar="NAME",
    help="The key belongs to this module (modules.NAME.KEY).",
)


def edit_commands() -> tuple[CliCommand, ...]:
    """Return the three subcommands that join the 'kdx config' group."""
    return (
        CliCommand(
            name="set",
            help="Set a key in the local layer (kdx.local.yml), checked against its declaration.",
            handler=_set_command,
            module_name="core",
            args=(
                CliArgument(("key",), metavar="KEY", help="A dotted key, e.g. core.log.level."),
                CliArgument(("value",), metavar="VALUE", help="The new value."),
                _MODULE,
                _JSON,
            ),
        ),
        CliCommand(
            name="unset",
            help="Remove a key from the local layer, so the layer below it applies again.",
            handler=_unset_command,
            module_name="core",
            args=(CliArgument(("key",), metavar="KEY", help="A dotted key."), _MODULE, _JSON),
        ),
        CliCommand(
            name="schema",
            help="List every key that can be set: type, default, choices, live, current value.",
            handler=_schema_command,
            module_name="core",
            args=(
                _MODULE,
                CliArgument(("--group",), metavar="PREFIX", help="Only keys starting with PREFIX."),
                _JSON,
            ),
        ),
    )


def _chassis(request: CliRequest) -> Chassis:
    chassis = request.options.get("chassis") or getattr(request.context, "chassis", None)
    if chassis is None:
        raise RuntimeError("config commands require a chassis bound to the request")
    return cast("Chassis", chassis)


def _reacts(module: Any) -> bool:
    """Return True when *module* overrides the reload hook (the base one only logs)."""
    hook = getattr(type(module), "on_config_reload", None)
    return callable(hook) and hook is not Module.on_config_reload


def editable_keys(chassis: Chassis) -> dict[str, Declared]:
    """Return every key the settings editor knows, by full dotted name."""
    reloadable = [
        record.spec.name
        for record in chassis.registry.ordered()
        if _reacts(record.module)
    ]
    return declared_keys(chassis.registry.schemas(), reloadable)


def local_file(chassis: Chassis) -> Path:
    """Return the local layer that is written: the loaded one, else ``kdx.local.yml``."""
    configured = chassis.config.local_config_file
    return Path(configured) if configured else Path(chassis.paths.root) / LOCAL_FILE_NAME


def _plain(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value


def schema_view(chassis: Chassis, *, module: str = "", group: str = "") -> list[dict[str, Any]]:
    """Return the declared keys joined with their current value and the layer that supplies it."""
    config = chassis.config
    rows: list[dict[str, Any]] = []
    for key, item in editable_keys(chassis).items():
        if module and item.module != module:
            continue
        if group and not key.startswith(group):
            continue
        row = item.as_dict()
        source = config.source_of(key)
        have = source is not None and source != ConfigSource.DEFAULT
        row["value"] = _plain(redact_for_display(key, config.get(key, item.field.default)))
        row["layer"] = source.label() if have and source is not None else "built-in default"
        row["overridden"] = bool(source is not None and source > ConfigSource.LOCAL)
        row["editable"] = not item.secret
        row["default"] = _plain(row["default"])
        rows.append(row)
    return rows


def apply_edits(
    chassis: Chassis,
    *,
    sets: dict[str, Any] | None = None,
    unsets: list[str] | None = None,
    module: str = "",
    reload: bool = False,
    caller: Caller | None = None,
) -> dict[str, Any]:
    """Validate and write a batch, optionally reload now, and say what each key became.

    Raises ConfigEditError before touching a file when any key is refused. With *reload*
    (the daemon applying its own change) the new layers are read at once, and a key says
    ``live`` when the running process took it, ``restart`` when it needs a restart;
    without it a key is only ``saved``.
    """
    plan = plan_edits(editable_keys(chassis), sets=sets, unsets=unsets or (), module=module)
    path = local_file(chassis)
    removed = write_local(path, plan)
    if chassis.paths.local_config_file is None:
        # The file did not exist when the daemon booted; teach it where to look on reload.
        chassis.paths = replace(chassis.paths, local_config_file=path)
        chassis.context.paths = chassis.paths
    if reload:
        chassis.reload_config()
    rows: list[dict[str, Any]] = []
    for key in (*plan.sets, *plan.unsets):
        item = plan.declared[key]
        adding = key in plan.sets
        source = chassis.config.source_of(key) if reload else None
        shadowed = bool(source is not None and source > ConfigSource.LOCAL)
        applied = "saved" if not reload else ("live" if item.live else "restart")
        rows.append(
            {
                "key": key,
                "action": "set" if adding else "unset",
                "value": _plain(redact_for_display(key, plan.sets.get(key))) if adding else None,
                "applied": "shadowed" if shadowed else applied,
                "changed": adding or key in removed,
            }
        )
    audit = getattr(chassis, "audit", None)
    if audit is not None:
        audit.record(
            "config", caller=caller, outcome="ok", command="config edit",
            detail={"set": {row["key"]: row["value"] for row in rows if row["action"] == "set"},
                    "unset": [row["key"] for row in rows if row["action"] == "unset"]},
        )
    return {"file": str(path), "reloaded": reload, "at": round(time.time(), 3), "keys": rows}


_NOTES = {
    "live": "applied now",
    "restart": "saved; needs a restart to take effect",
    "saved": "saved; applies on the next start or reload",
    "shadowed": "saved, but a higher layer (environment or CLI) still wins",
}


def _note_text(applied: str) -> str:
    """Return the human sentence for an outcome (the JSON keeps the English table above)."""
    if applied == "live":
        return tr("applied now")
    if applied == "restart":
        return tr("saved; needs a restart to take effect")
    if applied == "shadowed":
        return tr("saved, but a higher layer (environment or CLI) still wins")
    return tr("saved; applies on the next start or reload")


def _daemon_reload(request: CliRequest, chassis: Chassis) -> bool | None:
    """Ask a live daemon to re-read its layers; None when no daemon is running."""
    from .commands import _client_discovery  # circular at import time

    timeout = float(chassis.config.get_float("core.ipc.timeout", 2.0))
    discovery, _path = _client_discovery(request, chassis)
    if discovery is None or not discovery.is_process_alive():
        return None
    try:
        code, _payload = discovery.request(RELOAD_PATH, method="POST", timeout=timeout)
    except OSError:
        return None
    return code < 400


def _run_edit(request: CliRequest, *, sets: dict[str, Any], unsets: list[str]) -> int:
    chassis = _chassis(request)
    module = str(request.option("module") or "").strip()
    try:
        report = apply_edits(chassis, sets=sets, unsets=unsets, module=module)
    except ConfigEditError as exc:
        write_line(f"{TAG_ERROR} {exc}")
        if exc.hint:
            write_line(f"{TAG_NOTE} {exc.hint}")
        return int(ExitCode.USAGE_ERROR)
    reloaded = _daemon_reload(request, chassis)
    declared = editable_keys(chassis)
    for row in report["keys"]:
        if reloaded:
            row["applied"] = "live" if declared[row["key"]].live else "restart"
        row["note"] = _NOTES[row["applied"]]
    report["reloaded"] = bool(reloaded)
    if request.flag("as_json"):
        dump_json(report)
        return int(ExitCode.SUCCESS)
    for row in report["keys"]:
        verb = "set" if row["action"] == "set" else "removed"
        shown = f" = {row['value']}" if row["action"] == "set" else ""
        write_line(f"{TAG_OK} " + tr("{key}{shown} {verb} in {file}", key=row["key"], shown=shown,
                                      verb=verb, file=report["file"]))
        write_line(f"{TAG_NOTE} " + _note_text(row["applied"]))
    if reloaded is None:
        write_line(
            f"{TAG_NOTE} " + tr("no daemon is running; the change applies on the next start")
        )
    elif reloaded is False:
        write_line(
            f"{TAG_ERROR} " + tr("the daemon refused the reload; run kdx reload as an admin")
        )
    return int(ExitCode.SUCCESS)


def _set_command(request: CliRequest) -> int:
    return _run_edit(
        request,
        sets={str(request.option("key")): str(request.option("value"))},
        unsets=[],
    )


def _unset_command(request: CliRequest) -> int:
    return _run_edit(request, sets={}, unsets=[str(request.option("key"))])


def _schema_command(request: CliRequest) -> int:
    chassis = _chassis(request)
    module = str(request.option("module") or "").strip()
    group = str(request.option("group") or "").strip()
    rows = schema_view(chassis, module=module, group=group)
    if request.flag("as_json"):
        dump_json({"keys": rows})
        return int(ExitCode.SUCCESS)
    if not rows:
        write_line(f"{TAG_NOTE} " + tr("no declared key matches"))
        return int(ExitCode.SUCCESS)
    print_table(
        [
            [
                row["key"], row["type"], "yes" if row["live"] else "restart",
                "secret" if row["secret"] else str(row["value"]), row["layer"],
            ]
            for row in rows
        ],
        headers=("key", "type", "live", "value", "layer"),
    )
    print_key_values([("file written", str(local_file(chassis)))])
    return int(ExitCode.SUCCESS)

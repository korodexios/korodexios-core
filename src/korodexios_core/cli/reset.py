"""'kdx reset': clear the disposable development data, never the durable side.

The command lists first and deletes only with ``--apply``. The roots come from
configuration: ``core.reset.disposable`` (default: the data directory, ``var/lib``),
``core.reset.durable`` (extra roots that must never be touched, on top of the durable
directory ``data/``), and ``core.reset.keep`` (file names that stay, ``.gitkeep``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.ipc import DISCOVERY_NAME, IpcDiscovery
from ..kernel.reset import DEFAULT_KEEP, ResetError, ResetPlan, apply_reset, plan_reset
from .render import dump_json, print_table, tr, write_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

LISTED_ENTRIES = 40


def reset_command() -> CliCommand:
    """Return the 'kdx reset' command."""
    return CliCommand(
        name="reset",
        help="List, then with --apply delete, the disposable development data.",
        group="core",
        handler=_reset,
        remote=False,
        args=(
            CliArgument(
                ("--apply",),
                dest="apply",
                action="store_true",
                help="really delete (without it the command only lists what it would remove)",
            ),
            CliArgument(
                ("--json",),
                dest="as_json",
                action="store_true",
                help="Emit the plan and result as JSON.",
            ),
        ),
    )


def _chassis(request: CliRequest) -> Chassis:
    return cast("Chassis", request.options.get("chassis") or request.context.chassis)


def _reset(request: CliRequest) -> int:
    chassis = _chassis(request)
    paths = chassis.paths
    config = chassis.config
    disposable = [str(item) for item in config.get_list("core.reset.disposable", [])]
    if not disposable:
        disposable = [str(paths.data_dir)]
    durable = [
        str(paths.durable_dir),
        *(str(item) for item in config.get_list("core.reset.durable", [])),
    ]
    protected = [
        item
        for item in (
            paths.state_dir,
            paths.log_dir,
            paths.config_file,
            paths.local_config_file,
            paths.env_file,
        )
        if item is not None
    ]
    keep = [str(item) for item in config.get_list("core.reset.keep", list(DEFAULT_KEEP))]
    as_json = bool(request.flag("as_json"))
    try:
        plan = plan_reset(Path(paths.root), disposable, durable, protected, keep)
    except ResetError as exc:
        write_line(f"{TAG_ERROR} {exc}")
        return int(ExitCode.CONFIG_ERROR)
    if not request.flag("apply"):
        return _show(plan, as_json=as_json, applied=False)

    discovery = IpcDiscovery.load(Path(paths.state_dir) / DISCOVERY_NAME)
    if discovery is not None and discovery.is_process_alive():
        write_line(f"{TAG_ERROR} " + tr("the daemon is running; stop it first: kdx stop"))
        return int(ExitCode.ERROR)
    if plan.empty:
        return _show(plan, as_json=as_json, applied=True)

    from .ux import UserExperience

    ux = UserExperience(config)
    if ux.is_interactive() and not as_json:
        _show(plan, as_json=False, applied=False)
        if not ux.confirm(
            tr(
                "Delete {count} item(s) ({size} bytes)?",
                count=len(plan.entries),
                size=plan.total_bytes,
            ),
            default=False,
        ):
            write_line(f"{TAG_NOTE} " + tr("nothing was deleted"))
            return int(ExitCode.SUCCESS)
    result = apply_reset(Path(paths.root), plan)
    if as_json:
        dump_json({"plan": plan.as_dict(), "result": result.as_dict(), "applied": True})
    else:
        write_line(
            f"{TAG_OK} "
            + tr(
                "removed {count} item(s), freed {size} bytes",
                count=result.removed,
                size=result.freed,
            )
        )
        for failure in result.failures:
            write_line(f"{TAG_ERROR} {failure}")
    return int(ExitCode.ERROR) if result.failures else int(ExitCode.SUCCESS)


def _show(plan: ResetPlan, *, as_json: bool, applied: bool) -> int:
    if as_json:
        dump_json({"plan": plan.as_dict(), "applied": applied})
        return int(ExitCode.SUCCESS)
    for root in plan.roots:
        write_line(f"{TAG_NOTE} " + tr("disposable root: {root}", root=root))
    for root in plan.absent:
        write_line(f"{TAG_NOTE} " + tr("{root} does not exist; nothing to clear there", root=root))
    for root in plan.durable:
        write_line(f"{TAG_NOTE} " + tr("durable, never touched: {root}", root=root))
    if plan.empty:
        write_line(f"{TAG_OK} " + tr("nothing to remove"))
        return int(ExitCode.SUCCESS)
    shown = plan.entries[:LISTED_ENTRIES]
    print_table(
        [[entry.path, entry.kind, str(entry.size)] for entry in shown],
        ["Path", "Kind", "Bytes"],
    )
    if len(plan.entries) > len(shown):
        write_line(
            f"{TAG_NOTE} " + tr("... and {count} more", count=len(plan.entries) - len(shown))
        )
    write_line(
        f"{TAG_NOTE} "
        + tr(
            "{count} item(s), {size} bytes; run kdx reset --apply to delete them",
            count=len(plan.entries),
            size=plan.total_bytes,
        )
    )
    return int(ExitCode.SUCCESS)

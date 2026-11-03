"""'kdx limiter': look at rate limiters and start or end a cooldown from the terminal.

The state files live under the durable directory (``data/limiters``) and are shared with the
daemon through a named lock, so an operator can pause every outbound call of a running
project (``kdx limiter cooldown external-api --hours 24``) without touching its code.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

from ..constants import TAG_NOTE, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.limiter import LimiterRegistry, read_limiter_states
from .render import dump_json, print_table, tr, write_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

LIMITERS_DIR_NAME = "limiters"
JSON_ARG = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
NAME_ARG = CliArgument(("name",), metavar="NAME", help="Limiter name (see 'kdx limiter list').")


def limiter_command() -> CliCommand:
    """Return the 'kdx limiter' command group."""
    return CliCommand(
        name="limiter",
        help="Show rate limiters; start or end a cooldown.",
        group="core",
        remote=False,
        subcommands=(
            CliCommand(
                name="list",
                help="List limiters: calls today, budget left, cooldown.",
                handler=_list,
                group="core",
                remote=False,
                args=(JSON_ARG,),
            ),
            CliCommand(
                name="cooldown",
                help="Refuse every call of a limiter for a while (after a 429 or 503).",
                handler=_cooldown,
                group="core",
                remote=False,
                args=(
                    NAME_ARG,
                    CliArgument(
                        ("--hours",), type="float", default=0.0, help="Hours to cool down."
                    ),
                    CliArgument(
                        ("--minutes",), type="float", default=0.0, help="Minutes to cool down."
                    ),
                    CliArgument(("--reason",), default="operator", help="Why, shown in doctor."),
                    JSON_ARG,
                ),
            ),
            CliCommand(
                name="clear",
                help="End a cooldown early.",
                handler=_clear,
                group="core",
                remote=False,
                args=(NAME_ARG, JSON_ARG),
            ),
        ),
    )


def _registry(request: CliRequest) -> LimiterRegistry:
    chassis = cast("Chassis", request.options.get("chassis") or request.context.chassis)
    return LimiterRegistry(chassis.paths.durable_dir / LIMITERS_DIR_NAME)


def _list(request: CliRequest) -> int:
    rows = read_limiter_states(_registry(request).directory)
    if request.flag("as_json"):
        dump_json({"limiters": rows})
        return int(ExitCode.SUCCESS)
    if not rows:
        write_line(f"{TAG_NOTE} " + tr("no limiters recorded"))
        return int(ExitCode.SUCCESS)
    print_table(
        [
            [
                str(row["name"]),
                str(row["used_today"]),
                "-" if row["remaining_today"] is None else str(row["remaining_today"]),
                f"{row['cooldown_seconds']:.0f}s" if row["cooling_down"] else "-",
                str(row["cooldown_reason"] or "-"),
            ]
            for row in rows
        ],
        headers=("name", "used today", "left today", "cooldown", "reason"),
    )
    return int(ExitCode.SUCCESS)


def _cooldown(request: CliRequest) -> int:
    hours = float(request.option("hours") or 0.0)
    minutes = float(request.option("minutes") or 0.0)
    if hours <= 0 and minutes <= 0:
        write_line(f"{TAG_NOTE} " + tr("give --hours or --minutes"))
        return int(ExitCode.USAGE_ERROR)
    limiter = _registry(request).get(str(request.option("name")), declared=False)
    until = limiter.cooldown(hours, minutes=minutes, reason=str(request.option("reason") or ""))
    if request.flag("as_json"):
        dump_json({"name": limiter.name, "cooldown_until": until})
    else:
        stamp = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S")
        message = tr("{name} cooling down until {when}", name=limiter.name, when=stamp)
        write_line(f"{TAG_OK} {message}")
    return int(ExitCode.SUCCESS)


def _clear(request: CliRequest) -> int:
    limiter = _registry(request).get(str(request.option("name")), declared=False)
    had = limiter.clear_cooldown()
    if request.flag("as_json"):
        dump_json({"name": limiter.name, "was_cooling_down": had})
    else:
        if had:
            write_line(f"{TAG_OK} " + tr("{name} cooldown ended", name=limiter.name))
        else:
            write_line(f"{TAG_NOTE} " + tr("{name} was not cooling down", name=limiter.name))
    return int(ExitCode.SUCCESS)

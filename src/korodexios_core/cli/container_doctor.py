"""'kdx container': read what the host and user look like, then propose - never change.

``kdx container doctor`` reports who runs the stack (uid, gid), how ids are mapped in this user
namespace, whether a rootless engine has usable subordinate ids, and, when it has not, a free
range and the exact command an administrator would run. ``kdx container env`` prints (or adds
to ``.env``) the ``KDX_UID``/``KDX_GID`` lines compose passes to the image build, so bind-mounted
directories belong to the person running the stack. Neither command edits a system file or an
existing account: an id somebody else uses is never proposed, and an existing ``.env`` key is
never overwritten.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.container_probe import probe_container_host
from .render import dump_json, print_key_values, tr, write_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

JSON_ARG = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")


def container_command() -> CliCommand:
    """Return the 'kdx container' command group."""
    return CliCommand(
        name="container",
        help="Check user ids and container engines; propose settings, change nothing.",
        group="core",
        remote=False,
        subcommands=(
            CliCommand(
                name="doctor",
                help="Report uid, gid, id mapping and rootless readiness; propose a free "
                "subuid range if one is needed.",
                handler=_doctor,
                group="core",
                remote=False,
                args=(JSON_ARG,),
            ),
            CliCommand(
                name="env",
                help="Print KDX_UID and KDX_GID for compose; --write adds missing keys to .env.",
                handler=_env,
                group="core",
                remote=False,
                args=(
                    CliArgument(
                        ("--write",), dest="write", action="store_true",
                        help="Add the keys .env lacks (never overwrites).",
                    ),
                    JSON_ARG,
                ),
            ),
        ),
    )


def _doctor(request: CliRequest) -> int:
    report = probe_container_host()
    if request.flag("as_json"):
        dump_json(report)
        return int(ExitCode.SUCCESS)
    user = report["user"]
    mapping = "host (no restriction)"
    if report["namespace"]["restricted"]:
        mapping = "restricted namespace: " + ", ".join(
            f"{m['first']}-{m['first'] + m['count'] - 1}" for m in report["namespace"]["mapped"]
        )
    container_user = f"{report['container_user']['uid']}:{report['container_user']['gid']}"
    print_key_values(
        [
            ("user", f"{user['name'] or '?'} (uid {user['uid']}, gid {user['gid']})"),
            ("id mapping", mapping),
            ("subuid", ", ".join(f"{s['first']}-{s['first'] + s['count'] - 1}"
                                 for s in report["subuid"]) or "none"),
            ("engines", ", ".join(report["engines"]) or "none found"),
            ("container user", container_user),
        ]
    )
    if report["rootless_ready"]:
        write_line(f"{TAG_OK} " + tr("a rootless engine has the ids it needs"))
        return int(ExitCode.SUCCESS)
    for problem in report["problems"]:
        write_line(f"{TAG_ERROR} {problem}")
    advice = report["advice"]
    if advice:
        write_line(
            f"{TAG_NOTE} " + tr("a free range that nobody uses (review it, then run as admin):")
        )
        write_line(f"    {advice['command']}")
        if advice.get("fallback"):
            write_line(
                f"{TAG_NOTE} " + tr("if usermod refuses (the user has running processes), "
                                    "replace the two lines directly:")
            )
            write_line(f"    {advice['fallback']}")
        write_line(f"{TAG_NOTE} {advice['note']}")
    return int(ExitCode.ERROR)


def _env(request: CliRequest) -> int:
    report = probe_container_host()
    ids = report["container_user"]
    values = {"KDX_UID": str(ids["uid"]), "KDX_GID": str(ids["gid"])}
    written: list[str] = []
    if request.flag("write"):
        chassis = cast("Chassis", request.options.get("chassis") or request.context.chassis)
        target = Path(chassis.paths.root) / ".env"
        existing = target.read_text(encoding="utf-8") if target.is_file() else ""
        have = {line.split("=", 1)[0].strip() for line in existing.splitlines() if "=" in line}
        missing = [f"{key}={value}" for key, value in values.items() if key not in have]
        if missing:
            prefix = "" if not existing or existing.endswith("\n") else "\n"
            with target.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(prefix + "\n".join(missing) + "\n")
            written = [line.split("=", 1)[0] for line in missing]
    if request.flag("as_json"):
        dump_json({"values": values, "written": written})
        return int(ExitCode.SUCCESS)
    for key, value in values.items():
        write_line(f"{key}={value}")
    if request.flag("write"):
        keys = ", ".join(written) or "nothing new"
        write_line(f"{TAG_OK} " + tr("added to .env: {keys}", keys=keys))
    return int(ExitCode.SUCCESS)


__all__ = ["container_command"]

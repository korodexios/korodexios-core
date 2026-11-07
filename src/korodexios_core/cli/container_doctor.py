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

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.container_probe import probe_container_host
from .render import dump_json, print_key_values, tr, write_line
from .ux import UserExperience

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
                name="fix",
                help="Set up rootless-engine ids: explains why sudo is needed, shows the "
                "commands, asks, then lets sudo ask for the password.",
                handler=_fix,
                group="core",
                remote=False,
                args=(),
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
    for note in report["notes"]:
        write_line(f"{TAG_NOTE} {note}")
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
        write_line(f"{TAG_NOTE} " + tr("'kdx container fix' explains and runs this for you"))
    return int(ExitCode.ERROR)


def run_step(argv: list[str]) -> int:
    """Run one command with the terminal attached, so sudo asks for the password itself.

    The chassis never reads, stores or passes on a password: the child owns the terminal.
    """
    return subprocess.run(argv, check=False).returncode


def _fix(request: CliRequest) -> int:
    report = probe_container_host()
    if report["rootless_ready"]:
        write_line(f"{TAG_OK} " + tr("nothing to fix: a rootless engine has the ids it needs"))
        return int(ExitCode.SUCCESS)
    advice = report["advice"]
    for problem in report["problems"]:
        write_line(f"{TAG_ERROR} {problem}")
    if not advice or not advice["steps"]:
        write_line(
            f"{TAG_NOTE} " + tr("no safe automatic change is possible; see 'kdx container doctor'")
        )
        return int(ExitCode.ERROR)
    steps = [list(step) for step in advice["steps"]]
    if report["user"]["root"]:
        steps = [step[1:] if step and step[0] == "sudo" else step for step in steps]
    write_line(f"{TAG_NOTE} " + tr(
        "why sudo: /etc/subuid and /etc/subgid are owned by root and say which user ids "
        "your containers may use; no other file is changed"))
    write_line(f"{TAG_NOTE} " + tr("the free range was chosen so that no account or other "
                                   "user's range is touched. The commands:"))
    for step in steps:
        write_line("    " + " ".join(step))
    write_line(
        f"{TAG_NOTE} " + tr("sudo asks for your password itself; this program never sees it")
    )
    chassis = cast("Chassis", request.options.get("chassis") or request.context.chassis)
    ux = UserExperience(chassis.config)
    if not ux.confirm(tr("Run these commands now?"), default=False):
        write_line(f"{TAG_NOTE} " + tr("nothing was changed; run the commands yourself when ready"))
        return int(ExitCode.ERROR)
    if not report["user"]["root"] and shutil.which("sudo") is None:
        write_line(
            f"{TAG_ERROR} " + tr("sudo is not installed; run the commands as an administrator")
        )
        return int(ExitCode.ERROR)
    for step in steps:
        code = run_step(step)
        if code != 0:
            message = tr("a command failed (exit {code}); nothing further was run", code=code)
            write_line(f"{TAG_ERROR} {message}")
            return int(ExitCode.ERROR)
    after = probe_container_host()
    if not after["rootless_ready"]:
        write_line(f"{TAG_ERROR} " + tr("the ids are still not usable; run 'kdx container doctor'"))
        return int(ExitCode.ERROR)
    if "podman" in after["engines"]:
        run_step(["podman", "system", "migrate"])  # as this user: re-reads the new ranges
    write_line(f"{TAG_OK} " + tr("done: a rootless engine now has the ids it needs"))
    for note in after["notes"]:
        write_line(f"{TAG_NOTE} {note}")
    return int(ExitCode.SUCCESS)


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

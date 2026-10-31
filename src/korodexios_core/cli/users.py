"""'kdx users' and 'kdx audit': manage who may use the control plane, and see what they did.

Both work on files under the durable directory, so they need shell access to the host -
which is the point: whoever can run them already owns the machine. A running daemon
picks up a user change on its next request; no reload, no restart.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..kernel.audit import AUDIT_FILE_NAME, read_audit
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.users import ROLES, UserError, UserStore
from .render import dump_json, print_key_values, print_table, tr, write_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

JSON_ARG = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
NAME_ARG = CliArgument(("name",), metavar="NAME", help="User name (a-z, 0-9, '.', '_', '-').")


def users_command() -> CliCommand:
    """Return the 'kdx users' command group."""
    role_help = f"Role: {', '.join(ROLES)}."
    return CliCommand(
        name="users",
        help="Manage named control-plane users and their roles.",
        group="core",
        subcommands=(
            CliCommand(
                name="list",
                help="List users and their roles.",
                handler=_list,
                group="core",
                args=(JSON_ARG,),
            ),
            CliCommand(
                name="add",
                help="Create a user; prints the new token once.",
                handler=_add,
                group="core",
                args=(
                    NAME_ARG,
                    CliArgument(
                        ("--role",), choices=ROLES, default="operator", help=role_help
                    ),
                    JSON_ARG,
                ),
            ),
            CliCommand(
                name="remove",
                help="Delete a user; their token stops working at once.",
                handler=_remove,
                group="core",
                args=(NAME_ARG,),
            ),
            CliCommand(
                name="rotate",
                help="Issue a new token for a user; the old one stops working.",
                handler=_rotate,
                group="core",
                args=(NAME_ARG, JSON_ARG),
            ),
            CliCommand(
                name="role",
                help="Change a user's role.",
                handler=_role,
                group="core",
                args=(
                    NAME_ARG,
                    CliArgument(("role",), metavar="ROLE", choices=ROLES, help=role_help),
                ),
            ),
        ),
    )


def audit_command() -> CliCommand:
    """Return 'kdx audit': the newest control-plane actions, newest last."""
    return CliCommand(
        name="audit",
        help="Show who acted over the control plane (commands, jobs, reloads, stops).",
        handler=_audit,
        group="core",
        args=(
            CliArgument(
                ("-n", "--lines"), dest="lines", type="int", default=50,
                help="How many records to show [50].",
            ),
            CliArgument(("--user",), dest="user", help="Only this user's records."),
            CliArgument(
                ("--action",), dest="action",
                choices=("rpc", "job", "reload", "shutdown"),
                help="Only this kind of action.",
            ),
            JSON_ARG,
        ),
    )


def _chassis(request: CliRequest) -> Chassis:
    return cast("Chassis", request.options.get("chassis") or request.context.chassis)


def _store(request: CliRequest) -> UserStore:
    store = _chassis(request).users
    if store is None:  # pragma: no cover - the chassis always attaches one
        raise UserError("this chassis has no user store")
    return store


def _when(stamp: float) -> str:
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S") if stamp else "-"


def _run(action: Any) -> int:
    try:
        return int(action())
    except UserError as exc:
        write_line(f"{TAG_ERROR} {exc}")
        return int(ExitCode.ERROR)


def _list(request: CliRequest) -> int:
    def act() -> int:
        users = _store(request).users()
        if request.flag("as_json"):
            dump_json({"users": [user.as_dict() for user in users]})
            return 0
        if not users:
            write_line(f"{TAG_NOTE} no named users yet; the shared core.ipc.token is the owner")
            write_line(f"{TAG_NOTE} add one with: kdx users add NAME --role operator")
            return 0
        print_table(
            [[user.name, user.role, _when(user.created_at)] for user in users],
            headers=("name", "role", "created"),
        )
        write_line(f"{TAG_NOTE} the shared core.ipc.token is the built-in admin 'owner'")
        return 0

    return _run(act)


def _show_token(request: CliRequest, name: str, role: str, token: str, verb: str) -> None:
    if request.flag("as_json"):
        dump_json({"name": name, "role": role, "token": token})
        return
    print_key_values([("user", name), ("role", role), ("token", token)])
    write_line(
        f"{TAG_OK} "
        + tr("{verb}; this token is shown only now - hand it over privately", verb=verb)
    )
    write_line(f"{TAG_NOTE} terminal: kdx status --target http://HOST:8123 --token TOKEN")
    write_line(f"{TAG_NOTE} browser:  open http://HOST:8123/ui/ and paste the token")


def _add(request: CliRequest) -> int:
    def act() -> int:
        name = str(request.option("name"))
        role = str(request.option("role", "operator"))
        token = _store(request).add(name, role)
        _show_token(request, name, role, token, f"user {name} created")
        return 0

    return _run(act)


def _remove(request: CliRequest) -> int:
    def act() -> int:
        name = str(request.option("name"))
        _store(request).remove(name)
        write_line(
            f"{TAG_OK} "
            + tr("user {name} removed; their token no longer works", name=name)
        )
        return 0

    return _run(act)


def _rotate(request: CliRequest) -> int:
    def act() -> int:
        name = str(request.option("name"))
        store = _store(request)
        token = store.rotate(name)
        user = store.get(name)
        _show_token(request, name, user.role if user else "-", token, "new token issued")
        return 0

    return _run(act)


def _role(request: CliRequest) -> int:
    def act() -> int:
        updated = _store(request).set_role(str(request.option("name")), str(request.option("role")))
        write_line(f"{TAG_OK} " + tr("{name} is now {role}", name=updated.name, role=updated.role))
        return 0

    return _run(act)


def _audit(request: CliRequest) -> int:
    chassis = _chassis(request)
    path = Path(chassis.paths.durable_dir) / AUDIT_FILE_NAME
    entries = read_audit(
        path,
        limit=int(request.option("lines", 50)),
        user=str(request.option("user") or ""),
        action=str(request.option("action") or ""),
    )
    if request.flag("as_json"):
        dump_json({"path": str(path), "entries": entries})
        return 0
    if not entries:
        write_line(f"{TAG_NOTE} " + tr("no audit records yet in {path}", path=path))
        return 0
    print_table(
        [
            [
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(entry.get("at", 0)))),
                str(entry.get("user") or "-"),
                str(entry.get("action", "-")),
                str(entry.get("command", "")) or "-",
                str(entry.get("outcome", "-")),
                str(entry.get("address", "")) or "-",
            ]
            for entry in entries
        ],
        headers=("time", "user", "action", "command", "outcome", "from"),
    )
    return 0

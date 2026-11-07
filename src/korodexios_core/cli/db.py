"""'kdx db': look into the project's databases from the terminal (read-only by design).

A database exists only when a module asked for one (``context.db()``), so this command lists
what is really there - often nothing. ``query`` opens the file read-only and accepts exactly
one statement, so an agent can inspect state without any way to change it; schema changes
belong to a module's migrations and data changes to the module's own commands.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..exceptions import KorodexiosError
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.database import (
    DB_DIR_NAME,
    DEFAULT_DATABASE,
    Database,
    DatabaseError,
    databases_in,
    split_statements,
    validate_database_name,
)
from .render import dump_json, print_key_values, print_table, tr, write_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

JSON_ARG = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
NAME_ARG = CliArgument(
    ("name",), nargs="?", default=DEFAULT_DATABASE, metavar="NAME",
    help="Database name (see 'kdx db list') [main].",
)
DEFAULT_LIMIT = 100


def db_command() -> CliCommand:
    """Return the 'kdx db' command group."""
    return CliCommand(
        name="db",
        help="Inspect the project's SQLite databases (they exist only when a module asks).",
        subcommands=(
            CliCommand(
                name="list", help="List the databases with size and schema version.",
                handler=_list, args=(JSON_ARG,), group="core", remote=False,
            ),
            CliCommand(
                name="show", help="Show a database's tables, columns and row counts.",
                handler=_show, args=(NAME_ARG, JSON_ARG), group="core", remote=False,
            ),
            CliCommand(
                name="query",
                help="Run ONE read-only SQL statement (SELECT, PRAGMA ...) and print the rows.",
                handler=_query,
                args=(
                    NAME_ARG,
                    CliArgument(("sql",), metavar="SQL", help="One statement, e.g. 'SELECT 1'."),
                    CliArgument(("--limit",), type="int", default=DEFAULT_LIMIT,
                                help="Most rows to print [100]."),
                    JSON_ARG,
                ),
                group="core", remote=False,
            ),
            CliCommand(
                name="backup",
                help="Copy a database with SQLite's online backup (safe while it is in use).",
                handler=_backup,
                args=(
                    NAME_ARG,
                    CliArgument(("--to",), dest="target", metavar="FILE",
                                help="Target file (default: data/db/backups/NAME-<time>.sqlite3)."),
                    JSON_ARG,
                ),
                group="core", remote=False,
            ),
        ),
        group="core", remote=False,
    )


def _directory(request: CliRequest) -> Any:
    chassis = cast("Chassis", request.options.get("chassis") or request.context.chassis)
    return chassis.paths.durable_dir / DB_DIR_NAME


def _database(request: CliRequest) -> Database:
    name = validate_database_name(str(request.option("name") or DEFAULT_DATABASE))
    return Database(_directory(request) / f"{name}.sqlite3", name)


def _fail(error: KorodexiosError) -> int:
    write_line(f"{TAG_ERROR} {error}")
    if getattr(error, "hint", None):
        write_line(f"{TAG_NOTE} {error.hint}")
    return int(ExitCode.ERROR)


def _list(request: CliRequest) -> int:
    rows = [
        {
            "name": db.name,
            "size_bytes": db.path.stat().st_size,
            "schema_version": db.schema_version(),
        }
        for db in databases_in(_directory(request))
    ]
    if request.flag("as_json"):
        dump_json({"databases": rows})
        return int(ExitCode.SUCCESS)
    if not rows:
        write_line(f"{TAG_NOTE} " + tr("no databases: no module has asked for one"))
        return int(ExitCode.SUCCESS)
    print_table(
        [[row["name"], str(row["size_bytes"]), str(row["schema_version"])] for row in rows],
        headers=("name", "bytes", "schema version"),
    )
    return int(ExitCode.SUCCESS)


def _show(request: CliRequest) -> int:
    try:
        view = _database(request).describe()
    except KorodexiosError as exc:
        return _fail(exc)
    if request.flag("as_json"):
        dump_json(view)
        return int(ExitCode.SUCCESS)
    if not view["exists"]:
        write_line(f"{TAG_NOTE} " + tr("database {name} does not exist yet", name=view["name"]))
        return int(ExitCode.SUCCESS)
    print_key_values(
        [("path", view["path"]), ("bytes", view["size_bytes"]),
         ("schema version", view["schema_version"])]
    )
    print_table(
        [
            [t["name"], str(t["rows"]), ", ".join(
                f"{c['name']} {c['type']}".strip() for c in t["columns"])]
            for t in view["tables"]
        ],
        headers=("table", "rows", "columns"),
    )
    return int(ExitCode.SUCCESS)


def _query(request: CliRequest) -> int:
    sql = str(request.option("sql") or "")
    try:
        if len(split_statements(sql if sql.rstrip().endswith(";") else sql + ";")) != 1:
            raise DatabaseError("give exactly one statement", hint="'kdx db query' is read-only")
        limit = max(1, int(request.option("limit") or DEFAULT_LIMIT))
        rows = _database(request).query(sql, limit=limit)
    except KorodexiosError as exc:
        return _fail(exc)
    except Exception as exc:  # sqlite3.Error: bad SQL, or a write refused by query_only
        write_line(f"{TAG_ERROR} {exc}")
        return int(ExitCode.ERROR)
    if request.flag("as_json"):
        dump_json({"rows": rows})
        return int(ExitCode.SUCCESS)
    if not rows:
        write_line(f"{TAG_NOTE} " + tr("no rows"))
        return int(ExitCode.SUCCESS)
    headers = tuple(rows[0])
    print_table([[str(row[h]) for h in headers] for row in rows], headers=headers)
    return int(ExitCode.SUCCESS)


def _backup(request: CliRequest) -> int:
    database = _database(request)
    target_text = str(request.option("target") or "")
    if target_text:
        from pathlib import Path

        target = Path(target_text).expanduser()
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = _directory(request) / "backups" / f"{database.name}-{stamp}.sqlite3"
    try:
        written = database.backup(target)
    except KorodexiosError as exc:
        return _fail(exc)
    if request.flag("as_json"):
        dump_json({"name": database.name, "backup": str(written)})
    else:
        write_line(f"{TAG_OK} " + tr("{name} backed up to {path}", name=database.name,
                                     path=written))
    return int(ExitCode.SUCCESS)


__all__ = ["db_command"]

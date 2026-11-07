"""A small durable database for modules that need one: SQLite, stdlib only, opt-in.

Nothing here runs unless a module asks: ``context.db()`` hands out a handle and the file
(``data/db/<name>.sqlite3``) is only created by the first statement. A project that never
calls it has no database and pays nothing, so the question "do we need a DB?" stays with the
person or the agent building the project - ``kdx ui guide`` and ``docs/AGENTS_GUIDE.md`` say
when one earns its place (relations, queries, many rows, several writers) and when a config
key, the GUI preferences or a small JSON file is enough.

The handle opens a connection per call (like the job queue), so it is safe from any thread
and from the CLI, and there is nothing to close at shutdown. Schema changes are ordered
migrations recorded in ``schema_migrations``::

    db = context.db()                      # data/db/main.sqlite3
    db.migrate([(1, "CREATE TABLE note (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")])
    db.execute("INSERT INTO note (body) VALUES (?)", ("hello",))
    rows = db.query("SELECT * FROM note WHERE body = ?", ("hello",))
    with db.transaction() as tx:           # all or nothing
        tx.execute("UPDATE note SET body = ? WHERE id = ?", ("hi", 1))
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from ..exceptions import KorodexiosError

DB_DIR_NAME = "db"
DEFAULT_DATABASE = "main"
SUFFIX = ".sqlite3"
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_MIGRATIONS_TABLE = "schema_migrations"
_SYSTEM_TABLES = frozenset({_MIGRATIONS_TABLE})


class DatabaseError(KorodexiosError):
    """A database name, migration or statement was not acceptable."""


def validate_database_name(name: str) -> str:
    """Return *name* when it can name a database file, else raise :class:`DatabaseError`."""
    if not _NAME.match(str(name)):
        raise DatabaseError(
            f"{name!r} is not a usable database name",
            hint="lowercase letters, digits, '-' or '_', starting with a letter, e.g. 'main'",
        )
    return str(name)


def split_statements(script: str) -> list[str]:
    """Split a SQL script into complete statements (triggers and strings stay whole).

    The last statement may omit its ';'. An unterminated string, comment or trigger body
    raises :class:`DatabaseError` instead of being sent to SQLite half-written.
    """
    statements: list[str] = []
    current = ""
    for char in script:
        current += char
        if char == ";" and sqlite3.complete_statement(current):
            statements.append(current.strip())
            current = ""
    rest = current.strip()
    if rest:
        if not sqlite3.complete_statement(rest + ";"):
            raise DatabaseError("the SQL ends in an incomplete statement", hint="close the quote")
        statements.append(rest)
    return [statement for statement in statements if statement.rstrip("; \n")]


_READ_PRAGMAS = frozenset({
    "table_info", "table_xinfo", "table_list", "index_list", "index_info", "index_xinfo",
    "foreign_key_list", "database_list", "schema_version", "user_version", "integrity_check",
    "quick_check", "page_count", "page_size", "freelist_count", "compile_options",
    "collation_list", "function_list", "foreign_keys",
})
_ALLOWED_ACTIONS = frozenset({
    sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE,
})


def _enable_wal(connection: sqlite3.Connection) -> None:
    """Switch to WAL once; switching needs an exclusive lock and ignores the busy timeout.

    Several first connections opening a new file at the same moment would otherwise fail
    with 'database is locked', so the mode is only set when it is not WAL yet, and a lost
    race is retried briefly (whoever won has already switched it).
    """
    for _ in range(100):
        if str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
            return
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            time.sleep(0.02)
    connection.execute("PRAGMA journal_mode=WAL")  # still busy after ~2s: raise the real error


def _read_only_authorizer(action: int, arg1: Any, arg2: Any, db_name: Any, source: Any) -> int:
    """Allow reads and a short list of inspection PRAGMAs; deny everything else."""
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and str(arg1).lower() in _READ_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class Database:
    """One SQLite file with ordered migrations; a connection is opened per call."""

    def __init__(self, path: Path, name: str = DEFAULT_DATABASE) -> None:
        self.name = name
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """Return the database file (it may not exist yet)."""
        return self._path

    def exists(self) -> bool:
        """Return True once a statement created the file."""
        return self._path.is_file()

    # -- connections -------------------------------------------------------------

    @contextlib.contextmanager
    def connect(self, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a connection (rows are ``sqlite3.Row``); autocommit unless a transaction runs."""
        if not readonly:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        elif not self._path.is_file():
            raise DatabaseError(
                f"database {self.name!r} does not exist yet",
                hint="a module creates it with context.db().migrate(...)",
            )
        if readonly:
            # opened read-only at the file level, plus an authorizer: no write, ATTACH or
            # file-creating statement (VACUUM INTO) gets through, whatever the SQL says
            connection = sqlite3.connect(
                f"{self._path.resolve().as_uri()}?mode=ro", uri=True, timeout=30.0,
                isolation_level=None,
            )
        else:
            connection = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            if readonly:
                connection.set_authorizer(_read_only_authorizer)  # after our own PRAGMA above
            else:
                _enable_wal(connection)
            yield connection
        finally:
            connection.close()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[_Transaction]:
        """Run statements all or nothing: commit on success, roll back on any exception."""
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield _Transaction(connection)
            except BaseException:
                # SQLite may already have ended the transaction (disk full, I/O error): the
                # ROLLBACK error must not replace the real one
                with contextlib.suppress(sqlite3.Error):
                    connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    # -- statements --------------------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> int:
        """Run one statement; return the number of rows it changed."""
        with self.connect() as connection:
            return connection.execute(sql, params).rowcount

    def insert(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> int:
        """Run an INSERT; return the new row id."""
        with self.connect() as connection:
            return int(connection.execute(sql, params).lastrowid or 0)

    def query(
        self, sql: str, params: Sequence[Any] | dict[str, Any] = (), *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Run a read-only statement; return rows as dicts (at most *limit*)."""
        with self.connect(readonly=True) as connection:
            cursor = connection.execute(sql, params)
            rows = cursor.fetchmany(limit) if limit else cursor.fetchall()
            return [dict(row) for row in rows]

    def one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> dict[str, Any] | None:
        """Return the first row of a read-only statement, or None."""
        rows = self.query(sql, params, limit=1)
        return rows[0] if rows else None

    # -- migrations --------------------------------------------------------------

    def migrate(self, steps: Sequence[tuple[int, str]]) -> list[int]:
        """Apply every step whose version is not recorded yet, in order; return what ran.

        A step is ``(version, sql)``; versions are positive, unique and written in
        ascending order. Each step runs in its own transaction together with the record
        that it ran, so a failing step leaves the database at the previous version.
        """
        ordered = list(steps)
        versions = [version for version, _ in ordered]
        if any(not isinstance(v, int) or v < 1 for v in versions) or versions != sorted(
            set(versions)
        ):
            raise DatabaseError(
                "migration versions must be unique positive integers in ascending order",
                hint="e.g. [(1, 'CREATE TABLE ...'), (2, 'ALTER TABLE ...')]",
            )
        with self.connect() as connection:
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {_MIGRATIONS_TABLE} "
                "(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
            )
            done = {row["version"] for row in connection.execute(
                f"SELECT version FROM {_MIGRATIONS_TABLE}")}
        applied: list[int] = []
        for version, sql in ordered:
            if version in done:
                continue
            try:
                with self.transaction() as tx:
                    # another process may have applied this step since the list was read
                    if tx.query(f"SELECT 1 FROM {_MIGRATIONS_TABLE} WHERE version = ?", (version,)):
                        continue
                    for statement in split_statements(sql):
                        tx.execute(statement)
                    tx.execute(
                        f"INSERT INTO {_MIGRATIONS_TABLE} (version, applied_at) VALUES (?, ?)",
                        (version, time.time()),
                    )
            except sqlite3.Error as exc:
                raise DatabaseError(
                    f"migration {version} of database {self.name!r} failed: {exc}",
                    hint="the database stays at the previous version; fix the SQL and rerun",
                ) from exc
            applied.append(version)
        return applied

    def schema_version(self) -> int:
        """Return the highest applied migration (0 when none, or no database yet)."""
        if not self.exists():
            return 0
        try:
            row = self.one(f"SELECT MAX(version) AS v FROM {_MIGRATIONS_TABLE}")
        except sqlite3.Error:
            return 0
        return int(row["v"] or 0) if row else 0

    # -- inspection --------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """Return size, schema version and every table with its columns and row count."""
        tables: list[dict[str, Any]] = []
        if self.exists():
            with self.connect(readonly=True) as connection:
                names = [
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table' "
                        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    )
                ]
                for table in names:
                    quoted = '"' + table.replace('"', '""') + '"'
                    columns = [
                        {"name": c["name"], "type": c["type"], "primary_key": bool(c["pk"]),
                         "not_null": bool(c["notnull"])}
                        for c in connection.execute(f"PRAGMA table_info({quoted})")
                    ]
                    count = connection.execute(f"SELECT COUNT(*) AS n FROM {quoted}").fetchone()
                    tables.append(
                        {"name": table, "rows": int(count["n"]), "columns": columns,
                         "system": table in _SYSTEM_TABLES}
                    )
        return {
            "name": self.name,
            "path": str(self._path),
            "exists": self.exists(),
            "size_bytes": self._path.stat().st_size if self.exists() else 0,
            "schema_version": self.schema_version(),
            "tables": tables,
        }

    def backup(self, target: Path) -> Path:
        """Copy the live database to *target* with SQLite's online backup (safe while in use)."""
        if not self.exists():
            raise DatabaseError(f"database {self.name!r} does not exist yet")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise DatabaseError(f"{target} already exists", hint="pick another file name")
        with self.connect(readonly=True) as source:
            destination = sqlite3.connect(target)
            try:
                source.backup(destination)
            finally:
                destination.close()
        return target


class _Transaction:
    """The statements of one open transaction (see :meth:`Database.transaction`)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> int:
        """Run one statement inside the transaction; return the rows it changed."""
        return self._connection.execute(sql, params).rowcount

    def insert(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> int:
        """Run an INSERT inside the transaction; return the new row id."""
        return int(self._connection.execute(sql, params).lastrowid or 0)

    def query(
        self, sql: str, params: Sequence[Any] | dict[str, Any] = ()
    ) -> list[dict[str, Any]]:
        """Read rows inside the transaction (it sees its own uncommitted changes)."""
        return [dict(row) for row in self._connection.execute(sql, params).fetchall()]


def databases_in(directory: Path) -> list[Database]:
    """Return the databases present under *directory* (``data/db``), by name."""
    if not Path(directory).is_dir():
        return []
    return [
        Database(path, path.name[: -len(SUFFIX)])
        for path in sorted(Path(directory).glob(f"*{SUFFIX}"))
        if _NAME.match(path.name[: -len(SUFFIX)])
    ]


__all__ = [
    "DB_DIR_NAME",
    "DEFAULT_DATABASE",
    "Database",
    "DatabaseError",
    "databases_in",
    "split_statements",
    "validate_database_name",
]

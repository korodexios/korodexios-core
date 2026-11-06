"""A durable work queue: tasks that survive a restart and report where they are.

``JobRegistry`` (``kernel/jobs.py``) runs a delegated command on a thread and forgets it
when the daemon stops. Long work that must outlive a restart needs more: a record on
disk, a state anyone can read (``queued``, ``running``, ``done``, ``failed``,
``cancelled``), a count of attempts, and a retry with backoff when a handler raises.

The store is one SQLite file (stdlib ``sqlite3``) under the durable directory, so a
daemon, a ``kdx queue submit`` in another terminal, and a remote caller on the control
plane all see the same queue. Claiming a task is one ``BEGIN IMMEDIATE`` transaction,
so two workers - even in two processes - never take the same task.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .instance_lock import pid_alive

__all__ = [
    "ACTIVE_STATES",
    "FINAL_STATES",
    "STATES",
    "JobQueue",
    "PermanentJobError",
    "QueueTask",
]

STATES: Final[tuple[str, ...]] = ("queued", "running", "done", "failed", "cancelled")
FINAL_STATES: Final[frozenset[str]] = frozenset({"done", "failed", "cancelled"})
ACTIVE_STATES: Final[frozenset[str]] = frozenset({"queued", "running"})
DEFAULT_MAX_ATTEMPTS: Final[int] = 3
DEFAULT_BACKOFF: Final[float] = 30.0
MAX_BACKOFF: Final[float] = 3600.0
MAX_TEXT: Final[int] = 4000
"""Longest result or error text kept per task; the log carries the rest."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT 'null',
    state TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    run_after REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    result TEXT,
    error TEXT,
    user TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS tasks_state ON tasks (state, run_after);
"""

_MIGRATIONS: Final[tuple[tuple[str, str], ...]] = (
    ("owner", "ALTER TABLE tasks ADD COLUMN owner TEXT NOT NULL DEFAULT ''"),
)
"""Columns a queue file written by an older chassis lacks, and how to add them."""


def worker_identity() -> str:
    """Return 'host:pid', the owner a claimed task records for this process."""
    return f"{socket.gethostname().strip().lower()}:{os.getpid()}"


def _owner_alive(owner: str) -> bool:
    """Return True when *owner* is another live process on this machine.

    A task claimed on another host cannot be probed from here and is treated as
    interrupted, which is what a single shared queue file has always assumed.
    """
    host, _, pid_text = str(owner).rpartition(":")
    if not host or host != socket.gethostname().strip().lower():
        return False
    try:
        pid = int(pid_text)
    except ValueError:
        return False
    return pid != os.getpid() and pid_alive(pid)


class PermanentJobError(Exception):
    """Raised by a handler when retrying cannot help: the task fails at once."""


@dataclass(frozen=True, slots=True)
class QueueTask:
    """One task as stored."""

    id: int
    kind: str
    payload: Any
    state: str
    attempts: int
    max_attempts: int
    run_after: float
    created_at: float
    started_at: float | None
    finished_at: float | None
    result: Any
    error: str | None
    user: str
    owner: str = ""
    """'host:pid' of the process that claimed the task last; empty until claimed."""

    def as_dict(self) -> dict[str, Any]:
        """Return the task as JSON."""
        return {
            "id": self.id,
            "kind": self.kind,
            "payload": self.payload,
            "state": self.state,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "run_after": round(self.run_after, 3),
            "created_at": round(self.created_at, 3),
            "started_at": None if self.started_at is None else round(self.started_at, 3),
            "finished_at": None if self.finished_at is None else round(self.finished_at, 3),
            "result": self.result,
            "error": self.error,
            "user": self.user or None,
            "owner": self.owner or None,
        }


def _loads(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def _row(row: sqlite3.Row) -> QueueTask:
    return QueueTask(
        id=int(row["id"]),
        kind=str(row["kind"]),
        payload=_loads(row["payload"]),
        state=str(row["state"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        run_after=float(row["run_after"]),
        created_at=float(row["created_at"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        result=_loads(row["result"]),
        error=row["error"],
        user=str(row["user"] or ""),
        owner=str(row["owner"] or ""),
    )


def _text(value: Any) -> str:
    text = json.dumps(value, default=str, sort_keys=True)
    return text if len(text) <= MAX_TEXT else json.dumps({"truncated": text[:MAX_TEXT]})


class JobQueue:
    """The queue file plus the handlers registered for it in this process."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._path = Path(path)
        self._clock = clock
        self._handlers: dict[str, Callable[[QueueTask], Any]] = {}
        self._guard = threading.Lock()
        self._ready = False
        self.on_register: Callable[[], None] | None = None
        """Called after every handle(); the job_queue module uses it to start pumping."""

    @property
    def path(self) -> Path:
        """Return the SQLite file backing the queue."""
        return self._path

    @property
    def exists(self) -> bool:
        """Return True once anything has been submitted (the file is created lazily)."""
        return self._path.is_file()

    def handle(self, kind: str, handler: Callable[[QueueTask], Any]) -> None:
        """Register *handler* for tasks of *kind*; it returns a JSON-able result.

        The handler runs on a queue worker. Raising retries the task with backoff until
        ``max_attempts``; raising PermanentJobError fails it at once. A process that
        registers no handler for a kind never claims tasks of that kind.
        """
        if not str(kind).strip():
            raise ValueError("a queue handler needs a kind")
        if not callable(handler):
            raise TypeError(f"handler for {kind!r} must be callable")
        self._handlers[str(kind).strip()] = handler
        if self.on_register is not None:
            self.on_register()

    def handler_for(self, kind: str) -> Callable[[QueueTask], Any] | None:
        """Return the handler registered for *kind*, or None."""
        return self._handlers.get(kind)

    def kinds(self) -> tuple[str, ...]:
        """Return the kinds this process can run."""
        return tuple(self._handlers)

    @contextlib.contextmanager
    def _connect(self, *, create: bool = True) -> Iterator[sqlite3.Connection]:
        if create:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if not self._path.is_file():
                # The file went away under a live queue ('kdx reset', a manual delete):
                # the schema this instance once created must be created again.
                self._ready = False
        connection = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            if create and not self._ready:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript(_SCHEMA)
                present = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
                for column, statement in _MIGRATIONS:
                    if column not in present:
                        connection.execute(statement)
                self._ready = True
            yield connection
        finally:
            connection.close()

    def submit(
        self,
        kind: str,
        payload: Any = None,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        delay: float = 0.0,
        user: str = "",
    ) -> QueueTask:
        """Queue a task of *kind* and return it; it runs after *delay* seconds."""
        name = str(kind).strip()
        if not name:
            raise ValueError("a queued task needs a kind")
        attempts = max(1, int(max_attempts))
        now = float(self._clock())
        with self._guard, self._connect() as db:
            cursor = db.execute(
                "INSERT INTO tasks (kind, payload, max_attempts, run_after, created_at, user)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    name,
                    json.dumps(payload, default=str),
                    attempts,
                    now + max(0.0, delay),
                    now,
                    user,
                ),
            )
            return self._one(db, int(cursor.lastrowid or 0))

    def claim(self, kinds: tuple[str, ...] | None = None) -> QueueTask | None:
        """Take the oldest due queued task (of *kinds*, default: the registered ones)."""
        wanted = tuple(self._handlers) if kinds is None else tuple(kinds)
        if not wanted or not self.exists:
            return None
        marks = ",".join("?" for _ in wanted)
        now = float(self._clock())
        with self._guard, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    f"SELECT id FROM tasks WHERE state='queued' AND run_after<=? "
                    f"AND kind IN ({marks}) ORDER BY run_after, id LIMIT 1",
                    (now, *wanted),
                ).fetchone()
                if row is None:
                    db.execute("COMMIT")
                    return None
                db.execute(
                    "UPDATE tasks SET state='running', attempts=attempts+1, started_at=?,"
                    " finished_at=NULL, error=NULL, owner=? WHERE id=?",
                    (now, worker_identity(), row["id"]),
                )
                task = self._one(db, int(row["id"]))
                db.execute("COMMIT")
                return task
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def complete(self, task_id: int, result: Any = None) -> None:
        """Mark a running task done and keep *result*."""
        with self._guard, self._connect() as db:
            db.execute(
                "UPDATE tasks SET state='done', finished_at=?, result=?, error=NULL WHERE id=?",
                (float(self._clock()), _text(result), task_id),
            )

    def fail(
        self, task_id: int, error: str, *, permanent: bool = False, backoff: float = DEFAULT_BACKOFF
    ) -> QueueTask:
        """Record a failed attempt: requeue with exponential backoff, or fail for good.

        A task goes back to ``queued`` while it has attempts left and the failure is not
        *permanent*; the wait doubles with every attempt, capped at one hour.
        """
        now = float(self._clock())
        with self._guard, self._connect() as db:
            current = self._one(db, task_id)
            retry = not permanent and current.attempts < current.max_attempts
            if retry:
                wait = min(MAX_BACKOFF, backoff * (2 ** max(0, current.attempts - 1)))
                db.execute(
                    "UPDATE tasks SET state='queued', run_after=?, error=? WHERE id=?",
                    (now + wait, error[:MAX_TEXT], task_id),
                )
            else:
                db.execute(
                    "UPDATE tasks SET state='failed', finished_at=?, error=? WHERE id=?",
                    (now, error[:MAX_TEXT], task_id),
                )
            return self._one(db, task_id)

    def recover(self) -> int:
        """Requeue tasks a crash left ``running``; return how many.

        Call it once when a worker pool starts. A task another live process on this
        machine is still running is left alone - two processes may share one queue
        file - so only a task whose worker is gone is recovered. The attempt the crash
        interrupted stays counted, so a task that kills its worker every time still
        ends as ``failed``.
        """
        if not self.exists:
            return 0
        recovered = 0
        with self._guard, self._connect() as db:
            rows = db.execute(
                "SELECT id, attempts, max_attempts, owner FROM tasks WHERE state='running'"
            ).fetchall()
            for row in rows:
                if _owner_alive(str(row["owner"] or "")):
                    continue
                if int(row["attempts"]) < int(row["max_attempts"]):
                    db.execute(
                        "UPDATE tasks SET state='queued', error='interrupted by a restart'"
                        " WHERE id=? AND state='running'",
                        (row["id"],),
                    )
                else:
                    db.execute(
                        "UPDATE tasks SET state='failed', finished_at=?,"
                        " error='interrupted by a restart on its last attempt'"
                        " WHERE id=? AND state='running'",
                        (float(self._clock()), row["id"]),
                    )
                recovered += 1
        return recovered

    def cancel(self, task_id: int) -> bool:
        """Cancel a queued task; a running one cannot be recalled, so False."""
        with self._guard, self._connect() as db:
            cursor = db.execute(
                "UPDATE tasks SET state='cancelled', finished_at=? WHERE id=? AND state='queued'",
                (float(self._clock()), task_id),
            )
            return cursor.rowcount > 0

    def retry(self, task_id: int) -> bool:
        """Queue a failed or cancelled task again with a fresh attempt count."""
        with self._guard, self._connect() as db:
            cursor = db.execute(
                "UPDATE tasks SET state='queued', attempts=0, run_after=?, error=NULL,"
                " finished_at=NULL WHERE id=? AND state IN ('failed', 'cancelled')",
                (float(self._clock()), task_id),
            )
            return cursor.rowcount > 0

    def get(self, task_id: int) -> QueueTask | None:
        """Return task *task_id*, or None."""
        if not self.exists:
            return None
        with self._guard, self._connect(create=False) as db:
            try:
                return self._one(db, task_id)
            except KeyError:
                return None

    def tasks(self, *, state: str | None = None, limit: int = 50) -> list[QueueTask]:
        """Return tasks, newest first, optionally only those in *state*."""
        if not self.exists:
            return []
        with self._guard, self._connect(create=False) as db:
            if state:
                rows = db.execute(
                    "SELECT * FROM tasks WHERE state=? ORDER BY id DESC LIMIT ?",
                    (state, max(1, int(limit))),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
                ).fetchall()
        return [_row(row) for row in rows]

    def stats(self) -> dict[str, int]:
        """Return how many tasks are in each state."""
        counts = dict.fromkeys(STATES, 0)
        if not self.exists:
            return counts
        with self._guard, self._connect(create=False) as db:
            for row in db.execute("SELECT state, COUNT(*) AS n FROM tasks GROUP BY state"):
                counts[str(row["state"])] = int(row["n"])
        return counts

    def purge(self, *, older_than_days: float = 30.0) -> int:
        """Delete finished tasks older than *older_than_days*; return how many."""
        if not self.exists:
            return 0
        cutoff = float(self._clock()) - max(0.0, older_than_days) * 86400.0
        with self._guard, self._connect() as db:
            cursor = db.execute(
                "DELETE FROM tasks WHERE state IN ('done', 'failed', 'cancelled')"
                " AND finished_at IS NOT NULL AND finished_at<?",
                (cutoff,),
            )
            return int(cursor.rowcount)

    def run_one(self, *, backoff: float = DEFAULT_BACKOFF) -> QueueTask | None:
        """Claim and run one task with its handler; return the settled task, or None.

        A test or a one-shot script calls this directly; the ``job_queue`` module claims
        tasks itself and runs each through :meth:`execute` on its own thread.
        """
        task = self.claim()
        return None if task is None else self.execute(task, backoff=backoff)

    def execute(self, task: QueueTask, *, backoff: float = DEFAULT_BACKOFF) -> QueueTask:
        """Run a claimed *task* with its handler and settle it: done, requeued, or failed."""
        handler = self._handlers.get(task.kind)
        if handler is None:  # pragma: no cover - claim() only takes registered kinds
            return self.fail(task.id, f"no handler for {task.kind!r}", permanent=True)
        try:
            result = handler(task)
        except PermanentJobError as exc:
            return self.fail(task.id, f"{type(exc).__name__}: {exc}", permanent=True)
        except Exception as exc:
            return self.fail(task.id, f"{type(exc).__name__}: {exc}", backoff=backoff)
        self.complete(task.id, result)
        settled = self.get(task.id)
        assert settled is not None  # complete() just wrote the row
        return settled

    @staticmethod
    def _one(db: sqlite3.Connection, task_id: int) -> QueueTask:
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return _row(row)

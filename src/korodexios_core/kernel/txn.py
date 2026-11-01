"""A phase-checkpointed transaction over files, with a journal, recovery, and undo.

Anything that changes several files and must be undoable (an installer, a migration, an
import, a configuration rollout) needs the same machine. A transaction moves through
``prepared`` -> ``backed_up`` -> ``applied`` -> ``committed`` -> ``finalized``, and each
step is written to a journal before the next one starts. The journal survives a crash:

* a transaction that stopped before ``committed`` is **compensated** by :meth:`recover`: the
  files it touched go back to the bytes they had, from backups taken first;
* one that stopped at ``committed`` is **finished** (marked ``finalized``);
* a finished transaction can be taken back with :meth:`undo`, which **refuses** when any file
  no longer hashes to what the transaction wrote, because undoing over someone else's later
  change would destroy it.

The manager knows nothing about what the files mean. A project passes an *allow* callback
(which relative paths a transaction may touch) and a *reason* callback (how a failure code
reads to its people); everything else is generic. One manager owns one journal directory; an
advisory lock inside it serialises writers across processes, and :meth:`recover` belongs in
the start-up of whatever owns the manager, before it accepts work.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import secrets
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from .. import jsonio
from ..fsio import atomic_write_bytes, atomic_write_text
from ..paths import id_path
from .locks import NamedLockRegistry

SCHEMA: Final[int] = 1
LOCK_NAME: Final[str] = "txn"
LOCK_TIMEOUT: Final[float] = 30.0
LOCK_DIR: Final[str] = ".locks"
IN_FLIGHT: Final[tuple[str, ...]] = ("prepared", "backed_up", "applied", "committed")
TERMINAL: Final[tuple[str, ...]] = ("finalized", "rolled_back", "undone")
MAX_JOURNAL_BYTES: Final[int] = 16 * 1024 * 1024

Allow = Callable[[str], bool]
Reason = Callable[[str, Mapping[str, Any]], str]
Checkpoint = Callable[[str], None]


def default_reason(code: str, fields: Mapping[str, Any]) -> str:
    """Return a plain English line for a failure *code* and its *fields*."""
    detail = ", ".join(f"{key}={value}" for key, value in sorted(fields.items()))
    return f"{code}: {detail}" if detail else code


class TxnError(Exception):
    """A transaction was refused or failed; ``code`` is stable, the message is for people."""

    def __init__(self, code: str, message: str, fields: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.fields = dict(fields or {})


@dataclass(frozen=True, slots=True)
class TxnOp:
    """One change: write *content* to *path*, or delete it. *path* is relative to the root."""

    path: str
    action: str
    content: bytes = b""

    @classmethod
    def write(cls, path: str, content: str | bytes) -> TxnOp:
        """Return an op that writes *content* (text is UTF-8, newlines exactly as given)."""
        data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        return cls(path=path, action="write", content=data)

    @classmethod
    def delete(cls, path: str) -> TxnOp:
        """Return an op that deletes *path*; deleting a file that is not there is a no-op."""
        return cls(path=path, action="delete")


@dataclass(frozen=True, slots=True)
class Receipt:
    """The journal of one transaction: what it did and what each file hashed to."""

    txn_id: str
    label: str
    phase: str
    created_at: float
    ops: tuple[Mapping[str, Any], ...]

    @property
    def paths(self) -> tuple[str, ...]:
        """Return the paths the transaction touched."""
        return tuple(str(op["path"]) for op in self.ops)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "id": self.txn_id,
            "label": self.label,
            "phase": self.phase,
            "created_at": self.created_at,
            "ops": [dict(op) for op in self.ops],
        }


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    """What :meth:`TransactionManager.recover` did with one unfinished transaction."""

    txn_id: str
    action: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"id": self.txn_id, "action": self.action, "detail": self.detail}


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 of *data*."""
    return hashlib.sha256(data).hexdigest()


class TransactionManager:
    """Run, recover, and undo file transactions under *root*, journalled in *journal_dir*."""

    def __init__(
        self,
        root: Path | str,
        journal_dir: Path | str,
        *,
        allow: Allow | None = None,
        reason: Reason | None = None,
        checkpoint: Checkpoint | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.journal_dir = Path(journal_dir)
        self._allow = allow
        self._reason = reason or default_reason
        self._checkpoint = checkpoint or _no_checkpoint
        self._locks = NamedLockRegistry(self.journal_dir / LOCK_DIR)

    # -- public ------------------------------------------------------------------------

    def run(self, ops: Sequence[TxnOp], *, label: str = "") -> Receipt:
        """Apply *ops* all-or-nothing and return the finalized receipt.

        Every op is validated first and nothing is touched if one is refused. Any ordinary
        failure after that compensates before it is raised, so the files are back as they
        were; a crash (the process dying) leaves the journal for :meth:`recover`. The
        *checkpoint* callback is told each phase boundary, which is how a test crashes a
        transaction on purpose.
        """
        planned = self._plan(ops)
        with self._locked():
            journal = self._prepare(planned, label)
            try:
                self._checkpoint("prepared")
                self._back_up(journal, planned)
                self._set_phase(journal, "backed_up")
                self._checkpoint("backed_up")
                self._apply(journal, planned)
                self._set_phase(journal, "applied")
                self._checkpoint("applied")
                self._verify(journal, planned)
                self._set_phase(journal, "committed")
                self._checkpoint("committed")
                self._set_phase(journal, "finalized")
                self._checkpoint("finalized")
            except Exception as exc:
                self._compensate(journal)
                self._set_phase(journal, "rolled_back")
                if isinstance(exc, TxnError):
                    raise
                raise self._error("apply-failed", error=str(exc)) from exc
            return self._receipt(journal)

    def recover(self) -> list[RecoveryOutcome]:
        """Finish or compensate every transaction a crash left in flight."""
        outcomes: list[RecoveryOutcome] = []
        with self._locked():
            for txn_id in self._ids():
                try:
                    journal = self._read(txn_id)
                except TxnError as exc:
                    outcomes.append(RecoveryOutcome(txn_id, "failed", str(exc)))
                    continue
                if journal["phase"] not in IN_FLIGHT:
                    continue
                try:
                    if journal["phase"] == "committed":
                        self._set_phase(journal, "finalized")
                        outcomes.append(RecoveryOutcome(txn_id, "finalized"))
                    else:
                        self._compensate(journal)
                        self._set_phase(journal, "rolled_back")
                        outcomes.append(RecoveryOutcome(txn_id, "rolled_back"))
                except TxnError as exc:
                    outcomes.append(RecoveryOutcome(txn_id, "failed", str(exc)))
        return outcomes

    def undo(self, txn_id: str) -> Receipt:
        """Take back a finalized transaction, or refuse if its result has moved.

        Each touched file must still hash to what the transaction left (or be absent, for a
        delete). If any does not, nothing is changed and ``result-changed`` is raised.
        """
        with self._locked():
            journal = self._read(txn_id)
            if journal["phase"] == "undone":
                raise self._error("already-undone", id=txn_id)
            if journal["phase"] != "finalized":
                raise self._error("not-undoable", id=txn_id, phase=journal["phase"])
            for op in journal["ops"]:
                target = self._target(str(op["path"]))
                if _hash(target) != op["after"]:
                    raise self._error("result-changed", id=txn_id, path=op["path"])
            self._compensate(journal)
            self._set_phase(journal, "undone")
            return self._receipt(journal)

    def receipt(self, txn_id: str) -> Receipt:
        """Return the receipt of *txn_id*; raise ``txn-unknown`` when there is none."""
        return self._receipt(self._read(txn_id))

    def receipts(self) -> list[Receipt]:
        """Return every readable receipt, newest first."""
        found: list[Receipt] = []
        for txn_id in self._ids():
            with contextlib.suppress(TxnError):
                found.append(self._receipt(self._read(txn_id)))
        return sorted(found, key=lambda item: item.created_at, reverse=True)

    def pending(self) -> list[str]:
        """Return the ids of transactions a crash left in flight."""
        return [item.txn_id for item in self.receipts() if item.phase in IN_FLIGHT]

    # -- planning ----------------------------------------------------------------------

    def _plan(self, ops: Sequence[TxnOp]) -> list[dict[str, Any]]:
        if not ops:
            raise self._error("empty-transaction")
        planned: list[dict[str, Any]] = []
        seen: set[str] = set()
        for op in ops:
            if op.action not in {"write", "delete"}:
                raise self._error("unknown-action", action=op.action)
            relative = self._relative(op.path)
            key = relative.lower()
            if key in seen:
                raise self._error("duplicate-path", path=relative)
            seen.add(key)
            target = self._target(relative)
            if target.is_dir():
                raise self._error("target-is-directory", path=relative)
            planned.append(
                {
                    "path": relative,
                    "action": op.action,
                    "before": _hash(target),
                    "after": sha256_bytes(op.content) if op.action == "write" else None,
                    "backup": None,
                    "_content": op.content,
                }
            )
        return planned

    def _relative(self, path: str) -> str:
        pure = PurePosixPath(str(path).replace("\\", "/"))
        if (
            not str(path).strip()
            or pure.is_absolute()
            or ".." in pure.parts
            or any(":" in part for part in pure.parts)
        ):
            raise self._error("path-outside-root", path=path)
        relative = pure.as_posix()
        if relative == ".":
            raise self._error("path-outside-root", path=path)
        journal = self.journal_dir.resolve()
        if journal == self.root / relative or journal in (self.root / relative).parents:
            raise self._error("path-not-allowed", path=relative)
        if self._allow is not None and not self._allow(relative):
            raise self._error("path-not-allowed", path=relative)
        return relative

    def _target(self, relative: str) -> Path:
        target = self.root / relative
        resolved = target.parent.resolve() / target.name
        if self.root != resolved and self.root not in resolved.parents:
            raise self._error("path-outside-root", path=relative)
        return target

    # -- phases ------------------------------------------------------------------------

    def _prepare(self, planned: list[dict[str, Any]], label: str) -> dict[str, Any]:
        txn_id = f"{int(time.time())}-{secrets.token_hex(4)}"
        journal = {
            "schema": SCHEMA,
            "id": txn_id,
            "label": str(label),
            "phase": "prepared",
            "created_at": round(time.time(), 3),
            "ops": [
                {key: value for key, value in op.items() if key != "_content"} for op in planned
            ],
        }
        self._write(journal)
        return journal

    def _back_up(self, journal: dict[str, Any], planned: list[dict[str, Any]]) -> None:
        folder = self._backup_dir(journal["id"])
        for index, op in enumerate(journal["ops"]):
            target = self._target(op["path"])
            if op["before"] is None:
                continue
            name = str(index)
            atomic_write_bytes(folder / name, target.read_bytes())
            op["backup"] = name
        self._write(journal)

    def _apply(self, journal: dict[str, Any], planned: list[dict[str, Any]]) -> None:
        for index, (op, wanted) in enumerate(zip(journal["ops"], planned, strict=True)):
            target = self._target(op["path"])
            if op["action"] == "write":
                atomic_write_bytes(target, wanted["_content"])
            else:
                with contextlib.suppress(FileNotFoundError):
                    target.unlink()
            self._checkpoint(f"op:{index}")

    def _verify(self, journal: dict[str, Any], planned: list[dict[str, Any]]) -> None:
        for op in journal["ops"]:
            if _hash(self._target(op["path"])) != op["after"]:
                raise self._error("verify-failed", path=op["path"])

    def _compensate(self, journal: dict[str, Any]) -> None:
        """Put every touched file back to its recorded bytes; safe to run twice."""
        folder = self._backup_dir(journal["id"], create=False)
        for op in reversed(journal["ops"]):
            target = self._target(op["path"])
            if _hash(target) == op["before"]:
                continue
            if op["before"] is None:
                with contextlib.suppress(FileNotFoundError):
                    target.unlink()
                continue
            source = folder / str(op["backup"]) if op["backup"] is not None else None
            if source is None or not source.is_file():
                raise self._error("backup-missing", path=op["path"])
            data = source.read_bytes()
            if sha256_bytes(data) != op["before"]:
                raise self._error("backup-corrupt", path=op["path"])
            atomic_write_bytes(target, data)

    # -- journal -----------------------------------------------------------------------

    def _backup_dir(self, txn_id: str, *, create: bool = True) -> Path:
        folder = id_path(self.journal_dir, txn_id, ".backup")
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _write(self, journal: dict[str, Any]) -> None:
        path = id_path(self.journal_dir, journal["id"])
        atomic_write_text(path, _dump(journal))

    def _set_phase(self, journal: dict[str, Any], phase: str) -> None:
        journal["phase"] = phase
        self._write(journal)

    def _read(self, txn_id: str) -> dict[str, Any]:
        try:
            path = id_path(self.journal_dir, txn_id)
        except ValueError as exc:
            raise self._error("txn-unknown", id=txn_id) from exc
        if not path.is_file():
            raise self._error("txn-unknown", id=txn_id)
        try:
            data = jsonio.load_path(path, max_bytes=MAX_JOURNAL_BYTES)
        except (OSError, ValueError) as exc:
            raise self._error("journal-unreadable", id=txn_id, error=str(exc)) from exc
        if (
            not isinstance(data, dict)
            or data.get("id") != txn_id
            or not isinstance(data.get("ops"), list)
            or not isinstance(data.get("phase"), str)
        ):
            raise self._error("journal-unreadable", id=txn_id, error="unexpected shape")
        return data

    def _ids(self) -> list[str]:
        if not self.journal_dir.is_dir():
            return []
        return sorted(item.stem for item in self.journal_dir.glob("*.json") if item.is_file())

    def _receipt(self, journal: Mapping[str, Any]) -> Receipt:
        return Receipt(
            txn_id=str(journal["id"]),
            label=str(journal.get("label", "")),
            phase=str(journal["phase"]),
            created_at=float(journal.get("created_at", 0.0)),
            ops=tuple(dict(op) for op in journal["ops"]),
        )

    def _error(self, code: str, **fields: Any) -> TxnError:
        return TxnError(code, self._reason(code, fields), fields)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        lock = self._locks.acquire(LOCK_NAME, timeout=LOCK_TIMEOUT)
        try:
            yield
        finally:
            lock.release()


def _no_checkpoint(phase: str) -> None:
    return None


def _hash(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes())
    except FileNotFoundError:
        return None
    except IsADirectoryError:
        return None


def _dump(journal: Mapping[str, Any]) -> str:
    return json.dumps(journal, indent=2, sort_keys=True) + "\n"


__all__ = [
    "IN_FLIGHT",
    "TERMINAL",
    "Receipt",
    "RecoveryOutcome",
    "TransactionManager",
    "TxnError",
    "TxnOp",
    "default_reason",
    "sha256_bytes",
]

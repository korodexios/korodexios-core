"""Audit log: who asked the control plane to act, what they asked for, and how it ended.

Every action that crosses the control plane - a command (in the foreground or as a job),
a reload, a stop, and every refusal of one (a wrong token, a role too weak, a command
kept to the terminal) - becomes one JSON line in '<durable_dir>/audit.jsonl'. Reads are
not recorded: the log answers "who changed something", and polling dashboards would
bury that answer.

The file is append-only from the daemon's side and rotates at ``max_bytes`` into
'audit.jsonl.1' (one generation kept), so it can never fill a disk. Commands an operator
runs directly in a terminal on the host do not pass through here - they need shell
access to the machine, which is a stronger right than any role.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Final

from .. import jsonio
from ..paths import restrict_to_owner
from .users import Caller

AUDIT_FILE_NAME: Final[str] = "audit.jsonl"
DEFAULT_MAX_BYTES: Final[int] = 5 * 1024 * 1024


class AuditLog:
    """Appends audit records as JSON lines; safe to call from any thread."""

    def __init__(self, path: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.path = Path(path)
        self.max_bytes = max(1024, int(max_bytes))
        self._lock = threading.Lock()
        self.records = 0
        self.failures = 0

    def record(
        self,
        action: str,
        *,
        caller: Caller | None,
        outcome: str,
        command: str = "",
        detail: Any = None,
        address: str = "",
    ) -> None:
        """Append one record; a disk problem is counted, never raised into the request."""
        entry: dict[str, Any] = {
            "at": round(time.time(), 3),
            "action": action,
            "outcome": outcome,
            "user": caller.name if caller is not None else None,
            "role": caller.role if caller is not None else None,
        }
        if command:
            entry["command"] = command
        if detail is not None:
            entry["detail"] = detail
        if address:
            entry["address"] = address
        line = json.dumps(entry, default=str, sort_keys=True) + "\n"
        with self._lock:
            try:
                self._rotate_if_full(len(line))
                fresh = not self.path.exists()
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
                if fresh:
                    restrict_to_owner(self.path)
                self.records += 1
            except OSError:
                self.failures += 1

    def _rotate_if_full(self, incoming: int) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size + incoming <= self.max_bytes:
            return
        os.replace(str(self.path), str(self.path.with_name(self.path.name + ".1")))

    def as_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "records": self.records, "failures": self.failures}


def read_audit(
    path: Path, *, limit: int = 50, user: str = "", action: str = ""
) -> list[dict[str, Any]]:
    """Return the newest *limit* records of the log at *path*, oldest first, filtered.

    The rotated generation is read first, so a filter that matches little still reaches
    back across one rotation. A line that does not parse is skipped, not fatal.
    """
    wanted: deque[dict[str, Any]] = deque(maxlen=max(1, int(limit)))
    for candidate in (path.with_name(path.name + ".1"), path):
        for entry in _entries(candidate):
            if user and entry.get("user") != user:
                continue
            if action and entry.get("action") != action:
                continue
            wanted.append(entry)
    return list(wanted)


def _entries(path: Path) -> Iterable[dict[str, Any]]:
    try:
        handle = path.open(encoding="utf-8")
    except OSError:
        return
    with handle:
        for line in handle:
            try:
                entry = jsonio.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                yield entry

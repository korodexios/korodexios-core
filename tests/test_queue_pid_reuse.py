"""A reused process id does not keep a crashed task running forever (review 2026-11-08, R1)."""

from __future__ import annotations

import contextlib
import os
import socket
import sqlite3
import time
from pathlib import Path

import pytest

from korodexios_core.kernel.proc_start import process_created_at, started_after
from korodexios_core.kernel.queue import JobQueue

PARENT = os.getppid()
CREATED = process_created_at(PARENT)
needs_creation_time = pytest.mark.skipif(
    CREATED is None, reason="this platform cannot tell when a process was created"
)


def claimed_by(tmp_path: Path, pid: int, claimed_at: float) -> tuple[JobQueue, int]:
    """Return a queue holding one running task that *pid* claimed at *claimed_at*."""
    queue = JobQueue(tmp_path / "queue.sqlite3")
    queue.handle("mail", lambda task: "sent")
    task = queue.submit("mail", {"to": "x"})
    assert queue.claim() is not None
    owner = f"{socket.gethostname().strip().lower()}:{pid}"
    with contextlib.closing(sqlite3.connect(queue.path)) as db, db:
        db.execute(
            "UPDATE tasks SET owner=?, started_at=? WHERE id=?", (owner, claimed_at, task.id)
        )
    return queue, task.id


@needs_creation_time
def test_a_live_process_that_began_before_the_claim_may_be_the_worker(tmp_path: Path) -> None:
    assert CREATED is not None
    queue, _ = claimed_by(tmp_path, PARENT, CREATED + 3600)
    assert queue.recover() == 0


@needs_creation_time
def test_a_process_created_after_the_claim_is_a_reused_pid(tmp_path: Path) -> None:
    assert CREATED is not None
    queue, task_id = claimed_by(tmp_path, PARENT, CREATED - 3600)
    assert queue.recover() == 1
    task = queue.get(task_id)
    assert task is not None and task.state == "queued"


@needs_creation_time
def test_creation_time_is_plausible() -> None:
    created = process_created_at(os.getpid())
    assert created is not None and created <= time.time() + 1
    assert started_after(os.getpid(), created - 100)
    assert not started_after(os.getpid(), created + 100)
    assert process_created_at(99_999_999) is None

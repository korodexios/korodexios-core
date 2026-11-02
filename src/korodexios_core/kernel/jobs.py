"""Background jobs: a delegated command that runs on its own thread and reports as it goes.

A synchronous RPC holds its HTTP request for as long as the command runs, which is fine
for 'heartbeat status' and wrong for a backup of a large tree: the caller cannot see
progress and a proxy may give up first. A job is the same delegated call, started on a
thread of its own; the caller gets an id at once and reads the output as it grows.

Jobs are bounded on both ends: at most ``max_running`` run at once (a further submit is
refused, not queued, so a busy daemon says so), and only the newest ``keep`` finished
jobs are remembered.

Cancelling is cooperative. Python cannot stop a thread from outside, so 'cancel' sets the
job's event and the command decides when to look at it ('request.cancelled()' in a
handler) - between two files of a backup, say. A command that never looks runs to its
end; the job then reports that the cancel came too late.
"""

from __future__ import annotations

import builtins
import io
import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

from .rpc_output import captured_text

DEFAULT_MAX_RUNNING: Final[int] = 4
DEFAULT_KEEP: Final[int] = 50
ACTIVE_STATES: Final[frozenset[str]] = frozenset({"running", "cancelling"})

PreparedCall = Callable[[], dict[str, Any]]
Preparer = Callable[["Job"], PreparedCall]


class JobLimitError(RuntimeError):
    """Raised when a submit would exceed the number of jobs allowed to run at once."""


@dataclass(slots=True)
class Job:
    """One background run of a delegated command."""

    id: str
    command: str
    user: str = ""
    """Who started the job over the control plane; empty when started in-process."""
    buffer: io.StringIO = field(default_factory=io.StringIO)
    state: str = "running"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    """Set by JobRegistry.cancel; the running command reads it to stop early."""

    def as_dict(self, *, output: bool = True) -> dict[str, Any]:
        """Return the job as JSON; *output* includes everything written so far."""
        payload: dict[str, Any] = {
            "id": self.id,
            "command": self.command,
            "user": self.user or None,
            "state": self.state,
            "started_at": round(self.started_at, 3),
            "finished_at": round(self.finished_at, 3) if self.finished_at else None,
            "seconds": round((self.finished_at or time.time()) - self.started_at, 3),
            "exit_code": None if self.result is None else self.result.get("exit_code"),
            "error": self.error,
            "cancel_requested": self.cancel.is_set(),
        }
        if output:
            payload["output"] = captured_text(self.buffer)
        return payload


class JobRegistry:
    """Starts, tracks, and forgets background jobs; safe to call from any thread."""

    def __init__(self, *, max_running: int = DEFAULT_MAX_RUNNING, keep: int = DEFAULT_KEEP) -> None:
        self._max_running = max(1, int(max_running))
        self._keep = max(1, int(keep))
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._threads: dict[str, threading.Thread] = {}

    def submit(
        self,
        command: str,
        prepare: Preparer,
        on_start: Callable[[Job], object] | None = None,
        *,
        user: str = "",
    ) -> Job:
        """Prepare the call now (refusals raise here), then run it on a new thread.

        *on_start* sees the job with its id before the thread starts, so a 'started'
        notice can never arrive after the job's own 'finished' one.
        """
        job = Job(id="", command=str(command).strip(), user=str(user))
        call = prepare(job)
        with self._lock:
            running = sum(1 for item in self._jobs.values() if item.state in ACTIVE_STATES)
            if running >= self._max_running:
                raise JobLimitError(
                    f"{running} jobs are already running (limit {self._max_running}); "
                    "wait for one to finish"
                )
            job.id = f"job-{next(self._ids)}"
            self._jobs[job.id] = job
            self._forget_old()
        if on_start is not None:
            on_start(job)
        thread = threading.Thread(
            target=self._run, args=(job, call), name=f"kdx-{job.id}", daemon=True
        )
        with self._lock:
            # A finished job's thread is no longer worth waiting for at shutdown.
            self._threads = {
                key: value
                for key, value in self._threads.items()
                if key in self._jobs and self._jobs[key].state in ACTIVE_STATES
            }
            self._threads[job.id] = thread
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        """Return the job named *job_id*, or None once it is unknown or forgotten."""
        with self._lock:
            return self._jobs.get(str(job_id))

    def list(self) -> list[Job]:
        """Return every remembered job, newest first."""
        with self._lock:
            return sorted(self._jobs.values(), key=lambda job: job.started_at, reverse=True)

    def cancel(self, job_id: str) -> Job | None:
        """Ask the job to stop; return it, or None when it is unknown.

        A finished job is returned unchanged - there is nothing left to stop.
        """
        with self._lock:
            job = self._jobs.get(str(job_id))
            if job is not None and job.state == "running":
                job.cancel.set()
                job.state = "cancelling"
            return job

    def cancel_all(self) -> builtins.list[Job]:
        """Ask every running job to stop; return the jobs asked."""
        with self._lock:
            asked = [job for job in self._jobs.values() if job.state == "running"]
            for job in asked:
                job.cancel.set()
                job.state = "cancelling"
        return asked

    def wait(self, timeout: float) -> builtins.list[Job]:
        """Wait up to *timeout* seconds for every job thread; return the jobs still active."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._lock:
            threads = list(self._threads.items())
        for _job_id, thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            return [job for job in self._jobs.values() if job.state in ACTIVE_STATES]

    def running(self) -> int:
        """Return how many jobs are running now."""
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.state in ACTIVE_STATES)

    def _run(self, job: Job, call: PreparedCall) -> None:
        # The state flips last, so a reader that sees 'done' also sees the finish time.
        try:
            result = call()
        except Exception as exc:
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()
            job.state = "cancelled" if job.cancel.is_set() else "failed"
            return
        job.result = result
        job.finished_at = time.time()
        if job.cancel.is_set():
            job.state = "cancelled"
        else:
            job.state = "done" if int(result.get("exit_code", 0) or 0) == 0 else "failed"

    def _forget_old(self) -> None:
        finished = [job for job in self._jobs.values() if job.state not in ACTIVE_STATES]
        finished.sort(key=lambda job: job.started_at)
        for job in finished[: max(0, len(finished) - self._keep)]:
            self._jobs.pop(job.id, None)

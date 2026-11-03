"""Supervised child processes: a time limit, output in the log, and a clean end at stop.

Modules often wrap a tool written in another language - ``ffmpeg``, ``yt-dlp``, a Node
renderer. Calling ``subprocess.run`` directly leaves four gaps the chassis exists to
close: no time limit, output lost or written to stdout, a child that outlives the daemon,
and a grandchild that outlives the child. ``ProcessSupervisor.run`` closes all four: the
child runs in its own process group, every output line goes to the logger, a timeout or a
chassis stop kills the whole group, and the outcome is published as
``core.process.finished``.
"""

from __future__ import annotations

import collections
import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from ..exceptions import KorodexiosError

__all__ = [
    "PROCESS_FINISHED_EVENT",
    "ProcessError",
    "ProcessResult",
    "ProcessSupervisor",
]

PROCESS_FINISHED_EVENT: Final[str] = "core.process.finished"
TAIL_LINES: Final[int] = 200
"""How many trailing output lines of each stream a ProcessResult keeps."""
TERM_GRACE: Final[float] = 3.0
"""Seconds a child gets after SIGTERM before the group is killed outright."""
POLL: Final[float] = 0.05


class ProcessError(KorodexiosError):
    """A supervised process could not start, or failed while ``check=True`` was set."""

    def __init__(
        self, message: str, *, result: ProcessResult | None = None, hint: str | None = None
    ) -> None:
        super().__init__(message, hint=hint)
        self.result = result


@dataclass(slots=True)
class ProcessResult:
    """How one supervised process ended."""

    name: str
    command: tuple[str, ...]
    returncode: int
    seconds: float
    timed_out: bool = False
    cancelled: bool = False
    stdout: str = ""
    stderr: str = ""
    """The last TAIL_LINES lines of each stream; the full output is in the log."""

    @property
    def ok(self) -> bool:
        """Return True for a clean exit (code 0, not timed out, not cancelled)."""
        return self.returncode == 0 and not self.timed_out and not self.cancelled

    def as_dict(self) -> dict[str, Any]:
        """Return the result as JSON (the event payload)."""
        return {
            "name": self.name,
            "command": list(self.command),
            "returncode": self.returncode,
            "seconds": round(self.seconds, 3),
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
            "ok": self.ok,
        }


@dataclass(slots=True)
class _Running:
    name: str
    command: tuple[str, ...]
    process: subprocess.Popen[str]
    started: float = field(default_factory=time.monotonic)
    cancel: threading.Event = field(default_factory=threading.Event)


class ProcessSupervisor:
    """Runs child processes for modules and ends all of them when the chassis stops."""

    def __init__(self, *, logger: Any = None, bus: Any = None) -> None:
        self._logger = logger
        self._bus = bus
        self._running: dict[int, _Running] = {}
        self._guard = threading.Lock()
        self._closed = threading.Event()
        self._counter = 0

    def run(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        timeout: float | None = None,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        name: str = "",
        check: bool = False,
        stdin: str | None = None,
    ) -> ProcessResult:
        """Run *command* (an argument list, never a shell string) and wait for it.

        *env* adds to the inherited environment. A *timeout* (seconds) or a chassis stop
        kills the child's whole process group. With ``check=True`` anything but a clean
        exit raises ProcessError carrying the result.
        """
        if isinstance(command, str | bytes) or not command:
            raise TypeError("run_process needs a non-empty argument list, not a shell string")
        argv = tuple(os.fspath(part) for part in command)
        label = name or Path(argv[0]).stem or "process"
        if self._closed.is_set():
            raise ProcessError(f"cannot start {label!r}: the chassis is stopping")
        merged = None if env is None else {**os.environ, **{k: str(v) for k, v in env.items()}}
        try:
            process = subprocess.Popen(
                argv,
                cwd=None if cwd is None else os.fspath(cwd),
                env=merged,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                **_group_flags(),
            )
        except (OSError, ValueError) as exc:
            raise ProcessError(
                f"cannot start {label!r}: {exc}",
                hint=f"check that {argv[0]!r} is installed and on PATH, and that cwd exists",
            ) from exc
        with self._guard:
            self._counter += 1
            key = self._counter
            running = _Running(label, argv, process)
            self._running[key] = running
        if self._logger is not None:
            self._logger.info(f"process {label!r} started", pid=process.pid, command=list(argv))
        tails: dict[str, collections.deque[str]] = {
            "stdout": collections.deque(maxlen=TAIL_LINES),
            "stderr": collections.deque(maxlen=TAIL_LINES),
        }
        readers = [
            threading.Thread(
                target=self._pump,
                args=(label, stream, process.stdout if stream == "stdout" else process.stderr),
                kwargs={"tail": tails[stream]},
                name=f"kdx-proc-{label}-{stream}",
                daemon=True,
            )
            for stream in ("stdout", "stderr")
        ]
        for reader in readers:
            reader.start()
        if stdin is not None and process.stdin is not None:
            with contextlib.suppress(OSError):
                process.stdin.write(stdin)
                process.stdin.close()
        timed_out = False
        deadline = None if timeout is None else running.started + float(timeout)
        while process.poll() is None:
            if running.cancel.is_set():
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(POLL)
        cancelled = running.cancel.is_set() and process.poll() is None
        if process.poll() is None:
            _end_group(process)
        returncode = int(process.wait())
        for reader in readers:
            reader.join(timeout=2.0)
        with self._guard:
            self._running.pop(key, None)
        result = ProcessResult(
            name=label,
            command=argv,
            returncode=returncode,
            seconds=time.monotonic() - running.started,
            timed_out=timed_out,
            cancelled=cancelled,
            stdout="\n".join(tails["stdout"]),
            stderr="\n".join(tails["stderr"]),
        )
        self._finish(result)
        if check and not result.ok:
            if timed_out:
                reason = "timed out"
            elif cancelled:
                reason = "was cancelled"
            else:
                reason = f"exited {returncode}"
            last_line = result.stderr.splitlines()[-1] if result.stderr else None
            raise ProcessError(f"process {label!r} {reason}", result=result, hint=last_line)
        return result

    def active(self) -> list[dict[str, Any]]:
        """Return the processes running right now."""
        with self._guard:
            return [
                {
                    "name": item.name,
                    "pid": item.process.pid,
                    "seconds": round(time.monotonic() - item.started, 3),
                    "command": list(item.command),
                }
                for item in self._running.values()
            ]

    def terminate_all(self, timeout: float = 5.0) -> int:
        """Stop every running child and refuse new ones; return how many were running.

        Waits up to *timeout* for the callers of run() to collect their results.
        """
        self._closed.set()
        with self._guard:
            victims = list(self._running.values())
        for item in victims:
            item.cancel.set()
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            with self._guard:
                if not self._running:
                    break
            time.sleep(POLL)
        if victims and self._logger is not None:
            self._logger.info("child processes stopped", count=len(victims))
        return len(victims)

    def reopen(self) -> None:
        """Accept new processes again (a chassis that restarts in the same process)."""
        self._closed.clear()

    def _pump(self, label: str, stream: str, pipe: Any, *, tail: collections.deque[str]) -> None:
        """Forward one output stream to the log, line by line, keeping the tail."""
        if pipe is None:
            return
        try:
            for raw in pipe:
                line = raw.rstrip("\r\n")
                tail.append(line)
                if self._logger is not None and line.strip():
                    self._logger.info(line, process=label, stream=stream)
        except (OSError, ValueError):
            return

    def _finish(self, result: ProcessResult) -> None:
        if self._logger is not None:
            log = self._logger.info if result.ok else self._logger.warning
            log(
                f"process {result.name!r} finished",
                returncode=result.returncode,
                seconds=round(result.seconds, 3),
                timed_out=result.timed_out,
                cancelled=result.cancelled,
            )
        if self._bus is not None:
            self._bus.publish(PROCESS_FINISHED_EVENT, result.as_dict(), source="process")


def _group_flags() -> dict[str, Any]:
    """Return Popen keyword arguments that put the child in a group of its own."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _end_group(process: subprocess.Popen[str]) -> None:
    """End *process* and everything it started: polite first, then for good."""
    if os.name == "nt":
        # taskkill /T walks the child tree, which Windows has no process group call for.
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        _killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=TERM_GRACE)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            _killpg(process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def _killpg(pgid: int, sig: int) -> None:
    """Signal a process group (POSIX only; getattr keeps the Windows type check quiet)."""
    getattr(os, "killpg")(pgid, sig)  # noqa: B009 - os.killpg does not exist on Windows

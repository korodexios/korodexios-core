"""Rate limiting for outbound calls: a minimum gap, a daily budget, and a cooldown.

A project that calls somebody else's API or website has to keep to that service's
limits and step back after a 429 or 503. Every project used to solve it by hand - a
counter here, a ``.cooldown`` file there. A RateLimiter is that logic once: callers ask
``acquire()`` before each outbound call and the limiter waits out the gap, refuses past
the daily budget, and refuses while a cooldown is running.

The state is one small JSON file under the durable directory, guarded by a named lock,
so it is shared between processes (a daemon and a one-shot ``kdx`` command) and
survives a restart. ``kdx doctor`` and ``kdx limiter list`` read the same files.
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from ..exceptions import KorodexiosError
from ..fsio import atomic_write_text
from .locks import NamedLock, lock_name

__all__ = [
    "LimiterBlocked",
    "LimiterRegistry",
    "RateLimiter",
    "read_limiter_states",
]

STATE_SUFFIX: Final[str] = ".json"
LOCK_TIMEOUT: Final[float] = 10.0
LIMITER_COOLDOWN_EVENT: Final[str] = "core.limiter.cooldown"


class _Unset:
    """Marks a limit the caller did not pass, so "no budget" and "unchanged" differ."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final[_Unset] = _Unset()


class LimiterBlocked(KorodexiosError):
    """A call may not be made now: the daily budget is spent or a cooldown is running."""

    def __init__(
        self, message: str, *, reason: str, retry_after: float, hint: str | None = None
    ) -> None:
        super().__init__(message, hint=hint)
        self.reason = reason
        self.retry_after = retry_after
        """Seconds until the limiter would allow a call again."""


def _today(moment: float) -> str:
    return datetime.fromtimestamp(moment).strftime("%Y-%m-%d")


def _seconds_until_midnight(moment: float) -> float:
    stamp = datetime.fromtimestamp(moment)
    tomorrow = (stamp.replace(hour=0, minute=0, second=0, microsecond=0)).timestamp() + 86400.0
    return max(0.0, tomorrow - moment)


class RateLimiter:
    """One named limiter: ``min_interval``, ``jitter``, ``daily_budget``, ``cooldown()``."""

    def __init__(
        self,
        name: str,
        *,
        state_file: Path,
        lock_file: Path,
        min_interval: float = 0.0,
        jitter: float = 0.0,
        daily_budget: int | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
        logger: Any = None,
        bus: Any = None,
        declared: bool = True,
    ) -> None:
        self.name = lock_name(name)
        # False for a handle opened only to cooldown or clear: it must not rewrite the limits.
        self._declared = declared
        self._state_file = Path(state_file)
        self._lock_file = Path(lock_file)
        self._clock = clock
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._logger = logger
        self._bus = bus
        self._guard = threading.RLock()
        self._stop = threading.Event()
        self.configure(min_interval=min_interval, jitter=jitter, daily_budget=daily_budget)

    def configure(
        self, *, min_interval: float = 0.0, jitter: float = 0.0, daily_budget: int | None = None
    ) -> None:
        """Set the limits; the newest declaration wins."""
        if float(min_interval) < 0:
            raise ValueError(f"min_interval must not be negative, got {min_interval!r}")
        if float(jitter) < 0:
            raise ValueError(f"jitter must not be negative, got {jitter!r}")
        if daily_budget is not None and int(daily_budget) < 0:
            raise ValueError(f"daily_budget must not be negative, got {daily_budget!r}")
        self.min_interval = float(min_interval)
        self.jitter = float(jitter)
        self.daily_budget = None if daily_budget is None else int(daily_budget)

    def declare(
        self,
        *,
        min_interval: float | None = None,
        jitter: float | None = None,
        daily_budget: int | _Unset | None = UNSET,
    ) -> None:
        """Store new limits from now on; a limit left out keeps its current value."""
        self._declared = True
        self.configure(
            min_interval=self.min_interval if min_interval is None else min_interval,
            jitter=self.jitter if jitter is None else jitter,
            daily_budget=self.daily_budget if isinstance(daily_budget, _Unset) else daily_budget,
        )

    def try_acquire(self, cost: int = 1) -> float:
        """Take *cost* units now and return 0.0, or return the seconds to wait first.

        Raises LimiterBlocked while a cooldown runs or when the daily budget cannot
        cover *cost* - waiting a few seconds would not help, so the caller decides.
        """
        units = max(0, int(cost))
        with self._exclusive():
            now = float(self._clock())
            state = self._load(now)
            until = float(state.get("cooldown_until") or 0.0)
            if until > now:
                reason = str(state.get("cooldown_reason") or "cooldown")
                raise LimiterBlocked(
                    f"limiter {self.name!r} is cooling down for {until - now:.0f} more second(s)"
                    f" ({reason})",
                    reason="cooldown",
                    retry_after=until - now,
                    hint=f"'kdx limiter clear {self.name}' ends a cooldown early",
                )
            if self.daily_budget is not None and int(state["used"]) + units > self.daily_budget:
                raise LimiterBlocked(
                    f"limiter {self.name!r} spent its daily budget of {self.daily_budget}",
                    reason="budget",
                    retry_after=_seconds_until_midnight(now),
                    hint="the budget resets at local midnight",
                )
            wait = float(state.get("next_allowed") or 0.0) - now
            if wait > 0:
                return wait
            state["used"] = int(state["used"]) + units
            state["calls"] = int(state.get("calls", 0)) + 1
            state["last_call"] = now
            state["next_allowed"] = now + self._gap()
            self._save(state)
            return 0.0

    def _gap(self) -> float:
        return self.min_interval * (1.0 + self._rng.uniform(0.0, self.jitter))

    def finish(self) -> None:
        """Count the minimum gap from now, the end of the call, instead of from its start.

        ``acquire()`` starts the gap when the call begins; for a long call ("at least
        4 s *after* the last run finished") call ``finish()`` when it ends. The gap is
        only ever extended, never shortened.
        """
        with self._exclusive():
            now = float(self._clock())
            state = self._load(now)
            state["next_allowed"] = max(float(state.get("next_allowed") or 0.0), now + self._gap())
            self._save(state)

    def refund(self, cost: int = 1) -> None:
        """Give back *cost* units and the gap of a call that never reached the service.

        Use it when the call failed before doing anything (a connection refused, a local
        error) so that a failure does not eat the daily budget or hold up the retry. A
        429 is not a refund: the service counted it, so cool down instead.
        """
        units = max(0, int(cost))
        with self._exclusive():
            now = float(self._clock())
            state = self._load(now)
            state["used"] = max(0, int(state["used"]) - units)
            state["calls"] = max(0, int(state.get("calls", 0)) - 1)
            state["next_allowed"] = now
            self._save(state)

    def acquire(self, cost: int = 1, *, timeout: float | None = None) -> None:
        """Wait out the minimum gap, then take *cost* units; raise LimiterBlocked if refused.

        *timeout* caps the total wait; a gap longer than that raises LimiterBlocked
        ('timeout') instead of sleeping. A chassis stop ends the wait the same way.
        """
        deadline = None if timeout is None else float(self._clock()) + max(0.0, timeout)
        while True:
            wait = self.try_acquire(cost)
            if wait <= 0.0:
                return
            remaining = None if deadline is None else deadline - float(self._clock())
            if self._stop.is_set() or (remaining is not None and wait > remaining):
                raise LimiterBlocked(
                    f"limiter {self.name!r} would make the call wait {wait:.1f}s",
                    reason="timeout",
                    retry_after=wait,
                )
            self._sleep(min(wait, 1.0))

    def cooldown(
        self, hours: float = 0.0, *, minutes: float = 0.0, seconds: float = 0.0, reason: str = ""
    ) -> float:
        """Refuse every call for the given time; return the wall-clock end of the cooldown.

        Typical use after a 429 or 503: ``limiter.cooldown(hours=24, reason="HTTP 429")``.
        A shorter cooldown never shortens one that is already running.
        """
        span = max(0.0, float(hours) * 3600.0 + float(minutes) * 60.0 + float(seconds))
        with self._exclusive():
            now = float(self._clock())
            state = self._load(now)
            running = float(state.get("cooldown_until") or 0.0)
            until = max(now + span, running)
            if now + span > running:
                state["cooldown_until"] = until
                state["cooldown_reason"] = str(reason)
                self._save(state)
        if self._logger is not None:
            self._logger.warning(
                f"limiter {self.name!r} cooling down", seconds=round(span, 1), reason=reason
            )
        if self._bus is not None:
            self._bus.publish(
                LIMITER_COOLDOWN_EVENT,
                {"name": self.name, "until": until, "reason": reason},
                source="limiter",
            )
        return until

    def clear_cooldown(self) -> bool:
        """End a running cooldown; return whether there was one."""
        with self._exclusive():
            now = float(self._clock())
            state = self._load(now)
            had = float(state.get("cooldown_until") or 0.0) > now
            state["cooldown_until"] = 0.0
            state["cooldown_reason"] = ""
            self._save(state)
        return had

    def status(self) -> dict[str, Any]:
        """Return the limiter's state as JSON: budget left, gap, cooldown."""
        with self._exclusive():
            now = float(self._clock())
            return _describe(self.name, self._load(now), now)

    def stop(self) -> None:
        """End every wait in acquire(); the chassis calls this at shutdown."""
        self._stop.set()

    def _exclusive(self) -> _Exclusive:
        return _Exclusive(self._guard, self._lock_file, self.name, self._logger)

    def _load(self, now: float) -> dict[str, Any]:
        state: dict[str, Any] = {}
        try:
            loaded = json.loads(self._state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
        except (OSError, ValueError):
            state = {}
        today = _today(now)
        if state.get("day") != today:
            state["day"] = today
            state["used"] = 0
        state.setdefault("used", 0)
        if self._declared:
            state["min_interval"] = self.min_interval
            state["jitter"] = self.jitter
            state["daily_budget"] = self.daily_budget
        else:
            # A handle that declared nothing enforces what the declaring process stored.
            self.min_interval = float(state.get("min_interval") or 0.0)
            self.jitter = float(state.get("jitter") or 0.0)
            stored = state.get("daily_budget")
            self.daily_budget = None if stored is None else int(stored)
        return state

    def _save(self, state: dict[str, Any]) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._state_file, json.dumps(state, indent=2, sort_keys=True) + "\n")


class _Exclusive:
    """Thread guard plus the named cross-process lock around one read-modify-write."""

    def __init__(self, guard: threading.RLock, lock_file: Path, name: str, logger: Any) -> None:
        self._guard = guard
        self._lock = NamedLock(lock_file, name=f"limiter-{name}", logger=logger)

    def __enter__(self) -> None:
        self._guard.acquire()
        try:
            self._lock.acquire(timeout=LOCK_TIMEOUT)
        except BaseException:
            self._guard.release()
            raise

    def __exit__(self, *_exc: object) -> None:
        try:
            self._lock.release()
        finally:
            self._guard.release()


def _describe(name: str, state: dict[str, Any], now: float) -> dict[str, Any]:
    """Return the JSON view of one limiter state; *now* decides what is still running."""
    budget = state.get("daily_budget")
    used = int(state.get("used") or 0)
    until = float(state.get("cooldown_until") or 0.0)
    cooling = until > now
    return {
        "name": name,
        "min_interval": state.get("min_interval"),
        "daily_budget": budget,
        "used_today": used,
        "remaining_today": None if budget is None else max(0, int(budget) - used),
        "calls": int(state.get("calls") or 0),
        "last_call": state.get("last_call"),
        "cooling_down": cooling,
        "cooldown_seconds": round(until - now, 1) if cooling else 0.0,
        "cooldown_reason": str(state.get("cooldown_reason") or "") if cooling else "",
        "cooldown_until": until if cooling else None,
    }


def read_limiter_states(directory: Path, *, now: float | None = None) -> list[dict[str, Any]]:
    """Return every limiter state under *directory*, read-only (no lock, no rewrite).

    This is what ``kdx doctor`` and ``kdx limiter list`` show, so it works from any
    process, including one that never declared the limiter.
    """
    moment = float(time.time() if now is None else now)
    rows: list[dict[str, Any]] = []
    if not directory.is_dir():
        return rows
    for item in sorted(directory.glob(f"*{STATE_SUFFIX}")):
        try:
            state = json.loads(item.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(state, dict):
            continue
        if state.get("day") != _today(moment):
            state["used"] = 0
        rows.append(_describe(item.stem, state, moment))
    return rows


class LimiterRegistry:
    """The limiters of one chassis, by name; backs ``context.limiter(...)``."""

    def __init__(self, directory: Path, *, logger: Any = None, bus: Any = None) -> None:
        self._directory = Path(directory)
        self._logger = logger
        self._bus = bus
        self._limiters: dict[str, RateLimiter] = {}
        self._guard = threading.Lock()

    @property
    def directory(self) -> Path:
        """Return the directory holding one state file per limiter."""
        return self._directory

    def get(
        self,
        name: str,
        *,
        min_interval: float | None = None,
        jitter: float | None = None,
        daily_budget: int | _Unset | None = UNSET,
        declared: bool | None = None,
    ) -> RateLimiter:
        """Return limiter *name*, creating it on first use.

        Passing any limit *declares* the limiter: those limits are stored and replace
        earlier ones (a limit left out stays as it was). Passing none returns a handle
        that never rewrites the stored limits - it enforces what the declaring process
        stored and is safe for status, ``cooldown()`` and ``clear_cooldown()``.
        ``declared`` forces either behaviour.
        """
        key = lock_name(name)
        given = min_interval is not None or jitter is not None or daily_budget is not UNSET
        is_declared = given if declared is None else declared
        with self._guard:
            limiter = self._limiters.get(key)
            if limiter is None:
                limiter = RateLimiter(
                    key,
                    state_file=self._directory / f"{key}{STATE_SUFFIX}",
                    lock_file=self._directory / f"{key}.lock",
                    min_interval=0.0 if min_interval is None else min_interval,
                    jitter=0.0 if jitter is None else jitter,
                    daily_budget=None if isinstance(daily_budget, _Unset) else daily_budget,
                    logger=self._logger,
                    bus=self._bus,
                    declared=is_declared,
                )
                self._limiters[key] = limiter
            elif is_declared:
                limiter.declare(
                    min_interval=min_interval, jitter=jitter, daily_budget=daily_budget
                )
            return limiter

    def names(self) -> tuple[str, ...]:
        """Return the names of the limiters this process declared."""
        return tuple(self._limiters)

    def states(self) -> list[dict[str, Any]]:
        """Return every limiter on disk, whichever process declared it."""
        return read_limiter_states(self._directory)

    def stop(self) -> None:
        """End the waits of every limiter."""
        for limiter in list(self._limiters.values()):
            limiter.stop()

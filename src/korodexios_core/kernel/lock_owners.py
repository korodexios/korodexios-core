"""Thread ownership for named locks: who may wait, who may re-enter, and the `hold()` handle.

The OS-level lock behind a name excludes other processes. Threads of one process need a rule
of their own: the thread that owns a name may ask for it again and gets the same handle; any
other thread waits for the name (up to its timeout) instead of sharing the critical section.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..constants import DEFAULT_LOCK_TIMEOUT
from ..exceptions import LockTimeoutError

if TYPE_CHECKING:
    from .locks import NamedLock, NamedLockRegistry

_WAKE_SLICE = 0.05
"""Longest single wait: a handle released without the registry still gets noticed."""


def wait_until_free(
    free: threading.Condition,
    busy: Callable[[], bool],
    timeout: float | None,
    blocking: bool,
    refuse: Callable[[], LockTimeoutError],
) -> None:
    """Wait on *free* (already held by the caller) until *busy* is False.

    ``blocking=False`` or ``timeout=0`` is a single look; ``timeout=None`` waits as long as
    it takes. When the deadline passes the error built by *refuse* is raised.
    """
    if not blocking:
        deadline: float | None = time.monotonic()
    elif timeout is None:
        deadline = None
    else:
        deadline = time.monotonic() + max(0.0, float(timeout))
    while busy():
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise refuse()
        free.wait(_WAKE_SLICE if remaining is None else min(_WAKE_SLICE, remaining))


@dataclass(slots=True)
class _LockHandle:
    """Context manager returned by :meth:`NamedLockRegistry.hold`.

    The name is already reserved by the time this object exists, so taking it is
    one NamedLock.acquire call and the registry stays honest about names that
    nobody holds.
    """

    registry: NamedLockRegistry
    name: str
    timeout: float | None = DEFAULT_LOCK_TIMEOUT
    blocking: bool = True
    metadata: Mapping[str, Any] | None = field(default=None)
    # Set by NamedLockRegistry.hold when it reserves the name; None until then.
    lock: NamedLock | None = None

    def __enter__(self) -> NamedLock:
        reserved = self.lock
        if reserved is None:
            # Constructed without a reservation: the registry takes the name and
            # records it, which is also the re-entrant path 'acquire' documents.
            self.lock = self.registry.acquire(
                self.name,
                timeout=self.timeout,
                blocking=self.blocking,
                metadata=self.metadata,
            )
            return self.lock
        try:
            self.lock = reserved.acquire(timeout=self.timeout, blocking=self.blocking)
        except LockTimeoutError:
            # The reservation was this call's alone: hand the name back so the
            # registry never lists a lock nobody holds.
            self.registry.release(self.name)
            self.lock = None
            raise
        self.registry.settle(self.name)
        return self.lock

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.lock is not None:
            # Released and forgotten: a registry that still listed a free lock
            # would report a name nobody holds.
            self.registry.release(self.name)
            self.lock = None

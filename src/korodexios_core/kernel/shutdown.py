"""One budget for the whole shutdown: `core.lifecycle.stop_timeout` is the time the daemon takes.

A supervisor (systemd, Docker) allows a fixed grace period and then kills the process. Giving
every phase and every module the full timeout makes the worst case several times longer, so each
step gets what is left of one deadline instead, never less than a short floor so a step that is
already late still gets a moment to finish.
"""

from __future__ import annotations

import time

MIN_STEP_SECONDS = 0.1


class Budget:
    """A deadline that hands out the time still left."""

    def __init__(self, seconds: float | None) -> None:
        self._end = None if seconds is None else time.monotonic() + max(0.0, float(seconds))

    def left(self) -> float:
        """Return the seconds left (at least a short floor); 0.0 for an unbounded budget.

        Zero is what the module runner reads as "no timeout", so an unbounded budget stays so.
        """
        if self._end is None:
            return 0.0
        return max(MIN_STEP_SECONDS, self._end - time.monotonic())

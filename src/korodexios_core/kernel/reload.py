"""Reloading the configuration of a running daemon without ever stopping it.

A typo saved into ``kdx.yml`` must not end the process or leave the caller with a dropped
connection: the daemon keeps the configuration it already has, says why the new one was
refused, and carries on. Reloads come from two threads (the tick loop for SIGHUP, the control
plane for ``POST /api/v1/reload``), so they run one at a time.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from ..exceptions import KorodexiosError

if TYPE_CHECKING:
    from .chassis import Chassis

RELOAD_FAILED_EVENT = "core.config.reload_failed"
_SERIAL = threading.RLock()


def reload_guarded(chassis: Chassis) -> dict[str, Any]:
    """Reload *chassis* once; on a refused configuration keep the old one and say why.

    The summary of a good reload is the one `Chassis.reload_config` returns. A refused one is
    ``{"reloaded": False, "error": ..., "hint": ...}`` and leaves the live configuration,
    the reload counter and every module's view untouched.
    """
    with _SERIAL:
        try:
            return chassis.reload_config()
        except KorodexiosError as exc:
            chassis.logger.warning(
                f"configuration reload refused, keeping the running one: {exc.message}"
            )
            chassis.bus.publish(
                RELOAD_FAILED_EVENT, {"error": type(exc).__name__}, source="chassis"
            )
            return {"reloaded": False, "error": exc.message, "hint": exc.hint or ""}


def serve_reload_request(chassis: Chassis) -> dict[str, Any] | None:
    """Serve a pending SIGHUP reload; None when none is pending. The flag is always cleared."""
    if not chassis.signals.reload_requested:
        return None
    try:
        return reload_guarded(chassis)
    finally:
        chassis.signals.clear_reload()

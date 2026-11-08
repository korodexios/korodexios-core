"""The exit code of a command that ran on a daemon: what the daemon's handler returned."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..constants import ExitCode


def rpc_exit_code(status: int, payload: Mapping[str, Any]) -> int:
    """Return the exit code for an RPC reply.

    A refused or failed request (HTTP 400 and up) is 1. An answered one carries the
    handler's own code in ``result.exit_code`` (0, 1, or a module's verdict such as 4),
    so a script gets the same code from ``kdx --remote cmd`` as from ``kdx cmd``.
    """
    if status >= 400:
        return int(ExitCode.ERROR)
    result = payload.get("result")
    code = result.get("exit_code") if isinstance(result, Mapping) else None
    return code if isinstance(code, int) and not isinstance(code, bool) else int(ExitCode.SUCCESS)

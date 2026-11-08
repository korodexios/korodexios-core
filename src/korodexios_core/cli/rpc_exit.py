"""What a client of the daemon reports: the command's exit code, or that no daemon answers."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..constants import ExitCode
from .render import dump_json


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


def json_no_daemon(discovery_file: Path | str) -> int:
    """Print the machine answer for "no daemon" and return exit code 1.

    The payload and the stderr note stay English and untranslated: scripts read them.
    """
    dump_json(
        {"ok": False, "error": "no daemon is answering", "discovery_file": str(discovery_file)}
    )
    print("[i] no daemon is answering; start one with 'kdx run'", file=sys.stderr)
    return int(ExitCode.ERROR)

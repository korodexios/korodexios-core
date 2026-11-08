"""'kdx playbook': the working guide for AI agents, from the terminal.

The playbook ships inside the package (``resources/agent-playbook.md``). ``kdx init`` copies
it into every new project as ``AGENT_PLAYBOOK.md``; this command prints it or writes it into
a project that was created earlier.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..constants import TAG_ERROR, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from .docs_sync import check_one
from .render import tr, write_line

RESOURCES = Path(__file__).resolve().parent.parent / "resources"


def agent_playbook() -> str:
    """Return the playbook written for AI agents (English: agents read it)."""
    return (RESOURCES / "agent-playbook.md").read_text(encoding="utf-8")


def playbook_command() -> CliCommand:
    """Return the 'kdx playbook' command."""
    return CliCommand(
        name="playbook",
        help="Print the playbook for AI agents (how to work in a project on the core).",
        group="core",
        handler=_playbook,
        remote=False,
        args=(
            CliArgument(
                ("--write",),
                dest="write",
                metavar="FILE",
                help="Write the playbook to FILE instead of printing it.",
            ),
            CliArgument(
                ("--check",),
                dest="check",
                action="store_true",
                help="Change nothing; exit 3 and print a diff when AGENT_PLAYBOOK.md differs.",
            ),
        ),
    )


def _playbook(request: CliRequest) -> int:
    if request.flag("check"):
        return check_one(request, "playbook")
    text = agent_playbook()
    target = request.option("write")
    if not target:
        for line in text.splitlines():
            write_line(line)
        return int(ExitCode.SUCCESS)
    path = Path(str(target))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        reason = tr("{path} could not be written: {reason}", path=path, reason=exc)
        write_line(f"{TAG_ERROR} {reason}", stream=sys.stderr)
        return int(ExitCode.ERROR)
    write_line(f"{TAG_OK} " + tr("{path} created", path=path))
    return int(ExitCode.SUCCESS)

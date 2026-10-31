"""'kdx conventions': the project rules, in the reader's language, from the terminal.

The rules ship inside the package (``resources/conventions*.md``), so they are there with
nothing but ``pip install``. ``kdx init`` copies them into every new project; this command
prints them, writes them anywhere, or says where they are.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..constants import TAG_ERROR, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.i18n import DEFAULT_LOCALE, locale_chain
from .render import tr, write_line

RESOURCES = Path(__file__).resolve().parent.parent / "resources"


def agent_playbook() -> str:
    """Return the playbook written for AI agents (English: agents read it, owners need not)."""
    return (RESOURCES / "agent-playbook.md").read_text(encoding="utf-8")


def conventions_text(language: str) -> tuple[str, str]:
    """Return (language used, text) for *language*, falling back to English."""
    for candidate in (*locale_chain(language), DEFAULT_LOCALE):
        name = "conventions.md" if candidate == DEFAULT_LOCALE else f"conventions.{candidate}.md"
        path = RESOURCES / name
        if path.is_file():
            return candidate, path.read_text(encoding="utf-8")
    raise FileNotFoundError("the conventions files are missing from the package")


def conventions_command() -> CliCommand:
    """Return the 'kdx conventions' command."""
    return CliCommand(
        name="conventions",
        help="Print the project rules and conventions (how to build and work on a project).",
        group="core",
        handler=_conventions,
        remote=False,
        args=(
            CliArgument(
                ("--lang",),
                dest="lang",
                metavar="LANG",
                help="Language of the rules: en or sk [your language, else en].",
            ),
            CliArgument(
                ("--agent",),
                dest="agent",
                action="store_true",
                help="Print the playbook written for AI agents instead (English).",
            ),
            CliArgument(
                ("--write",),
                dest="write",
                metavar="FILE",
                help="Write the rules to FILE instead of printing them.",
            ),
        ),
    )


def _conventions(request: CliRequest) -> int:
    chosen = str(request.option("lang") or request.context.i18n.locale)
    text = agent_playbook() if request.flag("agent") else conventions_text(chosen)[1]
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

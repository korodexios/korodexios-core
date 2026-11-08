"""'kdx conventions': the rules every Korodexios project follows, from the terminal.

The text ships inside the package (``resources/conventions.md``). ``kdx init`` copies it
into every new project as ``docs/CONVENTIONS.md``; this command prints it or writes it
into a project that was created earlier. The spec template (``resources/spec-template.md``)
is seeded as ``docs/specs/TEMPLATE.md`` the same way.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..constants import TAG_ERROR, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from .docs_sync import check_one
from .render import tr, write_line

RESOURCES = Path(__file__).resolve().parent.parent / "resources"


def resource_text(name: str) -> str:
    path = RESOURCES / name
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        reason = tr("resource missing: {path}", path=path)
        write_line(f"{TAG_ERROR} {reason}", stream=sys.stderr)
        raise SystemExit(int(ExitCode.ERROR)) from None


def conventions_text() -> str:
    """Return the project conventions (English: agents and templates read it)."""
    return resource_text("conventions.md")


def spec_template() -> str:
    """Return the spec template seeded as ``docs/specs/TEMPLATE.md``."""
    return resource_text("spec-template.md")


def conventions_command() -> CliCommand:
    """Return the 'kdx conventions' command."""
    return CliCommand(
        name="conventions",
        help="Print the conventions every Korodexios project follows.",
        group="core",
        handler=_conventions,
        remote=False,
        args=(
            CliArgument(
                ("--write",),
                dest="write",
                metavar="FILE",
                help="Write the conventions to FILE instead of printing them (overwrites).",
            ),
            CliArgument(
                ("--check",),
                dest="check",
                action="store_true",
                help="Change nothing; exit 3 and print a diff when docs/CONVENTIONS.md differs.",
            ),
        ),
    )


def _conventions(request: CliRequest) -> int:
    if request.flag("check"):
        return check_one(request, "conventions")
    text = conventions_text()
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

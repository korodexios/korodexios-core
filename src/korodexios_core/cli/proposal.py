"""'kdx proposal': suggest a change to the core instead of editing it from a project.

A project never edits the core. ``kdx proposal new`` writes a numbered note from the
packaged template into ``docs/core-proposals/``; ``kdx proposal list`` shows what is open.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..constants import TAG_ERROR, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from .conventions import resource_text
from .render import dump_json, print_table, tr, write_line

if TYPE_CHECKING:
    from ..kernel.chassis import Chassis

PROPOSALS_DIR = "docs/core-proposals"
NAME = re.compile(r"^(\d{3})-.+\.md$")
JSON_ARG = CliArgument(
    ("--json",), dest="as_json", action="store_true", help="Emit the result as JSON."
)


def proposal_template() -> str:
    """Return the core-proposal template (English: agents read it)."""
    return resource_text("core-proposal-template.md")


def proposal_command() -> CliCommand:
    """Return the 'kdx proposal' command."""
    return CliCommand(
        name="proposal",
        help="Suggest a change to the core (projects never edit the core).",
        group="core",
        subcommands=(
            CliCommand(
                name="new",
                help="Create a numbered proposal note from the template.",
                handler=_new,
                remote=False,
                args=(
                    CliArgument(("title",), nargs="?", help="Short title of the proposal."),
                    JSON_ARG,
                ),
            ),
            CliCommand(
                name="list",
                help="List the proposals of this project.",
                handler=_list,
                remote=False,
                args=(JSON_ARG,),
            ),
        ),
    )


def _chassis(request: CliRequest) -> Chassis:
    return cast("Chassis", getattr(request.context, "chassis", None))


def _directory(request: CliRequest) -> Path:
    return Path(_chassis(request).paths.root) / PROPOSALS_DIR


def _entries(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(item for item in directory.iterdir() if NAME.match(item.name))


def slugify(title: str) -> str:
    """Return a file-name-safe form of *title*."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def next_number(directory: Path) -> str:
    """Return the next free three-digit proposal number."""
    taken = [int(item.name[:3]) for item in _entries(directory)]
    return f"{max(taken, default=0) + 1:03d}"


def _new(request: CliRequest) -> int:
    from .ux import UserExperience

    chassis = _chassis(request)
    title = str(request.option("title") or "").strip()
    if not title:
        title = UserExperience(chassis.config).ask_text(
            tr("Proposal title"), required=True, example="job queue ignores the stop timeout"
        )
    slug = slugify(title)
    if not slug:
        write_line(f"{TAG_ERROR} " + tr("a proposal needs a title"), stream=sys.stderr)
        return int(ExitCode.USAGE_ERROR)
    directory = _directory(request)
    number = next_number(directory)
    text = (
        proposal_template()
        .replace("NNN", number)
        .replace("Title", title, 1)
        .replace("YYYY-MM-DD", date.today().isoformat())
        .replace("<project>", Path(chassis.paths.root).name)
    )
    path = directory / f"{number}-{slug}.md"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        reason = tr("{path} could not be written: {reason}", path=path, reason=exc)
        write_line(f"{TAG_ERROR} {reason}", stream=sys.stderr)
        return int(ExitCode.ERROR)
    if request.flag("as_json"):
        dump_json({"number": number, "title": title, "file": str(path)})
    else:
        write_line(f"{TAG_OK} " + tr("{path} created", path=path))
    return int(ExitCode.SUCCESS)


def _row(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    status = next((line for line in lines if line.startswith("Status:")), "Status: ?")
    return {
        "number": path.name[:3],
        "title": (lines[0] if lines else "").removeprefix("# ").split(" - ", 1)[-1],
        "status": status.removeprefix("Status:").split("Date:")[0].strip(),
        "file": str(path),
    }


def _list(request: CliRequest) -> int:
    rows = [_row(path) for path in _entries(_directory(request))]
    if request.flag("as_json"):
        dump_json({"proposals": rows})
    elif not rows:
        write_line(tr("no proposals yet"))
    else:
        print_table(
            [(r["number"], r["status"], r["title"]) for r in rows],
            ("number", "status", "title"),
        )
    return int(ExitCode.SUCCESS)

"""'kdx docs': keep the documents ``kdx init`` seeds in step with the installed core.

``kdx init`` copies the conventions, the agent playbook and the spec template into every
project. Those copies drift the day the core ships a newer text, and nobody notices.
``kdx docs check`` compares each copy with the shipped resource (exit 3 and a diff when one
differs, which is what ``make check-docs`` runs); ``kdx docs update`` rewrites them.
``kdx conventions --check`` and ``kdx playbook --check`` do the same for one document.
"""

from __future__ import annotations

import difflib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..fsio import atomic_write_text
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from .render import dump_json, tr, write_line

if TYPE_CHECKING:
    from ..kernel.chassis import Chassis


@dataclass(frozen=True, slots=True)
class SeededDoc:
    """One document ``kdx init`` seeds: where it lives and where its text comes from."""

    key: str
    path: str
    text: Callable[[], str]


@dataclass(frozen=True, slots=True)
class DocRow:
    """What comparing one seeded document found or did."""

    key: str
    path: str
    state: str
    """'current', 'stale', 'missing' or 'written'."""
    diff: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"document": self.key, "path": self.path, "state": self.state}


def seeded_documents() -> tuple[SeededDoc, ...]:
    """Return every document the generator seeds, with its project-relative path."""
    from .conventions import conventions_text, spec_template
    from .playbook import agent_playbook

    return (
        SeededDoc("conventions", "docs/CONVENTIONS.md", conventions_text),
        SeededDoc("playbook", "AGENT_PLAYBOOK.md", agent_playbook),
        SeededDoc("spec-template", "docs/specs/TEMPLATE.md", spec_template),
    )


def _lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").splitlines()


def compare_document(root: Path, doc: SeededDoc) -> DocRow:
    """Compare the project copy under *root* with the shipped text (line endings ignored)."""
    path = root / doc.path
    wanted = doc.text()
    if not path.is_file():
        return DocRow(doc.key, doc.path, "missing")
    current = path.read_text(encoding="utf-8")
    if _lines(current) == _lines(wanted):
        return DocRow(doc.key, doc.path, "current")
    diff = "\n".join(
        difflib.unified_diff(
            _lines(current),
            _lines(wanted),
            fromfile=f"{doc.path} (project)",
            tofile=f"{doc.path} (core)",
            lineterm="",
        )
    )
    return DocRow(doc.key, doc.path, "stale", diff)


def _root(request: CliRequest) -> Path:
    chassis = cast("Chassis", request.options.get("chassis") or request.context.chassis)
    return Path(chassis.paths.root)


def _label(state: str) -> str:
    return {
        "current": tr("up to date"),
        "stale": tr("differs from the installed core"),
        "missing": tr("missing"),
        "written": tr("written"),
    }.get(state, state)


def _report(rows: list[DocRow], *, as_json: bool, show_diff: bool) -> None:
    if as_json:
        dump_json({"documents": [row.as_dict() for row in rows]})
        return
    for row in rows:
        tag = TAG_OK if row.state in ("current", "written") else TAG_ERROR
        write_line(f"{tag} {row.path}: {_label(row.state)}")
        if show_diff and row.diff:
            for line in row.diff.splitlines():
                write_line(line)
    if any(row.state in ("stale", "missing") for row in rows):
        write_line(f"{TAG_NOTE} " + tr("'kdx docs update' rewrites them from the installed core"))


def check_documents(root: Path, docs: tuple[SeededDoc, ...]) -> list[DocRow]:
    """Return one row per document; nothing is written."""
    return [compare_document(root, doc) for doc in docs]


def update_documents(root: Path, docs: tuple[SeededDoc, ...]) -> list[DocRow]:
    """Rewrite every document that differs; return what was found and done."""
    rows: list[DocRow] = []
    for doc in docs:
        row = compare_document(root, doc)
        if row.state == "current":
            rows.append(row)
            continue
        atomic_write_text(root / doc.path, doc.text())
        rows.append(DocRow(doc.key, doc.path, "written"))
    return rows


def check_one(request: CliRequest, key: str) -> int:
    """Handler body of ``kdx conventions --check`` and ``kdx playbook --check``."""
    docs = tuple(doc for doc in seeded_documents() if doc.key == key)
    rows = check_documents(_root(request), docs)
    _report(rows, as_json=False, show_diff=True)
    drift = any(row.state != "current" for row in rows)
    return int(ExitCode.CONFIG_ERROR if drift else ExitCode.SUCCESS)


def docs_command() -> CliCommand:
    """Return the 'kdx docs' group."""
    json_arg = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
    return CliCommand(
        name="docs",
        help="Keep the documents 'kdx init' seeded in step with the installed core.",
        group="core",
        remote=False,
        subcommands=(
            CliCommand(
                name="check",
                help="Change nothing; exit 3 and print a diff when a seeded document differs.",
                group="core",
                handler=_check,
                remote=False,
                args=(json_arg,),
            ),
            CliCommand(
                name="update",
                help="Rewrite the conventions, the playbook and the spec template from the core.",
                group="core",
                handler=_update,
                remote=False,
                args=(json_arg,),
            ),
        ),
    )


def _check(request: CliRequest) -> int:
    rows = check_documents(_root(request), seeded_documents())
    as_json = request.flag("as_json")
    _report(rows, as_json=as_json, show_diff=not as_json)
    drift = any(row.state != "current" for row in rows)
    return int(ExitCode.CONFIG_ERROR if drift else ExitCode.SUCCESS)


def _update(request: CliRequest) -> int:
    root = _root(request)
    try:
        rows = update_documents(root, seeded_documents())
    except OSError as exc:
        reason = tr("{path} could not be written: {reason}", path=root, reason=exc)
        write_line(f"{TAG_ERROR} {reason}", stream=sys.stderr)
        return int(ExitCode.ERROR)
    _report(rows, as_json=request.flag("as_json"), show_diff=False)
    return int(ExitCode.SUCCESS)

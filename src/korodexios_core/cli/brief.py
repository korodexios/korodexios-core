"""'kdx brief': the project's goal, written down once so every agent works toward it.

The brief lives in ``docs/BRIEF.md``. ``kdx init`` seeds an empty one; this command
interviews the developer in the terminal (or takes every answer as a flag) and rewrites
the file, keeping earlier answers as defaults. ``kdx brief --check`` says whether it is
complete, so an agent can refuse to guess when it is not.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from .render import dump_json, plain, tr, write_line

if TYPE_CHECKING:
    from ..kernel.chassis import Chassis

BRIEF_FILE = "docs/BRIEF.md"
PENDING = "(not answered yet)"
SEPARATOR = ";"


@dataclass(frozen=True, slots=True)
class Question:
    """One section of the brief and the prompt that fills it."""

    key: str
    title: str
    prompt: str
    example: str
    required: bool = True
    as_list: bool = False


def questions() -> tuple[Question, ...]:
    """Return the brief's questions in order (prompts go through the language service)."""
    return (
        Question(
            "goal",
            "Goal",
            tr("What should this application do, and why? (one sentence)"),
            "keep a ledger of backups and warn when one is stale",
        ),
        Question(
            "users",
            "Users",
            tr("Who uses it, and how (terminal, web page, other programs)?"),
            "one operator at a terminal; a cron job",
        ),
        Question(
            "features",
            "Core behaviour",
            tr("The 3 to 5 things it must do, separated by ';'"),
            "list backups; verify a backup; alert on stale ones",
            as_list=True,
        ),
        Question(
            "non_goals",
            "Non-goals",
            tr("What it will NOT do, separated by ';'"),
            "no cloud upload; no web page in the first version",
            as_list=True,
        ),
        Question(
            "done",
            "Done when",
            tr("How will you know it works? Observable results, separated by ';'"),
            "kdx backups list shows every run; a stale backup exits 1",
            as_list=True,
        ),
        Question(
            "constraints",
            "Constraints",
            tr("Limits to respect: platforms, languages, data, security (optional)"),
            "Windows and Linux; no network; data stays in data/",
            required=False,
            as_list=True,
        ),
        Question(
            "risks",
            "What could go wrong / who could abuse it",
            tr("What could go wrong, or who could abuse it? (optional)"),
            "a wrong path deletes a backup",
            required=False,
        ),
    )


def by_key(key: str) -> Question:
    """Return the question named *key*."""
    return next(question for question in questions() if question.key == key)


def render_brief(answers: dict[str, str]) -> str:
    """Return the brief as Markdown; an unanswered question keeps a visible marker."""
    lines = [
        "# Project brief",
        "",
        "The goal every agent works toward. Run `kdx brief` to answer or change it and",
        "`kdx brief --check` to see whether it is complete. If something here is unclear",
        "or missing, ask the owner; do not guess.",
        "",
    ]
    for question in questions():
        lines += [f"## {question.title}", ""]
        text = answers.get(question.key, "").strip()
        if not text:
            lines += [PENDING, ""]
        elif question.as_list:
            lines += [f"- {item.strip()}" for item in text.split(SEPARATOR) if item.strip()]
            lines.append("")
        else:
            lines += [text, ""]
    return "\n".join(lines)


def brief_template() -> str:
    """Return the empty brief that ``kdx init`` seeds."""
    return render_brief({})


def parse_brief(text: str) -> dict[str, str]:
    """Return the answers found in *text*, keyed by question; markers count as unanswered."""
    by_title = {question.title: question for question in questions()}
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            current = by_title[line[3:]].key if line[3:] in by_title else None
        elif current is not None and line and line != PENDING:
            sections.setdefault(current, []).append(line.removeprefix("- ").strip())
    return {
        key: (f"{SEPARATOR} " if by_key(key).as_list else " ").join(parts)
        for key, parts in sections.items()
    }


def pending(answers: dict[str, str]) -> list[str]:
    """Return the keys of required questions that have no answer."""
    return [q.key for q in questions() if q.required and not answers.get(q.key, "").strip()]


def brief_command() -> CliCommand:
    """Return the 'kdx brief' command."""
    flags = tuple(
        CliArgument(
            (f"--{question.key.replace('_', '-')}",),
            dest=question.key,
            metavar="TEXT",
            help=f"Answer for '{question.title}' without being asked.",
        )
        for question in questions()
    )
    return CliCommand(
        name="brief",
        help="Write down what the project must do (interview), or check that it is complete.",
        group="core",
        handler=_brief,
        remote=False,
        args=(
            CliArgument(
                ("--check",),
                dest="check",
                action="store_true",
                help="Only report whether the brief is complete (exit 1 when it is not).",
            ),
            CliArgument(("--file",), dest="file", metavar="FILE", help="Brief file to use."),
            CliArgument(
                ("--json",), dest="as_json", action="store_true", help="Emit the result as JSON."
            ),
            *flags,
        ),
    )


def _chassis(request: CliRequest) -> Chassis:
    return cast("Chassis", getattr(request.context, "chassis", None))


def _brief_path(request: CliRequest) -> Path:
    configured = str(request.option("file") or "").strip()
    path = (
        Path(configured).expanduser()
        if configured
        else Path(_chassis(request).paths.root) / BRIEF_FILE
    )
    return path if path.is_absolute() else Path.cwd() / path


def _report(path: Path, answers: dict[str, str], request: CliRequest, *, written: bool) -> int:
    missing = pending(answers)
    if request.flag("as_json"):
        dump_json({"file": str(path), "complete": not missing, "pending": missing})
    elif missing:
        titles = ", ".join(by_key(key).title for key in missing)
        reason = plain("brief is incomplete: {titles}", titles=titles)
        write_line(f"{TAG_ERROR} {reason}", stream=sys.stderr)
        write_line(f"{TAG_NOTE} " + tr("run 'kdx brief' to answer the questions"))
    elif written:
        write_line(f"{TAG_OK} " + tr("{path} written", path=path))
    else:
        write_line(f"{TAG_OK} " + tr("brief is complete: {path}", path=path))
    return int(ExitCode.ERROR if missing else ExitCode.SUCCESS)


def _brief(request: CliRequest) -> int:
    path = _brief_path(request)
    existing = parse_brief(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if request.flag("check"):
        if not path.is_file():
            if request.flag("as_json"):
                dump_json({"file": str(path), "complete": False, "pending": [], "exists": False})
                return int(ExitCode.ERROR)
            reason = plain("{path} does not exist", path=path)
            write_line(f"{TAG_ERROR} {reason}", stream=sys.stderr)
            write_line(f"{TAG_NOTE} " + tr("run 'kdx brief' to create it"))
            return int(ExitCode.ERROR)
        return _report(path, existing, request, written=False)
    answers = _interview(request, existing)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_brief(answers), encoding="utf-8")
    except OSError as exc:
        reason = plain("{path} could not be written: {reason}", path=path, reason=exc)
        write_line(f"{TAG_ERROR} {reason}", stream=sys.stderr)
        return int(ExitCode.ERROR)
    return _report(path, answers, request, written=True)


def _interview(request: CliRequest, existing: dict[str, str]) -> dict[str, str]:
    from .ux import UserExperience

    ux = UserExperience(_chassis(request).config)
    answers: dict[str, str] = {}
    for question in questions():
        given: Any = request.option(question.key)
        if given is not None:
            answers[question.key] = str(given).strip()
            continue
        answers[question.key] = ux.ask_text(
            question.prompt,
            default=existing.get(question.key, ""),
            example=question.example,
            required=question.required,
        )
    return answers

"""'kdx proposal': suggest a change to the core instead of editing it from a project.

A project never edits the core. When an agent (or a person) finds that the core lacks
something many projects would need, it writes a numbered note from the packaged template
into ``docs/core-proposals/`` - ``kdx proposal new``, or the MCP tool ``kdx_proposal_draft``.
The note stays on this machine.

Sending is a separate, explicit step the owner takes: ``kdx proposal send NNN`` prints the
exact text that would leave the machine (the project name only with ``--with-project``),
lists open issues with a similar title so the same idea is not filed twice, asks for a yes,
and then creates the issue with the GitHub CLI (``gh``). Without ``gh`` it prints a link to
the issue form and the file holding the text to paste. The issue address is written back
into the note, so a second send is refused.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
import urllib.parse
import webbrowser
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..fsio import atomic_write_text
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..version import CORE_API_VERSION, __version__
from .conventions import resource_text
from .render import dump_json, print_table, tr, write_line

if TYPE_CHECKING:
    from ..kernel.chassis import Chassis

PROPOSALS_DIR = "docs/core-proposals"
CORE_REPOSITORY = "korodexios/korodexios-core"
"""Where proposals for the chassis go; '--repo owner/name' sends them elsewhere (a fork)."""
ISSUE_TEMPLATE = "core-proposal.yml"
TITLE_PREFIX = "[core-proposal] "
NAME = re.compile(r"^(\d{3})-.+\.md$")
ISSUE_LINE = re.compile(r"^Issue:\s*(\S.*)$", re.MULTILINE)
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SECTIONS = ("problem", "where", "change", "why", "impact", "workaround", "implementation")
_HEADINGS = {
    "problem": "## Problem",
    "where": "## Where it showed up",
    "change": "## Proposed change",
    "why": "## Why it belongs in the core",
    "impact": "## Impact",
    "workaround": "## Workaround until then",
    "implementation": "## Implementation offered",
}
JSON_ARG = CliArgument(
    ("--json",), dest="as_json", action="store_true", help="Emit the result as JSON."
)
NUMBER_ARG = CliArgument(
    ("number",), metavar="NNN", help="Proposal number (see 'kdx proposal list')."
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
            CliCommand(
                name="show",
                help="Print one proposal note.",
                handler=_show,
                remote=False,
                args=(NUMBER_ARG, JSON_ARG),
            ),
            CliCommand(
                name="export",
                help="Print the text 'send' would submit (nothing leaves the machine).",
                handler=_export,
                remote=False,
                args=(
                    NUMBER_ARG,
                    CliArgument(("--with-project",), dest="with_project", action="store_true",
                                help="Include the project's name (left out by default)."),
                    CliArgument(("--output",), metavar="FILE", help="Write the text to FILE."),
                ),
            ),
            CliCommand(
                name="send",
                help="Submit a proposal as a GitHub issue, after showing it and asking.",
                handler=_send,
                remote=False,
                args=(
                    NUMBER_ARG,
                    CliArgument(("--repo",), metavar="OWNER/NAME",
                                help=f"Target repository [{CORE_REPOSITORY}]."),
                    CliArgument(("--with-project",), dest="with_project", action="store_true",
                                help="Include the project's name (left out by default)."),
                    CliArgument(("--open",), dest="open_browser", action="store_true",
                                help="Without gh: open the issue form in the browser."),
                    CliArgument(("--again",), dest="again", action="store_true",
                                help="Send although the note already names an issue."),
                    CliArgument(("--confirm",), dest="confirm", action="store_true",
                                help="I have read the text: send it without asking."),
                ),
            ),
        ),
    )


def _chassis(request: CliRequest) -> Chassis:
    return cast("Chassis", getattr(request.context, "chassis", None))


def _directory(request: CliRequest) -> Path:
    return proposals_dir(Path(_chassis(request).paths.root))


def proposals_dir(root: Path) -> Path:
    """Return the proposal folder of the project at *root*."""
    return Path(root) / PROPOSALS_DIR


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


def write_proposal(
    root: Path, title: str, sections: Mapping[str, str] | None = None
) -> tuple[str, Path]:
    """Write a new proposal note under *root*; return (number, path). Nothing is sent.

    *sections* fills the template's sections by key (problem, where, change, why,
    impact, workaround, implementation); a missing one keeps the template's prompt.
    """
    clean = str(title).strip()
    slug = slugify(clean)
    if not slug:
        raise ValueError("a proposal needs a title")
    directory = proposals_dir(root)
    number = next_number(directory)
    text = (
        proposal_template()
        .replace("NNN", number)
        .replace("Title", clean, 1)
        .replace("YYYY-MM-DD", date.today().isoformat())
        .replace("<project>", Path(root).name)
    )
    for key, value in (sections or {}).items():
        heading = _HEADINGS.get(key)
        if heading and str(value).strip():
            text = _fill_section(text, heading, str(value).strip())
    path = directory / f"{number}-{slug}.md"
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)
    return number, path


def _fill_section(text: str, heading: str, value: str) -> str:
    start = text.find(heading)
    if start == -1:
        return text
    body_start = start + len(heading) + 1
    following = text.find("\n## ", body_start)
    end = len(text) if following == -1 else following + 1
    return text[:body_start] + value + "\n" + ("\n" if following != -1 else "") + text[end:]


def _new(request: CliRequest) -> int:
    from .ux import UserExperience

    chassis = _chassis(request)
    title = str(request.option("title") or "").strip()
    if not title:
        title = UserExperience(chassis.config).ask_text(
            tr("Proposal title"), required=True, example="job queue ignores the stop timeout"
        )
    if not slugify(title):
        write_line(f"{TAG_ERROR} " + tr("a proposal needs a title"), stream=sys.stderr)
        return int(ExitCode.USAGE_ERROR)
    try:
        number, path = write_proposal(Path(chassis.paths.root), title)
    except OSError as exc:
        reason = tr("{path} could not be written: {reason}", path=_directory(request), reason=exc)
        write_line(f"{TAG_ERROR} {reason}", stream=sys.stderr)
        return int(ExitCode.ERROR)
    if request.flag("as_json"):
        dump_json({"number": number, "title": title, "file": str(path)})
    else:
        write_line(f"{TAG_OK} " + tr("{path} created", path=path))
    return int(ExitCode.SUCCESS)


def _row(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    status = next((line for line in lines if line.startswith("Status:")), "Status: ?")
    issue = ISSUE_LINE.search(text)
    return {
        "number": path.name[:3],
        "title": (lines[0] if lines else "").removeprefix("# ").split(" - ", 1)[-1],
        "status": status.removeprefix("Status:").split("Date:")[0].strip(),
        "issue": issue.group(1).strip() if issue else "",
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
            [(r["number"], r["status"], r["title"], r["issue"]) for r in rows],
            ("number", "status", "title", "issue"),
        )
    return int(ExitCode.SUCCESS)


def _find(request: CliRequest) -> Path | None:
    wanted = str(request.option("number") or "").strip().zfill(3)
    path = next((item for item in _entries(_directory(request)) if item.name[:3] == wanted), None)
    if path is None:
        write_line(f"{TAG_ERROR} " + tr("no proposal {number}; see 'kdx proposal list'",
                                        number=wanted), stream=sys.stderr)
    return path


def _show(request: CliRequest) -> int:
    path = _find(request)
    if path is None:
        return int(ExitCode.ERROR)
    if request.flag("as_json"):
        dump_json({**_row(path), "text": path.read_text(encoding="utf-8")})
        return int(ExitCode.SUCCESS)
    for line in path.read_text(encoding="utf-8").splitlines():
        write_line(line)
    return int(ExitCode.SUCCESS)


def issue_text(path: Path, *, with_project: bool = False) -> tuple[str, str]:
    """Return (title, body) of the issue a proposal becomes: the note plus the environment.

    The project name is replaced unless *with_project*. Only the status line and the
    sections travel: the note's title becomes the issue title, and the local lines (the
    Issue line, the template's advice to the agent) stay behind. This is exactly what
    'send' submits.
    """
    row = _row(path)
    text = path.read_text(encoding="utf-8")
    status = next((line for line in text.splitlines() if line.startswith("Status:")), "")
    if status and not with_project:
        status = re.sub(r"Raised in:\s*\S.*$", "Raised in: (not shared)", status)
    first = text.find("\n## ")
    sections = text[first + 1 :] if first != -1 else ""
    lines = [status, "", *sections.splitlines()] if status else sections.splitlines()
    environment = (
        f"korodexios-core {__version__} (core API v{CORE_API_VERSION}), "
        f"Python {platform.python_version()}, {platform.system()} {platform.release()}"
    )
    body = "\n".join(lines).strip() + f"\n\n---\nEnvironment: {environment}\n"
    return TITLE_PREFIX + row["title"], body


def _export(request: CliRequest) -> int:
    path = _find(request)
    if path is None:
        return int(ExitCode.ERROR)
    title, body = issue_text(path, with_project=request.flag("with_project"))
    target = request.option("output")
    if target:
        atomic_write_text(Path(str(target)), f"{title}\n\n{body}")
        write_line(f"{TAG_OK} " + tr("{path} created", path=target))
        return int(ExitCode.SUCCESS)
    write_line(title)
    write_line()
    for line in body.splitlines():
        write_line(line)
    return int(ExitCode.SUCCESS)


Runner = Any


def _gh(argv: list[str], *, runner: Runner = None) -> subprocess.CompletedProcess[str]:
    run = runner or subprocess.run
    return cast(
        "subprocess.CompletedProcess[str]",
        run(argv, capture_output=True, text=True, timeout=60, check=False),
    )


def similar_issues(
    gh: str, repo: str, title: str, *, runner: Runner = None
) -> list[dict[str, Any]]:
    """Return issues of *repo* whose title shares words with *title* (open and closed)."""
    words = " ".join(word for word in re.findall(r"[A-Za-z0-9]{4,}", title)[:6])
    if not words:
        return []
    try:
        completed = _gh(
            [gh, "issue", "list", "--repo", repo, "--state", "all", "--limit", "5",
             "--search", f"{words} in:title", "--json", "number,title,url,state"],
            runner=runner,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    try:
        found = json.loads(completed.stdout or "[]")
    except ValueError:
        return []
    return [item for item in found if isinstance(item, dict)]


def _record_issue(path: Path, url: str) -> None:
    text = path.read_text(encoding="utf-8")
    if ISSUE_LINE.search(text):
        text = ISSUE_LINE.sub(f"Issue: {url}", text, count=1)
    else:
        text = text.replace("\n\n", f"\nIssue: {url}\n\n", 1)
    atomic_write_text(path, text)


def _send(request: CliRequest) -> int:
    from .ux import UserExperience

    path = _find(request)
    if path is None:
        return int(ExitCode.ERROR)
    repo = str(request.option("repo") or CORE_REPOSITORY).strip()
    if not REPOSITORY.match(repo):
        write_line(f"{TAG_ERROR} " + tr("--repo must be OWNER/NAME, got {repo}", repo=repo),
                   stream=sys.stderr)
        return int(ExitCode.USAGE_ERROR)
    sent = _row(path)["issue"]
    if sent.startswith("http") and not request.flag("again"):
        write_line(f"{TAG_NOTE} " + tr("already sent: {url} (--again sends it once more)",
                                       url=sent))
        return int(ExitCode.SUCCESS)
    title, body = issue_text(path, with_project=request.flag("with_project"))
    write_line(f"{TAG_NOTE} " + tr("this text would be sent to github.com/{repo}:", repo=repo))
    write_line("=" * 72)
    write_line(title)
    write_line()
    for line in body.splitlines():
        write_line(line)
    write_line("=" * 72)
    runner = request.options.get("runner")
    # A caller (a test, an embedding tool) may name the gh binary, or "" for none.
    gh = request.options["gh_path"] if "gh_path" in request.options else shutil.which("gh")
    if gh:
        duplicates = similar_issues(str(gh), repo, title, runner=runner)
        if duplicates:
            write_line(f"{TAG_NOTE} " + tr("issues with a similar title - check them first:"))
            for item in duplicates:
                write_line(f"    #{item.get('number')} [{item.get('state')}] "
                           f"{item.get('title')} {item.get('url')}")
        ux = UserExperience(_chassis(request).config)
        asked = tr("Create the issue on github.com/{repo}?", repo=repo)
        if not (request.flag("confirm") or ux.confirm(asked, default=False)):
            write_line(f"{TAG_NOTE} " + tr("nothing was sent; read the text, then rerun "
                                           "with --confirm or answer yes"))
            return int(ExitCode.SUCCESS)
        body_file = path.with_suffix(".issue.md")
        atomic_write_text(body_file, body)
        try:
            completed = _gh([str(gh), "issue", "create", "--repo", repo, "--title", title,
                             "--body-file", str(body_file)], runner=runner)
        except (OSError, subprocess.SubprocessError) as exc:
            write_line(f"{TAG_ERROR} gh: {exc}", stream=sys.stderr)
            return int(ExitCode.ERROR)
        finally:
            body_file.unlink(missing_ok=True)
        if completed.returncode != 0:
            write_line(f"{TAG_ERROR} gh: {(completed.stderr or completed.stdout).strip()}",
                       stream=sys.stderr)
            write_line(f"{TAG_NOTE} " + tr("sign in with 'gh auth login', or send it by hand "
                                           "with 'kdx proposal export'"))
            return int(ExitCode.ERROR)
        url = (completed.stdout or "").strip().splitlines()[-1] if completed.stdout else ""
        if url:
            _record_issue(path, url)
        write_line(f"{TAG_OK} " + tr("issue created: {url}", url=url or "?"))
        return int(ExitCode.SUCCESS)
    # No gh: hand the owner a form link and a file to paste; only the title goes in the link.
    body_file = path.with_suffix(".issue.md")
    atomic_write_text(body_file, body)
    query = urllib.parse.urlencode({"template": ISSUE_TEMPLATE, "title": title})
    url = f"https://github.com/{repo}/issues/new?{query}"
    write_line(f"{TAG_NOTE} " + tr("the GitHub CLI (gh) is not installed; nothing was sent"))
    write_line(f"{TAG_NOTE} " + tr("open {url} and paste the text of {path}", url=url,
                                   path=body_file))
    if request.flag("open_browser"):
        webbrowser.open(url)
    return int(ExitCode.SUCCESS)

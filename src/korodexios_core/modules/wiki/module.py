"""Wiki module: the project's own knowledge base, for people and for agents.

Two scopes, one format:

- **public** (``docs/wiki/``) - committed and published with the repository: how to use,
  deploy and extend the project;
- **internal** (``docs-dev/wiki/``) - git-ignored developer notes: decisions in progress,
  findings, credentials *locations* (never credentials), plans.

A page exists once per language with the language as a suffix (``deploy.en.md``,
``deploy.sk.md``); ``modules.wiki.languages`` says which languages a page should have and
``kdx wiki check`` reports the missing ones. ``kdx wiki index`` writes an ``INDEX.md`` per
scope that an agent reads first; every command is also an MCP tool (``wiki_search``,
``wiki_show``, ...), and each index is an MCP resource (``kdx://wiki/public``).

Nothing is written until a command asks for it, so a project that never uses the wiki
gets no files; ``modules.wiki.enabled: false`` removes the commands as well.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from ...cli.render import dump_json, print_table, tr, write_line
from ...config.schema import ConfigField, ConfigKind, ConfigSchema
from ...constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ...fsio import atomic_write_text
from ...kernel.cli_contract import CliArgument, CliCommand, CliRequest, CliSurface
from ...kernel.contract import Module
from ...kernel.mcp import McpResource
from .pages import (
    INDEX_NAME,
    Issue,
    Page,
    check_pages,
    page_file,
    render_index,
    render_page,
    scan,
    search,
    valid_slug,
)

SCOPES = ("public", "internal")
DEFAULT_DIRS = {"public": "docs/wiki", "internal": "docs-dev/wiki"}
JSON_ARG = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
SCOPE_ARG = CliArgument(
    ("--scope",), choices=(*SCOPES, "all"), default="all", help="Which wiki [all]."
)
LANG_ARG = CliArgument(("--lang",), dest="language", metavar="LANG",
                       help="Language suffix, e.g. en, sk, de [modules.wiki.default_language].")


class WikiModule(Module):
    """Pages under docs/wiki (public) and docs-dev/wiki (internal), one file per language."""

    name = "wiki"
    description = "Project wiki: public and internal pages, per language, indexed for agents."
    version = "0.1.0"

    def config_schema(self) -> ConfigSchema:
        """Declare the keys this module reads."""
        return ConfigSchema(
            module=self.name,
            fields=(
                ConfigField("public_dir", kind=ConfigKind.STR, default=DEFAULT_DIRS["public"],
                            description="Published pages, committed with the project."),
                ConfigField("internal_dir", kind=ConfigKind.STR,
                            default=DEFAULT_DIRS["internal"],
                            description="Developer notes, kept out of git."),
                ConfigField("languages", kind=ConfigKind.LIST, default=["en"],
                            description="Languages every page should exist in (file suffixes)."),
                ConfigField("default_language", kind=ConfigKind.STR, default="en",
                            description="Language of a page without a suffix, and of new pages."),
            ),
        )

    # -- configuration -------------------------------------------------------------

    def root(self) -> Path:
        return Path(self.require_context().paths.root)

    def directory(self, scope: str) -> Path:
        """Return the directory of *scope*, relative paths read against the project root."""
        raw = Path(self.config.get_str(f"{scope}_dir", DEFAULT_DIRS[scope]))
        return raw if raw.is_absolute() else self.root() / raw

    def languages(self) -> list[str]:
        values = [str(item).strip() for item in self.config.get_list("languages", ["en"]) or []]
        return [item for item in values if item] or [self.default_language()]

    def default_language(self) -> str:
        return str(self.config.get_str("default_language", "en") or "en").strip() or "en"

    def pages(self, scope: str = "all") -> list[Page]:
        chosen = SCOPES if scope == "all" else (scope,)
        default = self.default_language()
        return [page for item in chosen for page in scan(self.directory(item), item, default)]

    def internal_ignored(self) -> bool | None:
        """Return whether git ignores the internal wiki; None when git cannot tell."""
        git = shutil.which("git")
        if git is None:
            return None
        probe = self.directory("internal") / "probe.md"
        try:
            completed = subprocess.run(
                [git, "check-ignore", "-q", str(probe)], cwd=self.root(),
                capture_output=True, text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode == 128:  # not a git repository
            return None
        return completed.returncode == 0

    def mcp_resources(self) -> list[McpResource]:
        """Offer each scope's index to MCP clients, rendered fresh on every read."""
        resources = []
        for scope in SCOPES:
            if self.directory(scope).is_dir():
                resources.append(
                    McpResource(
                        uri=f"kdx://wiki/{scope}",
                        name=f"Wiki index ({scope})",
                        description=f"Every {scope} wiki page with its languages and tags.",
                        read=_index_reader(self, scope),
                    )
                )
        return resources

    def index_text(self, scope: str) -> str:
        return render_index(self.pages(scope), scope, self.languages(), self.directory(scope))

    # -- CLI ----------------------------------------------------------------------

    def register_cli(self, surface: CliSurface) -> None:
        """Contribute 'kdx wiki ...'."""
        surface.add_command(
            CliCommand(
                name="wiki",
                help="The project wiki: public and internal pages, per language.",
                module_name=self.name,
                subcommands=(
                    CliCommand(
                        name="init", module_name=self.name, handler=self._init, args=(JSON_ARG,),
                        help="Create the wiki folders and keep the internal one out of git.",
                    ),
                    CliCommand(
                        name="new", module_name=self.name, handler=self._new,
                        help="Create a page with its header (title, tags, summary).",
                        args=(
                            CliArgument(("slug",), metavar="SLUG",
                                        help="Page name, e.g. deploy or ops/deploy."),
                            CliArgument(("--title",), help="Page title [from the slug]."),
                            CliArgument(("--tags",), help="Comma-separated tags."),
                            CliArgument(("--summary",), help="One-line summary."),
                            CliArgument(("--body",), help="Page text after the heading."),
                            CliArgument(("--internal",), dest="internal", action="store_true",
                                        help="Write to the internal (git-ignored) wiki."),
                            LANG_ARG, JSON_ARG,
                        ),
                    ),
                    CliCommand(
                        name="list", module_name=self.name, handler=self._list,
                        args=(SCOPE_ARG, LANG_ARG, JSON_ARG),
                        help="List pages with their languages and tags.",
                    ),
                    CliCommand(
                        name="show", module_name=self.name, handler=self._show,
                        args=(CliArgument(("slug",), metavar="SLUG", help="Page name."),
                              SCOPE_ARG, LANG_ARG, JSON_ARG),
                        help="Print one page (the default language when LANG is missing).",
                    ),
                    CliCommand(
                        name="search", module_name=self.name, handler=self._search,
                        args=(CliArgument(("query",), nargs="+", metavar="WORD",
                                          help="Words to look for."), SCOPE_ARG, JSON_ARG),
                        help="Find pages holding every word of the query.",
                    ),
                    CliCommand(
                        name="index", module_name=self.name, handler=self._index,
                        args=(JSON_ARG,),
                        help="Write INDEX.md for each wiki (what an agent reads first).",
                    ),
                    CliCommand(
                        name="check", module_name=self.name, handler=self._check,
                        args=(JSON_ARG,),
                        help="Report broken links, missing headers and translations.",
                    ),
                ),
            )
        )

    def _init(self, request: CliRequest) -> int:
        created = []
        for scope in SCOPES:
            directory = self.directory(scope)
            if not directory.is_dir():
                directory.mkdir(parents=True, exist_ok=True)
                created.append(str(directory))
        ignored = self.internal_ignored()
        added = ""
        if ignored is False:
            relative = self.directory("internal").relative_to(self.root()).as_posix()
            gitignore = self.root() / ".gitignore"
            current = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
            line = f"/{relative}/"
            if line not in current.splitlines():
                text = current + ("" if not current or current.endswith("\n") else "\n")
                atomic_write_text(gitignore, f"{text}# internal wiki (kdx wiki init)\n{line}\n")
                added = line
        if request.flag("as_json"):
            dump_json({"created": created, "gitignore_added": added or None,
                       "internal_ignored": True if added else ignored})
            return int(ExitCode.SUCCESS)
        for item in created:
            write_line(f"{TAG_OK} " + tr("{path} created", path=item))
        if added:
            write_line(f"{TAG_OK} " + tr("{line} added to .gitignore", line=added))
        elif ignored is None:
            write_line(f"{TAG_NOTE} " + tr("not a git repository: keep the internal wiki out "
                                           "of version control yourself"))
        write_line(f"{TAG_NOTE} " + tr("next: kdx wiki new home --title \"Start here\""))
        return int(ExitCode.SUCCESS)

    def _new(self, request: CliRequest) -> int:
        slug = str(request.option("slug") or "").strip().strip("/")
        if not valid_slug(slug):
            write_line(f"{TAG_ERROR} " + tr("{slug} is not a page name: lower-case letters, "
                                            "digits, '-', '_', up to two folders", slug=slug))
            return int(ExitCode.USAGE_ERROR)
        scope = "internal" if request.flag("internal") else "public"
        language = str(request.option("language") or self.default_language())
        path = page_file(self.directory(scope), slug, language)
        if path.exists():
            write_line(f"{TAG_ERROR} " + tr("{path} already exists", path=path))
            return int(ExitCode.ERROR)
        title = str(request.option("title") or "").strip() or slug.rsplit("/", 1)[-1].replace(
            "-", " ").replace("_", " ").capitalize()
        tags = [item.strip() for item in str(request.option("tags") or "").split(",")
                if item.strip()]
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, render_page(title, tags=tags,
                                            summary=str(request.option("summary") or ""),
                                            body=str(request.option("body") or "")))
        if request.flag("as_json"):
            dump_json({"slug": slug, "scope": scope, "language": language, "path": str(path)})
        else:
            write_line(f"{TAG_OK} " + tr("{path} created", path=path))
        return int(ExitCode.SUCCESS)

    def _list(self, request: CliRequest) -> int:
        language = request.option("language")
        pages = [page for page in self.pages(str(request.option("scope") or "all"))
                 if not language or page.language == language]
        if request.flag("as_json"):
            dump_json({"pages": [page.as_dict() for page in pages],
                       "languages": self.languages()})
            return int(ExitCode.SUCCESS)
        if not pages:
            write_line(f"{TAG_NOTE} " + tr("the wiki is empty; start with 'kdx wiki init'"))
            return int(ExitCode.SUCCESS)
        print_table([[page.slug, page.language, page.scope, page.title, ", ".join(page.tags)]
                     for page in pages], headers=("page", "lang", "scope", "title", "tags"))
        return int(ExitCode.SUCCESS)

    def _show(self, request: CliRequest) -> int:
        slug = str(request.option("slug") or "").strip().strip("/")
        wanted = str(request.option("language") or self.default_language())
        versions = [page for page in self.pages(str(request.option("scope") or "all"))
                    if page.slug == slug]
        if not versions:
            write_line(f"{TAG_ERROR} " + tr("no page {slug}; try 'kdx wiki search'", slug=slug))
            return int(ExitCode.ERROR)
        page = next((item for item in versions if item.language == wanted), None)
        fallback = page is None
        if page is None:
            page = next((item for item in versions if item.language == self.default_language()),
                        versions[0])
        if request.flag("as_json"):
            dump_json({**page.as_dict(body=True), "requested_language": wanted,
                       "fallback": fallback,
                       "languages": sorted(item.language for item in versions)})
            return int(ExitCode.SUCCESS)
        if fallback:
            write_line(f"{TAG_NOTE} " + tr("no {wanted} version; showing {language}",
                                           wanted=wanted, language=page.language))
        for line in page.path.read_text(encoding="utf-8").splitlines():
            write_line(line)
        return int(ExitCode.SUCCESS)

    def _search(self, request: CliRequest) -> int:
        words = request.option("query") or []
        query = " ".join(words) if isinstance(words, list) else str(words)
        hits = search(self.pages(str(request.option("scope") or "all")), query)
        if request.flag("as_json"):
            dump_json({"query": query, "hits": [
                {**page.as_dict(), "score": score, "snippets": snippets}
                for page, score, snippets in hits
            ]})
            return int(ExitCode.SUCCESS)
        if not hits:
            write_line(f"{TAG_NOTE} " + tr("nothing found for {query}", query=query))
            return int(ExitCode.SUCCESS)
        for page, _score, snippets in hits:
            write_line(f"{TAG_OK} {page.slug} [{page.language}, {page.scope}] - {page.title}")
            for snippet in snippets:
                write_line(f"    {snippet}")
        return int(ExitCode.SUCCESS)

    def _index(self, request: CliRequest) -> int:
        written = []
        for scope in SCOPES:
            directory = self.directory(scope)
            if not directory.is_dir():
                continue
            target = directory / INDEX_NAME
            text = self.index_text(scope)
            if not target.is_file() or target.read_text(encoding="utf-8") != text:
                atomic_write_text(target, text)
            written.append(str(target))
        if request.flag("as_json"):
            dump_json({"written": written})
        elif not written:
            write_line(f"{TAG_NOTE} " + tr("the wiki is empty; start with 'kdx wiki init'"))
        for item in written if not request.flag("as_json") else []:
            write_line(f"{TAG_OK} " + tr("{path} is up to date", path=item))
        return int(ExitCode.SUCCESS)

    def _check(self, request: CliRequest) -> int:
        issues = []
        for scope in SCOPES:
            directory = self.directory(scope)
            if not directory.is_dir():
                continue
            target = directory / INDEX_NAME
            current = target.is_file() and target.read_text(encoding="utf-8") == (
                self.index_text(scope)
            )
            issues.extend(check_pages(self.pages(scope), self.languages(),
                                      index_current=current))
        ignored = self.internal_ignored()
        if ignored is False and self.directory("internal").is_dir():
            issues.append(_internal_tracked(self.directory("internal")))
        errors = [issue for issue in issues if issue.severity == "error"]
        if request.flag("as_json"):
            dump_json({"issues": [issue.as_dict() for issue in issues], "errors": len(errors)})
        else:
            for issue in issues:
                tag = TAG_ERROR if issue.severity == "error" else TAG_NOTE
                write_line(f"{tag} {issue.path}: {issue.message}")
            if not issues:
                write_line(f"{TAG_OK} " + tr("the wiki is in order"))
        return int(ExitCode.ERROR if errors else ExitCode.SUCCESS)


def _internal_tracked(directory: Path) -> Issue:
    return Issue(
        "error", str(directory),
        "the internal wiki is not git-ignored; run 'kdx wiki init' or add it to .gitignore",
    )


def _index_reader(module: WikiModule, scope: str) -> Any:
    def read() -> str:
        return module.index_text(scope)

    return read


MODULE = WikiModule()

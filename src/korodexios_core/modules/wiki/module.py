"""Wiki module: the project's knowledge base in the LLM-wiki layout, for people and agents.

Two scopes, one format:

- **public** (``docs/wiki/``) - committed and published with the repository;
- **internal** (``docs-dev/wiki/``) - git-ignored developer notes.

Each scope is an LLM-wiki of its own (``raw/`` sources, typed pages, ``SCHEMA.md``,
``index.md``, an append-only ``log.md``), so it can be opened in Obsidian, maintained by an
agent the LLM-wiki way (ingest, query, lint), pointed at an existing LLM-wiki
(``modules.wiki.public_dir``), or merged into the wiki its user already runs
(``kdx wiki export``). Every command is also an MCP tool, and each index and schema is an MCP
resource (``kdx://wiki/public``, ``kdx://wiki/public/schema``).

Nothing is written until a command asks for it; ``modules.wiki.enabled: false`` removes the
commands as well.
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
from .export import apply_export, bundle, plan_export
from .pages import (
    INDEX_MARKER,
    INDEX_NAME,
    RAW_DIR,
    SCHEMA_NAME,
    Issue,
    Page,
    append_log,
    check_pages,
    page_path,
    raw_files,
    read_log,
    render_index,
    render_page,
    scan,
    schema_text,
    search,
    valid_slug,
)
from .sources import ingest_source

SCOPES = ("public", "internal")
DEFAULT_DIRS = {"public": "docs/wiki", "internal": "docs-dev/wiki"}
JSON_ARG = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
SCOPE_ARG = CliArgument(
    ("--scope",), choices=(*SCOPES, "all"), default="all", help="Which wiki [all]."
)
LANG_ARG = CliArgument(("--lang",), dest="language", metavar="LANG",
                       help="Language suffix, e.g. en, sk, de [modules.wiki.default_language].")
INTERNAL_ARG = CliArgument(("--internal",), dest="internal", action="store_true",
                           help="Write to the internal (git-ignored) wiki.")
TYPE_ARG = CliArgument(("--type",), dest="page_type", metavar="TYPE",
                       help="Page type: page, source, entity, concept, synthesis, decision, "
                       "guide, or your own [page].")


class WikiModule(Module):
    """Pages under docs/wiki (public) and docs-dev/wiki (internal), in the LLM-wiki layout."""

    name = "wiki"
    description = "Project wiki (LLM-wiki layout): public and internal, per language, for agents."
    version = "0.2.0"

    def config_schema(self) -> ConfigSchema:
        """Declare the keys this module reads."""
        return ConfigSchema(
            module=self.name,
            fields=(
                ConfigField("public_dir", kind=ConfigKind.STR, default=DEFAULT_DIRS["public"],
                            description="Published wiki, committed with the project."),
                ConfigField("internal_dir", kind=ConfigKind.STR,
                            default=DEFAULT_DIRS["internal"],
                            description="Developer wiki, kept out of git."),
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
        return None if completed.returncode == 128 else completed.returncode == 0  # 128: no repo

    def mcp_resources(self) -> list[McpResource]:
        """Offer each scope's index and schema to MCP clients, read fresh every time."""
        resources = []
        for scope in SCOPES:
            directory = self.directory(scope)
            if not directory.is_dir():
                continue
            resources.append(McpResource(
                uri=f"kdx://wiki/{scope}", name=f"Wiki index ({scope})",
                description=f"Every {scope} wiki page with its summary, type and languages.",
                read=_reader(lambda scope=scope: self.index_text(scope)),
            ))
            schema = directory / SCHEMA_NAME
            if schema.is_file():
                resources.append(McpResource(
                    uri=f"kdx://wiki/{scope}/schema", name=f"Wiki schema ({scope})",
                    description="The conventions of this wiki: read before writing a page.",
                    read=_reader(lambda path=schema: path.read_text(encoding="utf-8")),
                ))
        return resources

    def index_text(self, scope: str) -> str:
        return render_index(self.pages(scope), scope, self.languages())

    def _scope(self, request: CliRequest) -> str:
        return "internal" if request.flag("internal") else "public"

    def _log(self, scope: str, operation: str, description: str) -> None:
        directory = self.directory(scope)
        if directory.is_dir():
            append_log(directory, operation, description)

    # -- CLI ----------------------------------------------------------------------

    def register_cli(self, surface: CliSurface) -> None:
        """Contribute 'kdx wiki ...'."""
        surface.add_command(
            CliCommand(
                name="wiki",
                help="The project wiki (LLM-wiki layout): public and internal, per language.",
                module_name=self.name,
                subcommands=(
                    CliCommand(
                        name="init", module_name=self.name, handler=self._init, args=(JSON_ARG,),
                        help="Create both wikis (SCHEMA.md, raw/, log.md) and keep the internal "
                        "one out of git.",
                    ),
                    CliCommand(
                        name="new", module_name=self.name, handler=self._new,
                        help="Create a page with its header (type, title, tags, sources).",
                        args=(
                            CliArgument(("slug",), metavar="SLUG",
                                        help="Page name, e.g. deploy or ops/deploy."),
                            CliArgument(("--title",), help="Page title [from the slug]."),
                            TYPE_ARG,
                            CliArgument(("--tags",), help="Comma-separated tags."),
                            CliArgument(("--sources",),
                                        help="Comma-separated names of the source pages used."),
                            CliArgument(("--summary",), help="One-line summary."),
                            CliArgument(("--body",), help="Page text after the heading."),
                            INTERNAL_ARG, LANG_ARG, JSON_ARG,
                        ),
                    ),
                    CliCommand(
                        name="ingest", module_name=self.name, handler=self._ingest,
                        help="Copy a source into raw/ and create its source page to fill in.",
                        args=(
                            CliArgument(("file",), metavar="FILE",
                                        help="The source: an article, PDF, transcript, notes."),
                            CliArgument(("--title",), help="Source title [from the file name]."),
                            CliArgument(("--slug",), help="Page name [from the file name]."),
                            CliArgument(("--tags",), help="Comma-separated tags."),
                            INTERNAL_ARG, JSON_ARG,
                        ),
                    ),
                    CliCommand(
                        name="log", module_name=self.name, handler=self._log_command,
                        help="Show the wiki log, or append one entry to it.",
                        args=(
                            CliArgument(("operation",), nargs="?", metavar="OP",
                                        help="Entry kind: update, query, lint, schema, ..."),
                            CliArgument(("text",), nargs="*", metavar="TEXT",
                                        help="What happened, in one line."),
                            CliArgument(("-n", "--lines"), dest="limit", type="int", default=20,
                                        help="How many entries to show [20]."),
                            INTERNAL_ARG, JSON_ARG,
                        ),
                    ),
                    CliCommand(
                        name="list", module_name=self.name, handler=self._list,
                        args=(SCOPE_ARG, LANG_ARG, TYPE_ARG, JSON_ARG),
                        help="List pages with their type, languages and tags.",
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
                        help="Write index.md for each wiki (what an agent reads first).",
                    ),
                    CliCommand(
                        name="check", module_name=self.name, handler=self._check,
                        args=(CliArgument(("--record",), dest="record", action="store_true",
                                          help="Record the check in log.md."), JSON_ARG),
                        help="Lint: links, sources, orphans, raw files, translations, size.",
                    ),
                    CliCommand(
                        name="export", module_name=self.name, handler=self._export,
                        danger=True,
                        help="Merge this wiki into another one (an LLM-wiki, Obsidian, a git "
                        "wiki), or print it as JSON.",
                        args=(
                            CliArgument(("target",), nargs="?", metavar="DIR",
                                        help="Root of the wiki to merge into."),
                            CliArgument(("--namespace",), metavar="NAME",
                                        help="Folder in the target [the project name]."),
                            CliArgument(("--prefix",), metavar="NAME",
                                        help="Rename exported pages NAME-<page> to avoid clashes."),
                            CliArgument(("--format",), dest="output_format",
                                        choices=("files", "json"), default="files",
                                        help="files (merge into DIR) or json (print a bundle)."),
                            CliArgument(("--dry-run",), dest="dry_run", action="store_true",
                                        help="Show the plan; write nothing."),
                            CliArgument(("--internal",), dest="internal", action="store_true",
                                        help="Export the internal wiki (it leaves the project)."),
                            JSON_ARG,
                        ),
                    ),
                ),
            )
        )

    def _init(self, request: CliRequest) -> int:
        created = []
        for scope in SCOPES:
            directory = self.directory(scope)
            for folder in (directory, directory / RAW_DIR):
                if not folder.is_dir():
                    folder.mkdir(parents=True, exist_ok=True)
                    created.append(str(folder))
            schema = directory / SCHEMA_NAME
            if not schema.is_file():
                atomic_write_text(schema, schema_text(scope, self.languages(),
                                                      self.default_language()))
                created.append(str(schema))
                append_log(directory, "schema", f"wiki created ({scope})")
        added = self._ignore_internal()
        ignored = True if added else self.internal_ignored()
        if request.flag("as_json"):
            dump_json({"created": created, "gitignore_added": added or None,
                       "internal_ignored": ignored})
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

    def _ignore_internal(self) -> str:
        if self.internal_ignored() is not False:
            return ""
        relative = self.directory("internal").relative_to(self.root()).as_posix()
        gitignore = self.root() / ".gitignore"
        current = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
        line = f"/{relative}/"
        if line in current.splitlines():
            return ""
        text = current + ("" if not current or current.endswith("\n") else "\n")
        atomic_write_text(gitignore, f"{text}# internal wiki (kdx wiki init)\n{line}\n")
        return line

    def _new(self, request: CliRequest) -> int:
        slug = str(request.option("slug") or "").strip().strip("/")
        if not valid_slug(slug):
            write_line(f"{TAG_ERROR} " + tr("{slug} is not a page name: lower-case letters, "
                                            "digits, '-', '_', up to two folders", slug=slug))
            return int(ExitCode.USAGE_ERROR)
        scope = self._scope(request)
        language = str(request.option("language") or self.default_language())
        page_type = str(request.option("page_type") or "").strip() or "page"
        path = page_path(self.directory(scope), slug, language, self.default_language(),
                         page_type)
        if path.exists():
            write_line(f"{TAG_ERROR} " + tr("{path} already exists", path=path))
            return int(ExitCode.ERROR)
        title = str(request.option("title") or "").strip() or _title(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, render_page(
            title, page_type=page_type, tags=_list(request.option("tags")),
            sources=_list(request.option("sources")),
            summary=str(request.option("summary") or ""),
            body=str(request.option("body") or ""),
        ))
        self._log(scope, "create", f"{slug} ({language}): {title}")
        if request.flag("as_json"):
            dump_json({"slug": slug, "scope": scope, "language": language, "type": page_type,
                       "path": str(path)})
        else:
            write_line(f"{TAG_OK} " + tr("{path} created", path=path))
        return int(ExitCode.SUCCESS)

    def _ingest(self, request: CliRequest) -> int:
        source = ingest_source(request, self.root(), self.require_context().paths)
        if source is None or not source.is_file():
            write_line(f"{TAG_ERROR} " + tr("{path} not found", path=request.option("file", "")))
            return int(ExitCode.ERROR)
        scope = self._scope(request)
        directory = self.directory(scope)
        slug = str(request.option("slug") or "").strip() or _slugify(source.stem)
        if not valid_slug(slug) or "/" in slug:
            write_line(f"{TAG_ERROR} " + tr("{slug} is not a page name: lower-case letters, "
                                            "digits, '-', '_', up to two folders", slug=slug))
            return int(ExitCode.USAGE_ERROR)
        raw = directory / RAW_DIR / f"{slug}{source.suffix.lower()}"
        page = page_path(directory, slug, self.default_language(), self.default_language(),
                         "source")
        for existing in (raw, page):
            if existing.exists():
                write_line(f"{TAG_ERROR} " + tr("{path} already exists", path=existing))
                return int(ExitCode.ERROR)
        title = str(request.option("title") or "").strip() or _title(source.stem)
        raw.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, raw)
        relative = raw.relative_to(directory).as_posix()
        page.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(page, render_page(
            title, page_type="source", tags=_list(request.option("tags")), raw=relative,
            body=(f"Summary of `{relative}` in this wiki's words: what it says, its key "
                  "claims, and the pages it touches, linked by name. Read the raw file "
                  "and replace this paragraph."),
        ))
        append_log(directory, "ingest", f"{title} ({relative})")
        if request.flag("as_json"):
            dump_json({"raw": str(raw), "page": str(page), "name": slug, "scope": scope})
            return int(ExitCode.SUCCESS)
        write_line(f"{TAG_OK} " + tr("{path} created", path=raw))
        write_line(f"{TAG_OK} " + tr("{path} created", path=page))
        write_line(f"{TAG_NOTE} " + tr("next: write the summary, update the pages it touches, "
                                       "then kdx wiki index"))
        return int(ExitCode.SUCCESS)

    def _log_command(self, request: CliRequest) -> int:
        scope = self._scope(request)
        directory = self.directory(scope)
        operation = str(request.option("operation") or "").strip()
        words = request.option("text") or []
        text = " ".join(words) if isinstance(words, list) else str(words)
        if operation:
            if not text.strip():
                write_line(f"{TAG_ERROR} " + tr("say what happened: kdx wiki log update "
                                                "\"what changed\""))
                return int(ExitCode.USAGE_ERROR)
            appended = append_log(directory, operation, text)
            if request.flag("as_json"):
                dump_json({"appended": appended, "scope": scope})
            else:
                write_line(f"{TAG_OK} {appended}")
            return int(ExitCode.SUCCESS)
        entries = read_log(directory, int(request.option("limit") or 20))
        if request.flag("as_json"):
            dump_json({"scope": scope, "entries": entries})
        elif not entries:
            write_line(f"{TAG_NOTE} " + tr("the wiki is empty; start with 'kdx wiki init'"))
        for entry in entries if not request.flag("as_json") else []:
            write_line(f"[{entry['date']}] {entry['operation']} | {entry['description']}")
        return int(ExitCode.SUCCESS)

    def _list(self, request: CliRequest) -> int:
        language = request.option("language")
        page_type = request.option("page_type")
        pages = [page for page in self.pages(str(request.option("scope") or "all"))
                 if (not language or page.language == language)
                 and (not page_type or page.type == page_type)]
        if request.flag("as_json"):
            dump_json({"pages": [page.as_dict() for page in pages],
                       "languages": self.languages()})
            return int(ExitCode.SUCCESS)
        if not pages:
            write_line(f"{TAG_NOTE} " + tr("the wiki is empty; start with 'kdx wiki init'"))
            return int(ExitCode.SUCCESS)
        print_table([[page.slug, page.type or "-", page.language, page.scope, page.title,
                      ", ".join(page.tags)] for page in pages],
                    headers=("page", "type", "lang", "scope", "title", "tags"))
        return int(ExitCode.SUCCESS)

    def _show(self, request: CliRequest) -> int:
        wanted_slug = str(request.option("slug") or "").strip().strip("/")
        wanted = str(request.option("language") or self.default_language())
        versions = [page for page in self.pages(str(request.option("scope") or "all"))
                    if wanted_slug in (page.slug, page.name)]
        if not versions:
            write_line(f"{TAG_ERROR} " + tr("no page {slug}; try 'kdx wiki search'",
                                            slug=wanted_slug))
            return int(ExitCode.ERROR)
        page = next((item for item in versions if item.language == wanted), None)
        fallback = page is None
        if page is None:
            page = next((item for item in versions if item.language == self.default_language()),
                        versions[0])
        if request.flag("as_json"):
            dump_json({**page.as_dict(body=True), "requested_language": wanted,
                       "fallback": fallback,
                       "languages": sorted({item.language for item in versions})})
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
            _drop_legacy_index(directory)
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
        issues: list[Issue] = []
        for scope in SCOPES:
            directory = self.directory(scope)
            if not directory.is_dir():
                continue
            target = directory / INDEX_NAME
            current = target.is_file() and target.read_text(encoding="utf-8") == (
                self.index_text(scope)
            )
            found = check_pages(self.pages(scope), self.languages(), directory=directory,
                                index_current=current)
            issues.extend(found)
            if request.flag("record"):
                failed = sum(1 for item in found if item.severity == "error")
                append_log(directory, "lint", f"{failed} error(s), {len(found) - failed} "
                           "warning(s)")
        if self.internal_ignored() is False and self.directory("internal").is_dir():
            issues.append(Issue(
                "error", str(self.directory("internal")),
                "the internal wiki is not git-ignored; run 'kdx wiki init' or add it to "
                ".gitignore",
            ))
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

    def _export(self, request: CliRequest) -> int:
        scope = self._scope(request)
        directory = self.directory(scope)
        pages = self.pages(scope)
        project = self.root().name
        raw = raw_files(directory)
        if request.option("output_format") == "json":
            dump_json(bundle(pages, project=project, scope=scope, raw=raw))
            return int(ExitCode.SUCCESS)
        target_text = str(request.option("target") or "").strip()
        if not target_text:
            write_line(f"{TAG_ERROR} " + tr("name the wiki to merge into: kdx wiki export DIR"))
            return int(ExitCode.USAGE_ERROR)
        namespace = str(request.option("namespace") or "").strip() or _slugify(project)
        try:
            plan = plan_export(pages, directory, raw, Path(target_text).expanduser(),
                               namespace=namespace, prefix=str(request.option("prefix") or ""),
                               scope=scope, languages=self.languages())
        except ValueError as exc:
            write_line(f"{TAG_ERROR} {exc}")
            return int(ExitCode.USAGE_ERROR)
        if scope == "internal":
            write_line(f"{TAG_NOTE} " + tr("the internal wiki is exported: its notes leave the "
                                           "project"))
        if request.flag("as_json") and request.flag("dry_run"):
            dump_json(plan.as_dict())
            return int(ExitCode.SUCCESS if not plan.collisions else ExitCode.ERROR)
        if not request.flag("as_json"):
            _print_plan(plan.as_dict())
        if plan.collisions:
            write_line(f"{TAG_ERROR} " + tr("the target already has pages with these names; "
                                            "pass --prefix NAME"))
            return int(ExitCode.ERROR)
        if request.flag("dry_run"):
            write_line(f"{TAG_NOTE} " + tr("dry run: nothing was written"))
            return int(ExitCode.SUCCESS)
        done = apply_export(plan, project=project)
        self._log(scope, "export", f"{len(plan.writes)} written, {len(plan.removes)} removed "
                  f"into {plan.folder}")
        if request.flag("as_json"):
            dump_json({**plan.as_dict(), "done": done})
        else:
            write_line(f"{TAG_OK} " + tr("{count} change(s) written to {path}",
                                         count=len(done), path=plan.folder))
        return int(ExitCode.SUCCESS)


def _print_plan(plan: dict[str, Any]) -> None:
    for key, tag in (("write", TAG_OK), ("remove", TAG_NOTE), ("kept", TAG_NOTE),
                     ("collisions", TAG_ERROR)):
        for item in plan[key]:
            write_line(f"{tag} {key}: {item}")
    write_line(f"{TAG_NOTE} " + tr("{count} file(s) unchanged", count=len(plan["unchanged"])))


def _drop_legacy_index(directory: Path) -> None:
    """Remove a generated 'INDEX.md' of kdx 0.6.0; 'index.md' replaces it."""
    for entry in directory.iterdir():
        generated = entry.name == "INDEX.md" and entry.is_file() and (
            INDEX_MARKER in entry.read_text(encoding="utf-8")
        )
        if generated:
            entry.unlink()


def _title(slug: str) -> str:
    return slug.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").strip().capitalize()


def _slugify(text: str) -> str:
    cleaned = "".join(char if char.isalnum() else "-" for char in str(text).lower())
    return "-".join(part for part in cleaned.split("-") if part)[:64] or "source"


def _list(value: Any) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _reader(read: Any) -> Any:
    def call() -> str:
        return str(read())

    return call


MODULE = WikiModule()

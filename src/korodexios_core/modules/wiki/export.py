"""Merge a project wiki into a wiki its user already runs: an LLM-wiki, an Obsidian vault,
a Git-backed wiki (GitHub, Gitea, GitLab), or any folder of Markdown.

The pages land in one folder of the target wiki, ``<target>/<namespace>/``, with a catalog
page ``<namespace>-index.md``. Raw sources go where the target keeps its own: an LLM-wiki
has ``raw/`` beside ``wiki/``, so they land in ``<base>/raw/<namespace>/`` and each source
page's ``raw:`` field points there; a plain folder gets ``<namespace>/raw/``. Outside those
two folders the export only appends: one line to the target's ``log.md`` and one link to its
``index.md``, each written once.

Wikilinks in LLM-wiki and Obsidian are global by name, so a page called ``deploy`` would
clash with the user's own ``deploy``: the export refuses such a clash, and ``--prefix``
renames every exported page (``deploy`` -> ``myapp-deploy``), rewriting links and ``sources``.

A manifest (``.kdx-export.json``) remembers what was written and its hash. The next export
updates what changed, removes what the project deleted, and keeps - never overwrites - a file
someone edited in the target since. ``--dry-run`` prints the plan and writes nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final

import yaml

from ...fsio import atomic_write_text
from .pages import (
    CODE,
    INDEX_NAME,
    LINK,
    LOG_NAME,
    RAW_DIR,
    WIKILINK,
    Page,
    dump_header,
    render_index,
    skipped_path,
)

__all__ = ["MANIFEST_NAME", "ExportPlan", "apply_export", "bundle", "plan_export"]

MANIFEST_NAME: Final[str] = ".kdx-export.json"
NAMESPACE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RAW_KEY: Final[str] = "@raw/"
"""Manifest keys of raw files start with this; they live in the plan's raw folder."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(slots=True)
class ExportPlan:
    """What an export would do, file by file, before anything is written."""

    target: Path
    namespace: str
    prefix: str
    raw_root: Path
    raw_reference: str
    """How a source page names its raw file, e.g. 'raw/myapp' (relative to the wiki base)."""
    index_name: str = ""
    writes: dict[str, bytes] = field(default_factory=dict)
    unchanged: list[str] = field(default_factory=list)
    removes: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    """Files edited in the target since the last export (or the target's own): left alone."""
    collisions: list[str] = field(default_factory=list)
    manifest: dict[str, str] = field(default_factory=dict)

    @property
    def folder(self) -> Path:
        return self.target / self.namespace

    def path_of(self, key: str) -> Path:
        """Return the file a plan key names: a page under the folder, a raw file under raw."""
        if key.startswith(RAW_KEY):
            return self.raw_root / key[len(RAW_KEY):]
        return self.folder / key

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": str(self.target),
            "namespace": self.namespace,
            "prefix": self.prefix or None,
            "raw_folder": str(self.raw_root),
            "write": sorted(self.writes),
            "unchanged": sorted(self.unchanged),
            "remove": sorted(self.removes),
            "kept": sorted(self.kept),
            "collisions": sorted(self.collisions),
        }


def _rename(text: str, names: Mapping[str, str]) -> str:
    """Rewrite [[name]] links and Markdown links to renamed pages; code is left as written."""
    if not names:
        return text
    pieces: list[str] = []
    last = 0
    for match in CODE.finditer(text):
        pieces.append(_rename_prose(text[last:match.start()], names))
        pieces.append(match.group(0))
        last = match.end()
    pieces.append(_rename_prose(text[last:], names))
    return "".join(pieces)


def _rename_prose(text: str, names: Mapping[str, str]) -> str:
    def wiki(match: re.Match[str]) -> str:
        renamed = names.get(match.group(1).strip().casefold())
        if renamed is None:
            return match.group(0)
        return f"[[{renamed}{match.group(2) or ''}{match.group(3) or ''}]]"

    def markdown(match: re.Match[str]) -> str:
        target = match.group(1)
        head, _, file_name = target.rpartition("/")
        stem, _, suffix = file_name.partition(".")
        renamed = names.get(stem.casefold())
        if renamed is None or not target.endswith(".md"):
            return match.group(0)
        return f"]({head + '/' if head else ''}{renamed}.{suffix}{match.group(2) or ''})"

    return LINK.sub(markdown, WIKILINK.sub(wiki, text))


def _rewrite_header(text: str, names: Mapping[str, str], raw_reference: str) -> str:
    """Rename the header's 'sources' and point 'raw' at the raw file's new home."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    try:
        header = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return text
    if not isinstance(header, dict):
        return text
    changed = False
    sources = header.get("sources")
    if names and isinstance(sources, list) and sources:
        header["sources"] = [names.get(str(item).casefold(), str(item)) for item in sources]
        changed = True
    raw = str(header.get("raw") or "")
    if raw.startswith(f"{RAW_DIR}/") and raw_reference != RAW_DIR:
        header["raw"] = f"{raw_reference}/{raw[len(RAW_DIR) + 1:]}"
        changed = True
    if not changed:
        return text
    return f"---\n{dump_header(header).strip()}{text[end:]}"


def _foreign_names(target: Path, namespace: str) -> set[str]:
    """Return the page names the target wiki already has, outside our namespace folder."""
    names: set[str] = set()
    if not target.is_dir():
        return names
    for path in target.rglob("*.md"):
        relative = path.relative_to(target)
        if (relative.parts and relative.parts[0] == namespace) or skipped_path(relative):
            continue
        names.add(path.name[: -len(".md")].split(".", 1)[0].casefold())
    return names


def _raw_home(target: Path, namespace: str) -> tuple[Path, str]:
    """Return (folder, reference) for raw files: beside the wiki when it keeps raw/ there."""
    sibling = target.parent / RAW_DIR
    if sibling.is_dir() and not (target / RAW_DIR).is_dir():
        return sibling / namespace, f"{RAW_DIR}/{namespace}"
    return target / namespace / RAW_DIR, RAW_DIR


def plan_export(
    pages: Sequence[Page],
    source: Path,
    raw: Sequence[str],
    target: Path,
    *,
    namespace: str,
    prefix: str = "",
    scope: str = "public",
    languages: Sequence[str] = ("en",),
) -> ExportPlan:
    """Decide what exporting *pages* (and their *raw* files) into *target* would do."""
    if not NAMESPACE.match(namespace):
        raise ValueError(f"namespace {namespace!r} must be lower-case letters, digits, '-', '_'")
    if prefix and not NAMESPACE.match(prefix):
        raise ValueError(f"prefix {prefix!r} must be lower-case letters, digits, '-', '_'")
    raw_root, raw_reference = _raw_home(target, namespace)
    plan = ExportPlan(target=target, namespace=namespace, prefix=prefix, raw_root=raw_root,
                      raw_reference=raw_reference, index_name=f"{prefix or namespace}-index")
    renames = {page.name.casefold(): f"{prefix}-{page.name}" for page in pages} if prefix else {}
    foreign = _foreign_names(target, namespace)
    if not prefix:
        plan.collisions = sorted({page.name for page in pages if page.name.casefold() in foreign})
    wanted: dict[str, bytes] = {}
    for page in pages:
        location = page.path.relative_to(source)
        text = page.path.read_text(encoding="utf-8")
        if prefix:
            location = location.with_name(f"{prefix}-{location.name}")
            text = _rename(text, renames)
        text = _rewrite_header(text, renames, raw_reference)
        wanted[location.as_posix()] = text.encode("utf-8")
    for item in raw:
        wanted[RAW_KEY + item[len(RAW_DIR) + 1:]] = (source / item).read_bytes()
    wanted[f"{plan.index_name}.md"] = _namespace_index(
        pages, renames, namespace, scope, languages
    ).encode("utf-8")
    previous = _read_manifest(plan.folder)
    for key, data in sorted(wanted.items()):
        digest = _digest(data)
        plan.manifest[key] = digest
        existing = plan.path_of(key)
        if not existing.exists():
            plan.writes[key] = data
            continue
        current = _digest(existing.read_bytes())
        if current == digest:
            plan.unchanged.append(key)
        elif key not in previous:
            plan.kept.append(key)  # the target's own file: never ours to replace
            plan.manifest.pop(key)
        elif previous[key] != current:
            plan.kept.append(key)  # edited in the target since the last export
            plan.manifest[key] = previous[key]
        else:
            plan.writes[key] = data
    for key, digest in previous.items():
        if key in wanted:
            continue
        existing = plan.path_of(key)
        if existing.exists() and _digest(existing.read_bytes()) == digest:
            plan.removes.append(key)
        elif existing.exists():
            plan.kept.append(key)
            plan.manifest[key] = digest
    return plan


def _namespace_index(
    pages: Sequence[Page], renames: Mapping[str, str], namespace: str, scope: str,
    languages: Sequence[str],
) -> str:
    """Return the catalog page of the exported folder, with a header LLM-wiki tools accept."""
    renamed = [
        Page(**{**{name: getattr(page, name) for name in Page.__slots__},
                "name": renames.get(page.name.casefold(), page.name)})
        for page in pages
    ]
    stamp: Any = date.today()
    header = dump_header({
        "type": "synthesis",
        "title": f"{namespace} wiki ({scope})",
        "tags": ["index", namespace],
        "sources": [],
        "created": stamp,
        "updated": stamp,
    }).strip()
    return f"---\n{header}\n---\n" + render_index(renamed, f"{scope}, from {namespace}",
                                                 languages)


def _read_manifest(folder: Path) -> dict[str, str]:
    path = folder / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    return {str(key): str(value) for key, value in (files or {}).items()}


def _append_once(path: Path, line: str, *, marker: str) -> bool:
    """Append *line* to *path* unless a line holding *marker* is already there."""
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return False
    atomic_write_text(path, text + ("" if text.endswith("\n") else "\n") + line + "\n")
    return True


def apply_export(plan: ExportPlan, *, project: str, log: bool = True,
                 link_index: bool = True) -> list[str]:
    """Carry out *plan*; return what was done, one line each. Refuses while names clash."""
    if plan.collisions:
        raise ValueError(
            "the target already has pages named " + ", ".join(plan.collisions)
            + "; pass --prefix to rename the exported pages"
        )
    done: list[str] = []
    for key, data in sorted(plan.writes.items()):
        path = plan.path_of(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if key.endswith(".md") and not key.startswith(RAW_KEY):
            atomic_write_text(path, data.decode("utf-8"))
        else:
            path.write_bytes(data)
        done.append(f"write {key}")
    for key in sorted(plan.removes):
        plan.path_of(key).unlink(missing_ok=True)
        done.append(f"remove {key}")
    plan.folder.mkdir(parents=True, exist_ok=True)
    manifest = {
        "project": project,
        "exported": date.today().isoformat(),
        "prefix": plan.prefix or None,
        "raw_folder": str(plan.raw_root),
        "files": dict(sorted(plan.manifest.items())),
    }
    atomic_write_text(plan.folder / MANIFEST_NAME, json.dumps(manifest, indent=2) + "\n")
    link = f"[[{plan.index_name}]]"
    if link_index and _append_once(
        plan.target / INDEX_NAME, f"- {link} - pages imported from {project}", marker=link
    ):
        done.append(f"link {INDEX_NAME}")
    if log and (plan.writes or plan.removes):
        pages = sum(1 for key in plan.writes if key.endswith(".md") and not
                    key.startswith(RAW_KEY))
        line = (f"## [{date.today().isoformat()}] import | {project}: {pages} page file(s) "
                f"into {plan.namespace}/, {len(plan.removes)} removed")
        if _append_once(plan.target / LOG_NAME, "\n" + line, marker=line):
            done.append(f"log {LOG_NAME}")
    return done


def bundle(pages: Sequence[Page], *, project: str, scope: str,
           raw: Sequence[str] = ()) -> dict[str, Any]:
    """Return the wiki as one JSON document, for an API push (n8n, a wiki's REST API)."""
    return {
        "format": "kdx-wiki/1",
        "project": project,
        "scope": scope,
        "exported": date.today().isoformat(),
        "pages": [page.as_dict(body=True) for page in pages],
        "raw": list(raw),
    }

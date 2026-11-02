"""Reset development data: list what is disposable, refuse anything durable, then delete.

A project splits its state into a durable side (ledgers, receipts, users, the audit log) that
is deliberately hard to delete, and a disposable side (caches, indexes, scratch databases)
that may be rebuilt at any time. Developers who hand-delete folders break invariants; the
ones who never clean test against stale state. :func:`plan_reset` reads the declared roots
and builds a plan without touching a file, and :func:`apply_reset` carries out exactly that
plan. Nothing here guesses: a root that overlaps a durable root, the project root, or the
runtime state directory is refused, never trimmed to fit.
"""

from __future__ import annotations

import os
import stat as stat_module
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

DEFAULT_KEEP: Final[tuple[str, ...]] = (".gitkeep",)
_REPARSE_POINT: Final[int] = 0x400


class ResetError(ValueError):
    """A declared reset root is unsafe; the message names the root and the reason."""


@dataclass(frozen=True, slots=True)
class ResetEntry:
    """One file or directory a reset would remove, relative to the project root."""

    path: str
    kind: str
    size: int

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind, "size": self.size}


@dataclass(frozen=True, slots=True)
class ResetPlan:
    """What a reset would do: the roots it covers, what it removes, what it keeps."""

    roots: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    entries: tuple[ResetEntry, ...] = ()
    kept: tuple[str, ...] = ()
    durable: tuple[str, ...] = ()

    @property
    def total_bytes(self) -> int:
        """Return the bytes the plan would free."""
        return sum(entry.size for entry in self.entries)

    @property
    def empty(self) -> bool:
        """Return True when there is nothing to remove."""
        return not self.entries

    def as_dict(self) -> dict[str, Any]:
        return {
            "roots": list(self.roots),
            "absent": list(self.absent),
            "durable": list(self.durable),
            "kept": list(self.kept),
            "entries": [entry.as_dict() for entry in self.entries],
            "total_bytes": self.total_bytes,
        }


@dataclass(slots=True)
class ResetResult:
    """What :func:`apply_reset` removed, and what it could not."""

    removed: int = 0
    freed: int = 0
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"removed": self.removed, "freed": self.freed, "failures": list(self.failures)}


def _resolve(root: Path, value: str | os.PathLike[str]) -> Path:
    candidate = Path(value).expanduser()
    return (candidate if candidate.is_absolute() else root / candidate).resolve()


def _inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _display(project_root: Path, path: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix() or "."
    except ValueError:
        return str(path)


def _keep_names(keep: Iterable[str]) -> frozenset[str]:
    names = frozenset(str(name) for name in keep)
    for name in names:
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ResetError(f"keep entry {name!r} must be one plain file name")
    return names


def plan_reset(
    project_root: Path,
    disposable: Sequence[str | os.PathLike[str]],
    durable: Sequence[str | os.PathLike[str]],
    protected: Sequence[str | os.PathLike[str]] = (),
    keep: Iterable[str] = DEFAULT_KEEP,
) -> ResetPlan:
    """Return what a reset of the *disposable* roots would remove; touch nothing.

    Every path is resolved under *project_root* (symlinks followed, so a link cannot smuggle
    a root elsewhere). A disposable root must lie strictly inside the project, must not
    overlap any *durable* root in either direction, and must not contain or equal a
    *protected* path (the runtime state directory, the configuration files). Files named in
    *keep* (``.gitkeep`` by default) stay where they are, so layer roots survive. Symbolic
    links are removed as links and never followed.
    """
    base = project_root.resolve()
    names = _keep_names(keep)
    durable_roots = tuple(_resolve(base, item) for item in durable)
    protected_paths = tuple(_resolve(base, item) for item in protected)
    roots: list[Path] = []
    for item in disposable:
        root = _resolve(base, item)
        label = _display(base, root)
        if root == base or not _inside(root, base):
            raise ResetError(f"disposable root {label} must lie inside the project, not be it")
        for guarded in durable_roots:
            if _inside(root, guarded) or _inside(guarded, root):
                raise ResetError(
                    f"disposable root {label} overlaps the durable root "
                    f"{_display(base, guarded)}; durable data is never reset"
                )
        for guarded in protected_paths:
            if _inside(guarded, root):
                raise ResetError(
                    f"disposable root {label} contains {_display(base, guarded)}, "
                    "which the running chassis needs"
                )
        if root not in roots:
            roots.append(root)
    entries: list[ResetEntry] = []
    kept: list[str] = []
    absent: list[str] = []
    for root in roots:
        if not root.is_dir():
            absent.append(_display(base, root))
            continue
        files, links, directories = _scan(root)
        kept_files = [path for path in files if path.name in names]
        for path in files:
            if path.name in names:
                kept.append(_display(base, path))
            else:
                entries.append(_entry(base, path, "file"))
        for path in links:
            entries.append(_entry(base, path, "link"))
        for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            if any(path in held.parents for held in kept_files):
                continue
            entries.append(ResetEntry(path=_display(base, path), kind="dir", size=0))
    return ResetPlan(
        roots=tuple(_display(base, root) for root in roots),
        absent=tuple(absent),
        entries=tuple(entries),
        kept=tuple(kept),
        durable=tuple(_display(base, item) for item in durable_roots),
    )


def _is_link(path: Path) -> bool:
    """Return True for a symbolic link or a Windows junction; neither is ever followed."""
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & _REPARSE_POINT)


def _scan(root: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """Return (files, links, directories) under *root*, never descending into a link."""
    files: list[Path] = []
    links: list[Path] = []
    directories: list[Path] = []
    pending = [root]
    while pending:
        here = pending.pop()
        with os.scandir(here) as scan:
            children = sorted(scan, key=lambda child: child.name)
        for child in children:
            path = Path(child.path)
            if _is_link(path):
                links.append(path)
            elif child.is_dir(follow_symlinks=False):
                directories.append(path)
                pending.append(path)
            else:
                files.append(path)
    return files, links, directories


def _entry(base: Path, path: Path, kind: str) -> ResetEntry:
    try:
        size = 0 if kind == "link" else path.lstat().st_size
    except OSError:
        size = 0
    return ResetEntry(path=_display(base, path), kind=kind, size=size)


def apply_reset(project_root: Path, plan: ResetPlan) -> ResetResult:
    """Carry out *plan*: remove its files and links, then its now-empty directories.

    Only paths the plan names are touched, and each is checked again against the plan's
    roots, so a plan read from somewhere else cannot reach beyond them. A path that is
    already gone counts as removed; one that cannot be removed is reported and the rest of
    the plan still runs.
    """
    base = project_root.resolve()
    roots = tuple(_resolve(base, item) for item in plan.roots)
    result = ResetResult()
    for entry in plan.entries:
        target = base / entry.path
        probe = target.parent.resolve() / target.name
        if not any(_inside(probe, root) and probe != root for root in roots):
            result.failures.append(f"{entry.path}: outside the planned roots")
            continue
        try:
            if entry.kind == "dir":
                target.rmdir()
            else:
                _unlink(target)
        except FileNotFoundError:
            # Gone before we got to it: nothing was removed here and nothing freed.
            continue
        except OSError as exc:
            result.failures.append(f"{entry.path}: {exc.strerror or exc}")
            continue
        result.removed += 1
        result.freed += entry.size
    return result


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        if _is_link(path) and path.is_dir():
            # Windows removes a directory link with rmdir; the target is never touched.
            os.rmdir(path)
            return
        # A read-only file refuses deletion on Windows; clear the flag once and retry.
        os.chmod(path, stat_module.S_IWRITE | stat_module.S_IREAD)
        path.unlink()


__all__ = [
    "DEFAULT_KEEP",
    "ResetEntry",
    "ResetError",
    "ResetPlan",
    "ResetResult",
    "apply_reset",
    "plan_reset",
]

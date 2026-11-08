"""Which files `kdx wiki ingest` may copy into the wiki when the call comes over the control plane.

A terminal user reads their own files, so a local call is not limited. A call from the control
plane (RPC, MCP, WebSocket) runs as the daemon, so it must stay inside the project and away
from the daemon's own secrets: the wiki under `docs/wiki/` is committed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...paths import AppPaths

_SECRET_PREFIXES = ("kdx.local.", "korodexios.local.", "users.json", "audit.jsonl")


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _is_secret(path: Path, paths: AppPaths, root: Path) -> bool:
    protected = (paths.state_dir, paths.durable_dir / "db")
    if any(_inside(path, folder.resolve()) for folder in protected):
        return True
    parts = path.relative_to(root).parts
    if any(part.startswith(".") for part in parts):
        return True
    return path.name.startswith(_SECRET_PREFIXES) or any(
        part.startswith(_SECRET_PREFIXES) for part in parts
    )


def ingest_source(request: Any, root: Path, paths: AppPaths) -> Path | None:
    """Return the file to ingest, or None when it is missing or not allowed for this caller.

    A local call gets the path as typed. A control-plane call reads a relative path against
    the project root and may only name a file inside the project that is not runtime state,
    a dotfile or a local secret. "Missing" and "not allowed" look the same to the caller.
    """
    source = Path(str(request.option("file") or ""))
    if request.caller is None:
        return source
    project = root.resolve()
    resolved = (source if source.is_absolute() else project / source).resolve()
    if not _inside(resolved, project) or _is_secret(resolved, paths, project):
        return None
    return resolved

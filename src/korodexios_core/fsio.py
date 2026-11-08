"""Durable file writes: atomic, LF-only, optionally owner-only.

``Path.write_text`` translates ``\\n`` to ``\\r\\n`` on Windows and truncates the target in
place. A record written that way hashes differently on two platforms, and a reader that
arrives mid-write sees an empty file. These helpers write the bytes they are given to a
sibling file, flush them to disk, and rename the sibling over the target in one step.
"""

from __future__ import annotations

import contextlib
import os
import stat
import threading
import time
from pathlib import Path

from .paths import restrict_to_owner

REPLACE_RETRY_SECONDS = 1.0


def _replace(source: Path, target: Path) -> None:
    """Rename *source* over *target*, waiting out a reader on Windows.

    Windows refuses to replace a file another process has open (a status probe, an indexer,
    an antivirus scan) with a sharing violation. That clears in milliseconds, so it is
    retried for about a second before the error is raised.
    """
    deadline = time.monotonic() + REPLACE_RETRY_SECONDS
    while True:
        try:
            os.replace(str(source), str(target))
            return
        except PermissionError as exc:
            sharing = getattr(exc, "winerror", None) in (5, 32)
            if not sharing or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def _existing_mode(target: Path) -> int | None:
    """Return the permission bits of an existing *target* on POSIX, else None."""
    if os.name == "nt":
        return None
    try:
        return stat.S_IMODE(target.stat().st_mode)
    except OSError:
        return None


def atomic_write_bytes(
    path: str | os.PathLike[str], data: bytes, *, private: bool = False, fsync: bool = True
) -> Path:
    """Replace *path* with *data* in one filesystem step; return the path.

    The parent directory is created. With *private* the sibling is created empty and made
    owner-only BEFORE the secret is written to it, so the bytes are never readable by
    anyone else, not even for the moment the ACL tool takes on Windows; where owner-only
    cannot be had on POSIX the write is refused. Without *private* the mode of an existing
    target is kept (a hand-locked file stays locked). With *fsync* the data reaches the disk
    before the rename, so a crash leaves the old file or the new one, never a torn mixture.
    On failure the sibling is removed and the target is left as it was.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f"{target.name}.tmp.{os.getpid()}.{threading.get_ident()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()  # a sibling a crash left behind: start from a file we created
        kept = None if private else _existing_mode(target)
        descriptor = os.open(str(temporary), flags, 0o600 if private else (kept or 0o644))
        with os.fdopen(descriptor, "wb") as handle:
            if private and not restrict_to_owner(temporary) and os.name != "nt":
                raise PermissionError(f"cannot make {temporary} owner-only")
            if kept is not None:
                os.chmod(temporary, kept)
            handle.write(data)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        _replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise
    return target


def atomic_write_text(
    path: str | os.PathLike[str], text: str, *, private: bool = False, fsync: bool = True
) -> Path:
    """Write *text* as UTF-8 with its newlines exactly as given (LF stays LF), atomically.

    Nothing is translated: a ``\\n`` in *text* is one byte on every platform, which is what
    makes a hash of the file the same on every machine.
    """
    return atomic_write_bytes(path, text.encode("utf-8"), private=private, fsync=fsync)


__all__ = ["atomic_write_bytes", "atomic_write_text"]

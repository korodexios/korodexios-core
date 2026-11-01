"""Durable file writes: atomic, LF-only, optionally owner-only.

``Path.write_text`` translates ``\\n`` to ``\\r\\n`` on Windows and truncates the target in
place. A record written that way hashes differently on two platforms, and a reader that
arrives mid-write sees an empty file. These helpers write the bytes they are given to a
sibling file, flush them to disk, and rename the sibling over the target in one step.
"""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

from .paths import restrict_to_owner


def atomic_write_bytes(
    path: str | os.PathLike[str], data: bytes, *, private: bool = False, fsync: bool = True
) -> Path:
    """Replace *path* with *data* in one filesystem step; return the path.

    The parent directory is created. With *private* the sibling is owner-only before the
    rename, so a secret is never readable at the final path by anyone else. With *fsync*
    the data reaches the disk before the rename, so a crash leaves the old file or the new
    one, never a torn mixture. On failure the sibling is removed and the target is left as
    it was.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f"{target.name}.tmp.{os.getpid()}.{threading.get_ident()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(str(temporary), flags, 0o600 if private else 0o644)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            if fsync:
                os.fsync(handle.fileno())
        if private:
            restrict_to_owner(temporary)
        os.replace(str(temporary), str(target))
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

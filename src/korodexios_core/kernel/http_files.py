"""What the control plane needs to hand a file over HTTP: its type, a byte range, a frame rule.

Infrastructure only, stdlib only. ``ipc`` imports these names, so a handler that serves a
file keeps one place to look; nothing here opens a socket.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

CONTENT_OCTET_STREAM = "application/octet-stream"
UNSATISFIABLE = "unsatisfiable"

EMBEDDABLE_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Frame-Options", "SAMEORIGIN"),
    ("Content-Security-Policy", "frame-ancestors 'self'"),
)
"""Sent instead of the default DENY by a mount that opted in to being framed (the shell's
embed panel). Only the same origin may frame it; every other mount keeps DENY."""

_EXTRA_CONTENT_TYPES: dict[str, str] = {
    ".css": "text/css",
    ".csv": "text/csv",
    ".htm": "text/html",
    ".html": "text/html",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".wasm": "application/wasm",
}


def guess_content_type(name: str) -> str:
    """Return the media type for *name*, defaulting to octet-stream.

    The small table settles the extensions mimetypes answers differently per
    platform - '.js' is the usual offender, and on Windows '.csv' comes back as
    'application/vnd.ms-excel' - so a dashboard gets the same type on every host,
    and a static build keeps caching it.
    """
    suffix = Path(str(name)).suffix.lower()
    if suffix in _EXTRA_CONTENT_TYPES:
        return _EXTRA_CONTENT_TYPES[suffix]
    guessed, _encoding = mimetypes.guess_type(str(name))
    return guessed or CONTENT_OCTET_STREAM


def parse_byte_range(header: str | None, size: int) -> tuple[int, int] | str | None:
    """Read one HTTP 'Range: bytes=...' header against a body of *size* bytes.

    Returns the inclusive (first, last) window to send, ``None`` when the whole body is
    due (no header, another unit, several ranges, or a malformed value - all of which
    the standard says a server may ignore), and ``UNSATISFIABLE`` when the range starts
    past the end, which is answered 416.
    """
    text = str(header or "").strip().lower()
    if not text.startswith("bytes=") or "," in text:
        return None
    first, _, last = text[6:].strip().partition("-")
    try:
        if not first:
            count = int(last)
            if count <= 0 or not size:
                return UNSATISFIABLE
            return (max(size - count, 0), size - 1)
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError:
        return None
    if start < 0 or (last and end < start):
        return None
    if start >= size:
        return UNSATISFIABLE
    return (start, min(end, size - 1))


def read_window(target: Path, window: tuple[int, int] | None) -> bytes:
    """Return the file, or only the inclusive *window* of it (the rest is never read)."""
    if window is None:
        return target.read_bytes()
    with target.open("rb") as handle:
        handle.seek(window[0])
        return handle.read(window[1] - window[0] + 1)


def range_headers(window: tuple[int, int] | str | None, size: int) -> tuple[tuple[str, str], ...]:
    """Return the headers that go with an answer to a range request over *size* bytes."""
    if isinstance(window, str):
        return (("Content-Range", f"bytes */{size}"),)
    if window is None:
        return ()
    return (("Content-Range", f"bytes {window[0]}-{window[1]}/{size}"),)

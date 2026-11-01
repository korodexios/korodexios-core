"""Read JSON that somebody else wrote without ever raising anything but ValueError.

``json.loads`` raises ``RecursionError`` on deeply nested input, and the idiom
``except (OSError, ValueError)`` does not catch it: a 200 kB file of ``[[[[...`` escapes
as a traceback. Every place that parses a file, a request body, or a stream the process
did not write goes through :func:`loads` or :func:`load_path`, so malformed, oversized,
and too-deep input all arrive as the same ``ValueError`` the callers already handle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonLimitError(ValueError):
    """The JSON text is larger or more deeply nested than this process will parse."""


def loads(text: str | bytes | bytearray, *, max_bytes: int | None = None) -> Any:
    """Parse *text*; raise ``ValueError`` (never ``RecursionError``) for bad input.

    *max_bytes* bounds the encoded size before any parsing starts. A text over the bound
    raises :class:`JsonLimitError`, a subclass of ``ValueError``. ``UnicodeDecodeError``
    for undecodable bytes is a ``ValueError`` already.
    """
    if max_bytes is not None:
        size = len(text.encode("utf-8")) if isinstance(text, str) else len(text)
        if size > max_bytes:
            raise JsonLimitError(f"JSON input is {size} bytes, over the {max_bytes} byte limit")
    try:
        return json.loads(text)
    except RecursionError as exc:
        raise JsonLimitError("JSON input is nested too deeply to parse") from exc


def load_path(path: str | Path, *, max_bytes: int | None = None) -> Any:
    """Read and parse the JSON file at *path*.

    ``OSError`` from the read propagates; everything wrong with the content is a
    ``ValueError``. With *max_bytes* only that many bytes plus one are read from disk, so
    a huge file is refused without being loaded.
    """
    with Path(path).open("rb") as handle:
        raw = handle.read() if max_bytes is None else handle.read(max_bytes + 1)
    return loads(raw, max_bytes=max_bytes)

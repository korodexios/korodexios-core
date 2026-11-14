"""Typed table cells and live refresh: two small parts of a ``[ui]`` panel declaration.

A ``table`` column may carry a ``kind`` the renderer draws in its own way (``chip`` with
``tones``, ``bytes``, ``duration``, ``time``, ``link``, ``code``, ``media``). ``media`` holds a
path under a static mount of this daemon (audio, video, an image, a PDF): the renderer plays or
shows it, loading the path itself with the session cookie and a blob with a header token, and
never follows another host.

Any panel may also say ``refresh_on = ["queue.", "player.track"]``: bus event name prefixes
(``"*"`` is every event) that make a renderer load it again when such an event arrives on
``/api/v1/ui/events``. It works beside ``refresh`` (a fixed interval) and needs no polling.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

CELL_KINDS = ("text", "chip", "bytes", "duration", "time", "link", "code", "media")
TONES = ("ok", "warn", "bad", "busy", "muted")
MAX_TONES = 32
MAX_TOPICS = 8
_PATH = re.compile(r"^[A-Za-z0-9_.\-]{0,128}$")
_TOPIC = re.compile(r"^(\*|[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63})$")


def tones(value: Any, build: Any, ident: str) -> dict[str, str]:
    """Return the ``{word: tone}`` map of a chip column; an unknown tone is reported."""
    if not isinstance(value, Mapping):
        return {}
    found: dict[str, str] = {}
    for word, tone in list(value.items())[:MAX_TONES]:
        if str(tone) in TONES:
            found[str(word)] = str(tone)
        else:
            build.issue(f"tone {tone!s} for {word!s} is not one of {', '.join(TONES)}", ident)
    return found


def column(item: Any, build: Any, ident: str) -> dict[str, Any] | None:
    """Return one table column (``key``, ``label``, optional ``kind``/``tones``) or None."""
    if isinstance(item, Mapping):
        key, label = str(item.get("key") or ""), build.say(item.get("label") or item.get("key"))
        kind = str(item.get("kind") or "text")
    else:
        key, label, kind = str(item), build.say(item), "text"
    if not key or not _PATH.match(key):
        return None
    if kind not in CELL_KINDS:
        build.issue(f"column {key!r}: kind {kind!r} is not one of {', '.join(CELL_KINDS)}", ident)
        kind = "text"
    result: dict[str, Any] = {"key": key, "label": label}
    if kind != "text":
        result["kind"] = kind
    if kind == "chip" and isinstance(item, Mapping):
        chosen = tones(item.get("tones"), build, ident)
        if chosen:
            result["tones"] = chosen
    return result


def refresh_on(raw: Any, build: Any, ident: str) -> list[str]:
    """Return the event name prefixes of ``refresh_on`` (unique, at most MAX_TOPICS)."""
    if raw is None:
        return []
    if isinstance(raw, str) or not isinstance(raw, list | tuple):
        build.issue("refresh_on must be a list of event name prefixes, e.g. [\"queue.\"]", ident)
        return []
    topics: list[str] = []
    for item in raw:
        text = str(item).strip() if isinstance(item, str) else ""
        if not _TOPIC.match(text):
            build.issue(f"refresh_on: {item!r} is not an event name prefix", ident)
        elif text not in topics:
            topics.append(text)
    if len(topics) > MAX_TOPICS:
        build.issue(f"refresh_on keeps the first {MAX_TOPICS} prefixes", ident)
    return topics[:MAX_TOPICS]

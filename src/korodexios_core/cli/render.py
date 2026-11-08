"""Plain-text rendering helpers. The rich wizard layer reuses these primitives."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from typing import Any

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, tags_for

_TAGS = frozenset({TAG_ERROR, TAG_NOTE, TAG_OK, "[?]", "[!]", "[i]"})


_translator: Callable[..., str] | None = None


def set_translator(translator: Callable[..., str] | None) -> None:
    """Install (or, with None, remove) the function that translates fixed UI text.

    Only text the chassis wrote itself passes through it: section titles, key labels,
    table headers, the fixed words yes/no, and whole static messages. Values, JSON, and
    anything dynamic are never touched, so a machine reading stdout sees the same bytes
    in every language.
    """
    global _translator
    _translator = translator


def plain(message: str, /, **fields: Any) -> str:
    """Return *message* filled with *fields*, never translated.

    For what goes to stderr, logs and machine payloads: those stay English in every language,
    so a CI job can grep them (the stderr rule of the project).
    """
    if not fields:
        return message
    return message.format(**{key: str(value) for key, value in fields.items()})


def tr(message: str, /, **fields: Any) -> str:
    """Return *message* translated and filled with *fields*; the source text when untranslated.

    Use it for a sentence with values in it: the sentence is the translatable unit and
    the values stay out of the catalogue ('{count} string(s) left for {locale}').
    """
    if _translator is not None:
        return _translator(message, **fields)
    if not fields:
        return message
    return message.format(**{key: str(value) for key, value in fields.items()})


def _tr(text: str) -> str:
    return text if _translator is None or not text else _translator(text)


def _tr_message(message: str) -> str:
    """Translate a whole static message that may start with spaces and one status tag."""
    if _translator is None or not message:
        return message
    body = message.lstrip(" ")
    lead = message[: len(message) - len(body)]
    tag, _, rest = body.partition(" ")
    if rest and tag in _TAGS:
        return f"{lead}{tag} {_translator(rest)}"
    return f"{lead}{_translator(body)}"


def _ascii_tags(message: str, stream: Any) -> str:
    """Replace status tags the target stream cannot encode with their ASCII aliases."""
    aliases = tags_for(getattr(stream, "encoding", None))
    if not aliases:
        return message
    for tag, alias in aliases.items():
        message = message.replace(tag, alias)
    return message


def write_line(message: str = "", *, stream: Any = None, raw: bool = False) -> None:
    """Write one line to stdout (the UI channel) unless *stream* says otherwise.

    A static message is translated on stdout. Pass ``raw=True`` for data that only passes
    through (a daemon's output, a log line, a document, a diff): ``running`` stays ``running``
    in every language, so a script can grep it.
    """
    target = sys.stdout if stream is None else stream
    shown = message if raw or target is not sys.stdout else _tr_message(message)
    print(_ascii_tags(shown, target), file=target)


def write_status(message: str, *, tag: str = TAG_OK, stream: Any = None) -> None:
    """Write a tagged status line."""
    write_line(f"{tag} {message}", stream=stream)


def render_key_values(
    pairs: Sequence[tuple[str, Any]],
    *,
    indent: int = 2,
    key_width: int = 26,
) -> list[str]:
    """Render aligned 'key : value' lines."""
    pad = " " * indent
    lines: list[str] = []
    for key, value in pairs:
        rendered = _render(value)
        lines.append(f"{pad}{_tr(str(key)).ljust(key_width)} {rendered}")
    return lines


def print_key_values(
    pairs: Sequence[tuple[str, Any]],
    *,
    indent: int = 2,
    key_width: int = 26,
    stream: Any = None,
) -> None:
    """Print aligned 'key : value' lines."""
    for line in render_key_values(pairs, indent=indent, key_width=key_width):
        write_line(line, stream=stream)


def render_table(
    rows: Sequence[Sequence[Any]],
    headers: Sequence[str] = (),
    *,
    indent: int = 2,
) -> list[str]:
    """Render a fixed-width text table from stringifiable cells."""
    grid: list[list[str]] = [[_render(cell) for cell in row] for row in rows]
    if headers:
        grid.insert(0, [_tr(str(header)) for header in headers])
    if not grid:
        return []
    columns = max(len(row) for row in grid)
    grid = [row + [""] * (columns - len(row)) for row in grid]
    widths = [max(len(row[index]) for row in grid) for index in range(columns)]
    pad = " " * indent
    lines = [
        pad + "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip()
        for row in grid
    ]
    if headers:
        rule = pad + "  ".join("-" * width for width in widths)
        lines.insert(1, rule.rstrip())
    return lines


def print_table(
    rows: Sequence[Sequence[Any]],
    headers: Sequence[str] = (),
    *,
    indent: int = 2,
    stream: Any = None,
) -> None:
    """Print a fixed-width text table."""
    for line in render_table(rows, headers, indent=indent):
        write_line(line, stream=stream)


def render_section(title: str, *, indent: int = 0) -> str:
    """Render an underlined section heading."""
    pad = " " * indent
    shown = _tr(title)
    return f"{pad}{shown}\n{pad}{'-' * max(4, len(shown))}"


def print_section(title: str, *, indent: int = 0, stream: Any = None) -> None:
    """Print an underlined section heading."""
    for line in render_section(title, indent=indent).splitlines():
        write_line(line, stream=stream)


def dump_json(payload: Any, *, stream: Any = None) -> None:
    """Print *payload* as pretty JSON on the UI channel."""
    write_line(json.dumps(payload, indent=2, sort_keys=True, default=str), stream=stream)


def format_bool(value: bool) -> str:
    """Render a boolean as a friendly yes/no."""
    return _tr("yes" if value else "no")


def format_ms(value: float | None) -> str:
    """Render a millisecond duration, or a dash when unknown."""
    if value is None:
        return "-"
    return f"{value:.1f} ms"


def _render(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return _tr("yes" if value else "no")
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(_render(item) for item in value) or _tr("(empty)")
    if isinstance(value, dict):
        return ", ".join(f"{key}={_render(item)}" for key, item in value.items()) or _tr("(empty)")
    return str(value)


__all__ = [
    "TAG_ERROR",
    "TAG_NOTE",
    "TAG_OK",
    "dump_json",
    "format_bool",
    "format_ms",
    "print_key_values",
    "print_section",
    "print_table",
    "render_key_values",
    "render_section",
    "render_table",
    "set_translator",
    "tags_for",
    "write_line",
    "write_status",
]

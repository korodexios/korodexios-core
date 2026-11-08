"""Reading gettext source (``.po`` / ``.pot``) without a compile step.

A small state machine: each ``"..."`` continuation line belongs to the keyword before it, so a
plural form or a context can never leak into the translation of the entry above it. The chassis
translates plain messages only; an entry with a ``msgctxt`` or a ``msgid_plural`` is read and
then set aside, so it neither replaces a plain entry of the same text nor corrupts it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

BOM: Final = chr(0xFEFF)
"""The byte-order mark some editors put in front of a UTF-8 file."""
_ESCAPES: Final[dict[str, str]] = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
_KEYWORDS: Final[tuple[tuple[str, str], ...]] = (
    ("msgctxt", "ctx"),
    ("msgid_plural", "plural"),
    ("msgid", "id"),
    ("msgstr[", "plural-str"),
    ("msgstr", "str"),
)


def unquote(raw: str) -> str:
    """Return the text inside one quoted gettext string, escapes resolved; '' if unquoted."""
    body = raw.strip()
    if len(body) < 2 or body[0] != '"' or body[-1] != '"':
        return ""
    text, out, index = body[1:-1], [], 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            out.append(_ESCAPES.get(text[index + 1], text[index + 1]))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


@dataclass(frozen=True, slots=True)
class PoEntry:
    """One entry of a gettext source file."""

    msgid: str
    msgstr: str
    fuzzy: bool = False
    line: int = 0
    where: str = ""

    @property
    def translated(self) -> bool:
        """Return True when the entry carries a usable translation."""
        return bool(self.msgstr) and not self.fuzzy


def _keyword(line: str) -> tuple[str, str] | None:
    """Return (field, quoted text) when *line* starts a gettext keyword, else None."""
    for word, field in _KEYWORDS:
        if line.startswith(word):
            rest = line[len(word):]
            if word.endswith("["):
                rest = rest.partition("]")[2]
            elif rest[:1] not in (" ", '"', "\t", ""):
                continue
            return field, rest
    return None


class _Reader:
    """The state of one pass over a .po file: the entry being read and the comments before it."""

    def __init__(self) -> None:
        self.entries: list[PoEntry] = []
        self.fields: dict[str, str] = {}
        self.current = ""
        self.start, self.fuzzy, self.where = 0, False, ""
        self.next_fuzzy, self.next_where = False, ""

    def comment(self, line: str) -> None:
        if line.startswith("#,") and "fuzzy" in line:
            self.next_fuzzy = True
        elif line.startswith("#:"):
            self.next_where = line[2:].strip()

    def keyword(self, number: int, field: str, rest: str) -> None:
        if field in ("ctx", "id") and (field in self.fields or "str" in self.fields):
            self.flush()
        if not self.fields:
            self.start, self.fuzzy, self.where = number, self.next_fuzzy, self.next_where
            self.next_fuzzy, self.next_where = False, ""
        self.current = field
        self.fields[field] = unquote(rest)

    def flush(self) -> None:
        """Close the entry; one with a context or a plural is read and set aside."""
        fields = self.fields
        if fields.get("id") and not fields.keys() & {"ctx", "plural"}:
            entry = PoEntry(fields["id"], fields.get("str", ""), self.fuzzy, self.start, self.where)
            self.entries.append(entry)
        self.fields = {}


def parse_po(text: str) -> list[PoEntry]:
    """Parse gettext source *text* into entries; the header (empty msgid) is skipped.

    Understands comments, the ``fuzzy`` flag, ``#:`` references, strings continued over several
    lines, a leading byte-order mark and any line ending. Entries with a context or a plural
    are dropped (the chassis does not use them) without touching their neighbours.
    """
    reader = _Reader()
    for number, raw in enumerate(text.lstrip(BOM).splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            reader.comment(line)
            continue
        found = _keyword(line)
        if found is not None:
            reader.keyword(number, *found)
        elif line.startswith('"') and reader.current:
            reader.fields[reader.current] = reader.fields.get(reader.current, "") + unquote(line)
    reader.flush()
    return reader.entries

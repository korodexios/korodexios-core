"""Find the translatable strings in Python source and keep gettext files in step.

A string is translatable when it is user-facing text the chassis writes itself:

- ``translate("...")`` and ``_t(request, "...")`` calls;
- ``help="..."`` of ``CliCommand`` and ``CliArgument``;
- section titles, key labels, and table headers handed to ``print_section``,
  ``print_key_values``, and ``print_table``;
- whole static messages handed to ``write_line`` and ``write_status``, with or without a
  leading status tag.

Command and flag names, JSON, logs, and dynamic text are not found, by design: they are
never translated.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .i18n import PoEntry, parse_po

FIXED_WORDS: Final[tuple[str, ...]] = ("yes", "no", "(empty)")
"""Words the renderer prints by itself for booleans and empty collections."""

_TRANSLATE_CALLS: Final[frozenset[str]] = frozenset({"translate", "_t"})
_MESSAGE_CALLS: Final[frozenset[str]] = frozenset({"write_line", "write_status"})
_HELP_OWNERS: Final[frozenset[str]] = frozenset({"CliCommand", "CliArgument"})


@dataclass(frozen=True, slots=True)
class Found:
    """One source string and where it was first seen."""

    message: str
    where: str


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _text(node: ast.expr | None) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _static_message(node: ast.expr | None) -> str:
    """Return the text of a constant or of an f-string whose only fields are TAG_* names."""
    if isinstance(node, ast.Constant):
        return _text(node).strip()
    if not isinstance(node, ast.JoinedStr):
        return ""
    parts: list[str] = []
    for item in node.values:
        if isinstance(item, ast.Constant):
            parts.append(str(item.value))
        elif isinstance(item, ast.FormattedValue) and _name(item.value).startswith("TAG_"):
            continue
        else:
            return ""
    return "".join(parts).strip()


def _first_strings(call: ast.Call) -> Iterator[ast.expr]:
    name = _name(call.func)
    args = call.args
    if name in _TRANSLATE_CALLS:
        yield from (args[1:2] if name == "_t" else args[:1])
    elif name == "print_section" or name in _MESSAGE_CALLS:
        yield from args[:1]
    elif name == "print_key_values":
        for arg in args[:1]:
            for node in ast.walk(arg):
                if isinstance(node, ast.Tuple) and len(node.elts) == 2:
                    yield node.elts[0]
    elif name == "print_table":
        for keyword in call.keywords:
            if keyword.arg == "headers" and isinstance(keyword.value, (ast.Tuple, ast.List)):
                yield from keyword.value.elts
    if name in _HELP_OWNERS:
        for keyword in call.keywords:
            if keyword.arg == "help":
                yield keyword.value


def scan_source(source: str, label: str) -> list[Found]:
    """Return the translatable strings of one module's *source*, in order of appearance."""
    tree = ast.parse(source)
    found: list[Found] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _name(node.func)
        for expr in _first_strings(node):
            if name in _MESSAGE_CALLS or isinstance(expr, ast.JoinedStr):
                message = _static_message(expr)
            else:
                message = _text(expr)
            if message and not message.isspace():
                found.append(Found(message, f"{label}:{getattr(expr, 'lineno', node.lineno)}"))
    found.sort(key=lambda item: (item.where.rpartition(":")[0], int(item.where.rpartition(":")[2])))
    return found


def scan_tree(root: Path, extra: Iterable[str] = FIXED_WORDS) -> dict[str, str]:
    """Return {message: first place seen} for every ``.py`` file below *root*."""
    messages: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        label = path.relative_to(root).as_posix()
        try:
            found = scan_source(path.read_text(encoding="utf-8"), label)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for item in found:
            messages.setdefault(item.message, item.where)
    for word in extra:
        messages.setdefault(word, "renderer")
    return messages


def _quote(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def render_pot(domain: str, messages: dict[str, str]) -> str:
    """Return the template text for *messages*, sorted so a diff stays small."""
    lines = [
        f"# Translatable strings of the {domain} domain.",
        f"# Copy to <locale>/{domain}.po and fill in the msgstr lines.",
        'msgid ""',
        'msgstr ""',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        "",
    ]
    for message in sorted(messages):
        lines += [f"#: {messages[message]}", f"msgid {_quote(message)}", 'msgstr ""', ""]
    return "\n".join(lines)


def merge_po(existing: str, messages: dict[str, str]) -> tuple[str, int]:
    """Append the *messages* a translation lacks, with an empty msgstr; return (text, added)."""
    known = {entry.msgid for entry in parse_po(existing)}
    missing = [message for message in sorted(messages) if message not in known]
    if not missing:
        return existing, 0
    lines = [existing.rstrip("\n"), ""]
    for message in missing:
        lines += [f"#: {messages[message]}", f"msgid {_quote(message)}", 'msgstr ""', ""]
    return "\n".join(lines), len(missing)


def stale_entries(po_text: str, messages: dict[str, str]) -> list[PoEntry]:
    """Return the entries of a translation whose source string no longer exists."""
    return [entry for entry in parse_po(po_text) if entry.msgid not in messages]

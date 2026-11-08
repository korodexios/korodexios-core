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

from .i18n import PoEntry, parse_po, placeholders

FIXED_WORDS: Final[tuple[str, ...]] = ("yes", "no", "(empty)")
"""Words the renderer prints by itself for booleans and empty collections."""

_TRANSLATE_CALLS: Final[frozenset[str]] = frozenset({"translate", "_t", "tr"})
_MESSAGE_CALLS: Final[frozenset[str]] = frozenset({"write_line", "write_status"})
_HELP_OWNERS: Final[frozenset[str]] = frozenset({"CliCommand", "CliArgument"})
_UX_CALLS: Final[frozenset[str]] = frozenset({"status", "note", "error", "heading", "confirm"})
"""Methods of the interactive layer (`ux.note("...")`) whose first argument is user text."""


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
        for arg in args[1:2]:  # headers given positionally
            if isinstance(arg, (ast.Tuple, ast.List)):
                yield from arg.elts
    elif name == "add_argument":
        for keyword in call.keywords:
            if keyword.arg == "help":
                yield keyword.value
    elif name in _UX_CALLS and isinstance(call.func, ast.Attribute) and (
        _name(call.func.value) in ("ux", "_ux")
    ):
        yield from args[:1]
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
            if name in _MESSAGE_CALLS or name in _UX_CALLS or isinstance(expr, ast.JoinedStr):
                message = _static_message(expr)
            else:
                message = _text(expr)
            if message and not message.isspace():
                found.append(Found(message, f"{label}:{getattr(expr, 'lineno', node.lineno)}"))
    found.sort(key=lambda item: (item.where.rpartition(":")[0], int(item.where.rpartition(":")[2])))
    return found


def scan_tree(root: Path, extra: Iterable[str] = FIXED_WORDS) -> dict[str, str]:
    """Return {message: first file seen in} for every ``.py`` file below *root*."""
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
            messages.setdefault(item.message, label)  # file only: a moved line is no diff
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


@dataclass(frozen=True, slots=True)
class TodoItem:
    """One string a translator still has to handle."""

    msgid: str
    where: str
    placeholders: tuple[str, ...]
    current: str
    fuzzy: bool

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly view."""
        return {
            "msgid": self.msgid,
            "where": self.where,
            "placeholders": list(self.placeholders),
            "current": self.current,
            "fuzzy": self.fuzzy,
        }


def new_po(domain: str, locale: str, template: str) -> str:
    """Return an empty translation of *domain* for *locale*, built from the *template*."""
    lines = template.splitlines()
    header = '"Content-Type: text/plain; charset=UTF-8\n"'
    out: list[str] = []
    for line in lines:
        if line.startswith("# Translatable strings"):
            line = f"# Translation of the {domain} domain into {locale}."
        elif line.startswith("# Copy to"):
            continue
        elif line == header:
            out.append(f'"Language: {locale}\n"')
        out.append(line)
    return "\n".join(out).rstrip("\n") + "\n"


def todo_items(template: str, translation: str) -> list[TodoItem]:
    """Return the template strings *translation* has not translated (or left fuzzy)."""
    have = {entry.msgid: entry for entry in parse_po(translation)}
    items: list[TodoItem] = []
    for entry in parse_po(template):
        done = have.get(entry.msgid)
        if done is not None and done.translated:
            continue
        items.append(
            TodoItem(
                msgid=entry.msgid,
                where=entry.where,
                placeholders=tuple(sorted(placeholders(entry.msgid))),
                current=done.msgstr if done is not None else "",
                fuzzy=bool(done and done.fuzzy),
            )
        )
    return items


def compose_po(
    domain: str,
    locale: str,
    template: str,
    translation: str,
    updates: dict[str, str],
) -> tuple[str, dict[str, str], dict[str, str]]:
    """Return (new text, applied, rejected-with-reason) after merging *updates*.

    The file is rebuilt in template order with ``msgmerge`` semantics: a string that is
    still in the template keeps its translation and fuzzy flag; a translated string that
    left the template is kept as an obsolete ``#~`` entry (``kdx locale tidy`` prunes
    them, and the string coming back revives its translation); readers and
    ``kdx locale check`` ignore obsolete entries. An update is rejected when its string
    is not in the template, is empty, or names different ``{fields}`` than its source.
    """
    wanted = {entry.msgid: entry for entry in parse_po(template)}
    have = {entry.msgid: entry for entry in parse_po(translation)}
    old = parse_obsolete(translation)
    for gone, earlier in old.items():
        if gone in wanted and gone not in have:
            have[gone] = PoEntry(gone, earlier, True)  # revived: confirm it, keep it fuzzy
    applied: dict[str, str] = {}
    rejected: dict[str, str] = {}
    for msgid, msgstr in updates.items():
        if msgid not in wanted:
            rejected[msgid] = "not in the template (run kdx locale extract)"
        elif not str(msgstr).strip():
            rejected[msgid] = "empty translation"
        elif placeholders(str(msgstr)) != placeholders(msgid):
            rejected[msgid] = "placeholders differ from the source"
        else:
            applied[msgid] = str(msgstr)
    lines = [
        f"# Translation of the {domain} domain into {locale}.",
        'msgid ""',
        'msgstr ""',
        f'"Language: {locale}\n"',
        '"Content-Type: text/plain; charset=UTF-8\n"',
        "",
    ]
    for msgid, entry in wanted.items():
        previous = have.get(msgid)
        text = applied.get(msgid)
        if text is None:
            text = previous.msgstr if previous is not None else ""
        stale = msgid not in applied and previous is not None and previous.fuzzy
        flag = ["#, fuzzy"] if stale else []
        lines += [
            f"#: {entry.where}", *flag,
            f"msgid {_quote(msgid)}", f"msgstr {_quote(text)}", "",
        ]
    kept = {**old, **{k: e.msgstr for k, e in have.items() if k not in wanted and e.msgstr}}
    for msgid in sorted(kept):
        if msgid not in wanted and kept[msgid]:
            lines += [f"#~ msgid {_quote(msgid)}", f"#~ msgstr {_quote(kept[msgid])}", ""]
    return "\n".join(lines), applied, rejected


def parse_obsolete(text: str) -> dict[str, str]:
    """Return {msgid: msgstr} for the obsolete (``#~``) entries of gettext *text*."""
    body = "\n".join(
        line[2:].lstrip() for line in text.splitlines() if line.startswith("#~")
    )
    return {entry.msgid: entry.msgstr for entry in parse_po(body) if entry.msgstr}


def tidy_po(text: str) -> tuple[str, int]:
    """Return *text* without its obsolete entries, and how many were removed."""
    removed = len(parse_obsolete(text))
    kept = [line for line in text.splitlines() if not line.startswith("#~")]
    return "\n".join(kept).rstrip("\n") + "\n", removed

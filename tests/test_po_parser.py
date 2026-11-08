"""The .po reader keeps plurals, contexts and a BOM from corrupting plain entries (I4)."""

from __future__ import annotations

from korodexios_core.kernel.po import parse_po, unquote


def pairs(text: str) -> list[tuple[str, str]]:
    return [(entry.msgid, entry.msgstr) for entry in parse_po(text)]


def test_a_plural_entry_does_not_swallow_its_continuation_lines() -> None:
    text = (
        'msgid "one"\nmsgid_plural ""\n"many"\nmsgstr[0] ""\n"jeden"\n\n'
        'msgid "next"\nmsgstr "dalsi"\n'
    )
    assert pairs(text) == [("next", "dalsi")]


def test_a_context_never_replaces_the_plain_entry() -> None:
    text = (
        'msgid "Open"\nmsgstr "Otvorit"\n\n'
        'msgctxt "state"\nmsgid "Open"\nmsgstr "Otvoreny"\n'
    )
    assert pairs(text) == [("Open", "Otvorit")]


def test_a_multi_line_context_does_not_leak_into_the_previous_translation() -> None:
    text = 'msgid "a"\nmsgstr "A"\n\nmsgctxt ""\n"ctx"\nmsgid "b"\nmsgstr "B"\n'
    assert pairs(text) == [("a", "A")]


def test_a_byte_order_mark_keeps_the_first_entry() -> None:
    text = '\ufeffmsgid "first"\nmsgstr "PRVY"\n\nmsgid "second"\nmsgstr "DRUHY"\n'
    assert pairs(text) == [("first", "PRVY"), ("second", "DRUHY")]


def test_line_endings_and_continuations_still_work() -> None:
    assert pairs('msgid "a"\r\nmsgstr "A"\r\n\r\nmsgid "b"\r\nmsgstr "B"\r\n') == [
        ("a", "A"), ("b", "B"),
    ]
    text = 'msgid ""\n"Say \\"hi\\" "\n"to {name}"\nmsgstr ""\n"Povedz "\n"{name}"\n'
    assert pairs(text) == [('Say "hi" to {name}', "Povedz {name}")]


def test_flags_references_and_the_header_are_handled() -> None:
    text = (
        'msgid ""\nmsgstr "Language: sk\n"\n\n'
        '#: a.py:3\n#, fuzzy, python-format\nmsgid "a"\nmsgstr "A"\n'
    )
    [entry] = parse_po(text)
    assert entry.fuzzy and entry.where == "a.py:3" and not entry.translated


def test_unquote_resolves_escapes() -> None:
    assert unquote('"a\tb\\"c"') == 'a\tb"c'
    assert unquote("not quoted") == ""

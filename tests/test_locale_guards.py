"""Locale housekeeping that must not damage a catalogue (review 2026-11-08, I3 and I5)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.kernel.i18n import I18n

LOCALES = Path(__file__).resolve().parent.parent / "src" / "korodexios_core" / "locales"


def test_no_message_uses_a_domain_field() -> None:
    """translate(domain=...) selects a catalogue, so {domain} in a message can never fill."""
    template = (LOCALES / "korodexios.pot").read_text(encoding="utf-8")
    assert not re.findall(r'^msgid .*\{domain\}', template, flags=re.MULTILINE)


def test_the_extract_note_fills_its_name_field() -> None:
    service = I18n("sk")
    text = service.translate("no translation of {name} found under {path}", name="proj", path="x")
    assert "proj" in text and "{" not in text


def test_extract_refuses_a_source_without_python_strings(
    project_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The web domain's strings are in JavaScript; extracting Python would wipe its template."""
    web = LOCALES.parent / "modules" / "web_console" / "locales"
    before = {path: path.read_bytes() for path in web.rglob("*.po*")}
    empty = tmp_path / "js-only"
    empty.mkdir()
    (empty / "app.js").write_text("t('hello')"+chr(10), encoding="utf-8")
    try:
        code = main(["--home", str(project_root), "--no-log-file", "--no-color", "locale",
                     "extract", "--domain", "korodexios-web", "--source", str(empty)])
        assert code == 2
        assert "no Python strings" in capsys.readouterr().out
        assert {path: path.read_bytes() for path in web.rglob("*.po*")} == before
    finally:  # never leave the shipped catalogues damaged, whatever this test found
        for path, data in before.items():
            path.write_bytes(data)

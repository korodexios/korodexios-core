"""The interactive layer and the dynamic sentences go through the translator (review I6)."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest

from korodexios_core.cli import render
from korodexios_core.cli.entry import main
from korodexios_core.cli.ux import UserExperience
from korodexios_core.kernel.i18n import build_i18n
from korodexios_core.kernel.i18n_extract import scan_source


@pytest.fixture
def slovak() -> Iterator[None]:
    service = build_i18n("sk")
    render.set_translator(service.translate)
    try:
        yield
    finally:
        render.set_translator(None)


def make_ux(config_values: dict[str, object]) -> tuple[UserExperience, io.StringIO]:
    class Config:
        def get_bool(self, key: str, default: bool = False) -> bool:
            return bool(config_values.get(key, default))

        def get_int(self, key: str, default: int = 0) -> int:
            return int(config_values.get(key, default))  # type: ignore[call-overload]

    stream = io.StringIO()
    return UserExperience(Config(), stdout=stream), stream


def test_ux_status_and_confirm_are_translated(slovak: None) -> None:
    ux, stream = make_ux({"core.cli.interactive": False, "core.cli.color": False})
    ux.note("Run this backup now")
    assert "Spustiť túto zálohu teraz" in stream.getvalue()
    assert ux.confirm("Run this backup now", default=True) is True


def test_a_message_nobody_translated_stays_as_written(slovak: None) -> None:
    ux, stream = make_ux({"core.cli.interactive": False, "core.cli.color": False})
    ux.note("running 3 files / {braces}")
    assert "running 3 files / {braces}" in stream.getvalue()


def test_the_extractor_sees_ux_calls_and_positional_table_headers() -> None:
    source = (
        "def f(ux, rows):\n"
        "    ux.note('hello there')\n"
        "    ux.confirm('really?')\n"
        "    logger.error('a log line that must not be translated')\n"
        "    print_table(rows, ('Alpha', 'Beta'))\n"
    )
    messages = {item.message for item in scan_source(source, "x.py")}
    assert {"hello there", "really?", "Alpha", "Beta"} <= messages
    assert "a log line that must not be translated" not in messages


def test_an_unknown_config_key_sentence_is_translated(
    project_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KDX_CORE__BOGUS", "1")
    base = ["--home", str(project_root), "--no-log-file", "--no-color"]
    main([*base, "--locale", "sk", "config", "check"])
    out = capsys.readouterr().out
    assert "neznámy konfiguračný kľúč core.bogus" in out


def test_the_global_options_help_is_translated(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["--home", str(project_root), "--no-log-file", "--no-color"]
    assert main([*base, "--locale", "sk", "--help"]) == 0
    sk = capsys.readouterr().out
    assert "Koreňový adresár jadra." in sk and "--home PATH" in sk, "flags stay English"
    assert main([*base, "--help"]) == 0
    assert "Chassis root directory." in capsys.readouterr().out

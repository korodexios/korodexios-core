"""The language service: catalogue parsing, fallback, completeness checks, and 'kdx locale'."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from korodexios_core.cli.entry import main
from korodexios_core.constants import ExitCode
from korodexios_core.kernel.i18n import (
    CORE_DOMAIN,
    I18n,
    build_i18n,
    core_locales_dir,
    environment_locale,
    locale_chain,
    normalize_locale,
    parse_po,
    resolve_locale,
)

from .test_cli_v2 import base_args, write_root

POT = 'msgid ""\nmsgstr ""\n\nmsgid "hello"\nmsgstr ""\n\nmsgid "bye {name}"\nmsgstr ""\n'
PO = (
    '# comment\nmsgid ""\nmsgstr ""\n"Language: sk\\n"\n\n'
    'msgid "hello"\nmsgstr "ahoj"\n\n'
    'msgid "bye {name}"\nmsgstr "dovidenia {name}"\n'
)


def make_domain(root: Path, domain: str = "demo", *, pot: str = POT, po: str = PO) -> Path:
    locales = root / "locales"
    (locales / "sk").mkdir(parents=True)
    (locales / f"{domain}.pot").write_text(pot, encoding="utf-8")
    (locales / "sk" / f"{domain}.po").write_text(po, encoding="utf-8")
    return locales


def test_locale_names_are_normalized() -> None:
    assert normalize_locale("sk_SK.UTF-8") == "sk_SK"
    assert normalize_locale("SK-sk") == "sk_SK"
    assert normalize_locale("de@euro") == "de"
    assert normalize_locale("C") == ""
    assert normalize_locale("") == ""
    assert normalize_locale(None) == ""
    assert normalize_locale("../etc") == ""
    assert locale_chain("sk_SK") == ("sk_SK", "sk")
    assert locale_chain("sk") == ("sk",)
    assert locale_chain("nonsense!") == ()


def test_resolve_locale_reads_the_environment_only_for_auto() -> None:
    env = {"LANG": "cs_CZ.UTF-8"}
    assert resolve_locale("auto", env) == "cs_CZ"
    assert resolve_locale("AUTO", {"LC_ALL": "C", "LANG": "de_DE"}) == "de_DE"
    assert resolve_locale("auto", {"LANG": "C"}) == "en"
    assert resolve_locale("sk", env) == "sk"
    assert resolve_locale("", env) == "en"
    assert resolve_locale("???", env) == "en"
    assert resolve_locale(None, env) == "en"
    assert environment_locale({}) == "en"


def test_parse_po_handles_continuations_escapes_and_fuzzy() -> None:
    text = (
        '#, fuzzy\nmsgid "a"\nmsgstr "b"\n\n'
        'msgid ""\n"long "\n"id"\nmsgstr ""\n"line\\n"\n"two \\"q\\" \\\\ \\t"\n\n'
        'msgid "x"\nmsgstr "y"\n'
    )
    entries = {entry.msgid: entry for entry in parse_po(text)}
    assert entries["a"].fuzzy and not entries["a"].translated
    assert entries["x"].translated
    assert entries["x"].line > 0
    # the header is skipped, but a continued msgid is joined
    assert "long id" in entries
    assert entries["long id"].msgstr == 'line\ntwo "q" \\ \t'
    assert parse_po('msgid "bad"\nmsgstr bare\n')[0].msgstr == ""


def test_translate_uses_the_catalogue_and_falls_back_to_the_source(tmp_path: Path) -> None:
    service = I18n("sk_SK")
    service.register("demo", make_domain(tmp_path))
    assert service.locale == "sk_SK"
    assert service.translate("hello", domain="demo") == "ahoj"
    assert service.translate("bye {name}", domain="demo", name="Jan") == "dovidenia Jan"
    assert service.translate("unknown", domain="demo") == "unknown"
    assert service.translate("hello", domain="nowhere") == "hello"
    assert service.translate("only {x}", domain="nowhere", x=1) == "only 1"
    service.set_locale("en")
    assert service.translate("hello", domain="demo") == "hello"
    assert service.set_locale("???") == "en"
    assert service.set_locale("de") == "de"
    assert service.translate("hello", domain="demo") == "hello"


def test_a_bad_translation_never_crashes_a_command(tmp_path: Path) -> None:
    broken = PO.replace("dovidenia {name}", "dovidenia {nome}")
    service = I18n("sk")
    service.register("demo", make_domain(tmp_path, po=broken))
    assert service.translate("bye {name}", domain="demo", name="Jan") == "bye Jan"
    assert service.translate("bye {name}", domain="demo") == "bye {name}"


def test_the_classic_gettext_layout_is_found(tmp_path: Path) -> None:
    locales = tmp_path / "locales"
    (locales / "cs" / "LC_MESSAGES").mkdir(parents=True)
    (locales / "cs" / "LC_MESSAGES" / "demo.po").write_text(PO, encoding="utf-8")
    service = I18n("cs")
    service.register("demo", locales)
    assert service.translate("hello", domain="demo") == "ahoj"
    assert service.locales("demo") == ("cs", "en")
    assert service.locales("missing") == ("en",)
    with pytest.raises(ValueError):
        service.register("  ", locales)


def test_registering_again_drops_the_cached_catalogue(tmp_path: Path) -> None:
    service = I18n("sk")
    service.register("demo", tmp_path / "empty")
    assert service.translate("hello", domain="demo") == "hello"
    service.register("demo", make_domain(tmp_path))
    assert service.translate("hello", domain="demo") == "ahoj"
    assert service.domains()["demo"] == tmp_path / "locales"


def test_unreadable_catalogue_is_treated_as_empty(tmp_path: Path) -> None:
    locales = make_domain(tmp_path)
    (locales / "sk" / "demo.po").write_bytes(b"\xff\xfe\x00bad")
    service = I18n("sk")
    service.register("demo", locales)
    assert service.translate("hello", domain="demo") == "hello"


def test_check_reports_missing_fuzzy_obsolete_and_broken(tmp_path: Path) -> None:
    po = (
        'msgid "hello"\nmsgstr ""\n\n'
        '#, fuzzy\nmsgid "bye {name}"\nmsgstr "dovidenia {name}"\n\n'
        'msgid "gone"\nmsgstr "preč"\n'
    )
    service = I18n()
    service.register("demo", make_domain(tmp_path, po=po))
    report = service.check("demo")[0]
    assert (report.untranslated, report.fuzzy, report.obsolete) == (
        ("hello",), ("bye {name}",), ("gone",)
    )
    assert not report.complete and report.percent == 0
    assert report.as_dict()["complete"] is False

    broken = PO.replace("ahoj", "ahoj {x}")
    service.register("demo", make_domain(tmp_path / "b", po=broken))
    assert service.check("demo", "sk")[0].broken_fields == ("hello",)

    service.register("demo", make_domain(tmp_path / "c"))
    good = service.check("demo")[0]
    assert good.complete and good.percent == 100
    assert service.check("unknown") == []
    assert service.check("demo", "fr")[0].percent == 0


def test_the_chassis_ships_a_complete_slovak_catalogue() -> None:
    service = build_i18n("sk")
    assert CORE_DOMAIN in service.domains()
    assert service.domains()[CORE_DOMAIN] == core_locales_dir()
    assert "sk" in service.locales(CORE_DOMAIN)
    for report in service.check(CORE_DOMAIN):
        assert report.complete, report.as_dict()
    assert service.translate("every catalogue is complete") == "každý katalóg je úplný"


def run(root: Path, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = main([*base_args(root), *argv])
    return code, capsys.readouterr().out


def test_locale_show_and_list(capsys, project_root: Path) -> None:
    write_root(project_root)
    code, out = run(project_root, capsys, "locale", "show", "--json")
    payload = json.loads(out)
    assert code == 0 and payload["locale"] == "en" and "sk" in payload["available"]
    assert payload["origin"] == "built-in default"

    code, out = run(project_root, capsys, "--locale", "sk", "locale", "show")
    assert code == 0 and "jazyk" in out and "command line" in out

    code, out = run(project_root, capsys, "locale", "list")
    assert code == 0 and "korodexios" in out and "en, sk" in out
    code, out = run(project_root, capsys, "locale", "list", "--json")
    assert json.loads(out)["domains"]["korodexios"] == ["en", "sk"]


def test_locale_set_writes_the_local_file_and_wins_over_the_default(
    capsys, project_root: Path
) -> None:
    write_root(project_root)
    local = project_root / "kdx.local.yml"
    local.write_text("core:\n  log:\n    level: DEBUG\nproject_note: kept\n", encoding="utf-8")
    code, out = run(project_root, capsys, "locale", "set", "sk_SK.UTF-8")
    assert code == 0 and "sk_SK" in out
    saved = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert saved["core"] == {"log": {"level": "DEBUG"}, "locale": "sk_SK"}
    assert saved["project_note"] == "kept"

    code, out = run(project_root, capsys, "locale", "show", "--json")
    assert json.loads(out)["locale"] == "sk_SK"

    code, out = run(project_root, capsys, "locale", "set", "auto")
    assert code == 0
    assert yaml.safe_load(local.read_text(encoding="utf-8"))["core"]["locale"] == "auto"


def test_locale_set_creates_the_file_and_rejects_nonsense(capsys, project_root: Path) -> None:
    write_root(project_root)
    code, _ = run(project_root, capsys, "locale", "set", "not a locale")
    assert code == int(ExitCode.USAGE_ERROR)
    assert not (project_root / "kdx.local.yml").exists()
    code, _ = run(project_root, capsys, "locale", "set", "de")
    assert code == 0
    assert (project_root / "kdx.local.yml").is_file()


def test_locale_set_leaves_an_unreadable_file_alone(capsys, project_root: Path) -> None:
    write_root(project_root)
    (project_root / "kdx.local.yml").write_text("core: [unclosed", encoding="utf-8")
    code, _ = main([*base_args(project_root), "locale", "set", "sk"]), capsys.readouterr()
    # the chassis itself refuses a broken local file before the command runs
    assert code != 0
    assert (project_root / "kdx.local.yml").read_text(encoding="utf-8") == "core: [unclosed"


def test_locale_set_warns_when_a_higher_layer_wins(capsys, project_root: Path) -> None:
    write_root(project_root)
    code, out = run(project_root, capsys, "--locale", "cs", "locale", "set", "sk")
    assert code == 0 and "still wins" in out


def test_locale_check_exit_codes(capsys, project_root: Path) -> None:
    write_root(project_root)
    code, out = run(project_root, capsys, "locale", "check")
    assert code == 0 and "100%" in out
    code, out = run(project_root, capsys, "locale", "check", "--json", "--language", "sk")
    assert code == 0 and json.loads(out)["complete"] is True
    code, out = run(project_root, capsys, "locale", "check", "--domain", "nope")
    assert code == int(ExitCode.USAGE_ERROR)
    code, out = run(project_root, capsys, "locale", "check", "--language", "fr")
    assert code == int(ExitCode.CONFIG_ERROR) and "missing" in out
    code, out = run(project_root, capsys, "locale", "check", "--language", "fr", "--json")
    assert code == int(ExitCode.CONFIG_ERROR) and json.loads(out)["complete"] is False


def test_doctor_names_the_language(capsys, project_root: Path) -> None:
    write_root(project_root)
    code, out = run(project_root, capsys, "--locale", "sk", "doctor")
    assert code == 0 and "Jazyk" in out
    code, out = run(project_root, capsys, "doctor", "--json")
    assert json.loads(out)["language"]["locale"] == "en"


def test_the_renderer_translates_fixed_text_only() -> None:
    from korodexios_core.cli import render

    service = build_i18n("sk")
    render.set_translator(service.translate)
    try:
        assert render.render_section("Modules").splitlines()[0] == "Moduly"
        assert render.render_key_values([("state", "initialized")])[0].split()[0] == "stav"
        assert "initialized" in render.render_key_values([("state", "initialized")])[0]
        table = render.render_table([["x", True]], headers=("name", "state"))
        assert table[0].split()[:2] == ["názov", "stav"] and "áno" in table[2]
        assert render.render_key_values([("x", [])])[0].endswith("(prázdne)")
        assert render.format_bool(False) == "nie"
        assert render._tr_message("  [i] nothing written") == "  [i] nič sa nezapísalo"
        assert render._tr_message("[✓] a dynamic line 42") == "[✓] a dynamic line 42"
        assert render._tr_message("") == ""
        assert render._tr("") == ""
    finally:
        render.set_translator(None)
    assert render.render_section("Modules").splitlines()[0] == "Modules"
    assert render.format_bool(True) == "yes"


def test_command_help_is_translated_but_names_are_not(capsys, project_root: Path) -> None:
    write_root(project_root)
    code = main([*base_args(project_root), "--locale", "sk", "modules", "--help"])
    out = capsys.readouterr().out
    assert code == 0 and "Vypíše nájdené moduly a ich stav." in out
    assert "modules" in out and "list" in out
    code = main([*base_args(project_root), "modules", "--help"])
    assert "List discovered modules and their state." in capsys.readouterr().out


def test_extraction_finds_ui_text_and_skips_names() -> None:
    from korodexios_core.kernel.i18n_extract import scan_source

    source = (
        "CliCommand(name='run', help='Start it.')\n"
        "CliArgument(('--json',), help='Emit JSON.')\n"
        "print_section('Title')\n"
        "print_key_values([('label', value), ('other', 1)])\n"
        "print_table(rows, headers=('a col', 'b col'))\n"
        "write_line(f'{TAG_OK} done text')\n"
        "write_line(f'{TAG_OK} dynamic {value}')\n"
        "write_status('status text')\n"
        "x = _t(request, 'with {field}', field=1)\n"
        "y = ctx.translate('plain')\n"
        "write_line('   ')\n"
    )
    found = {item.message for item in scan_source(source, "m.py")}
    assert found == {
        "Start it.", "Emit JSON.", "Title", "label", "other", "a col", "b col",
        "done text", "status text", "with {field}", "plain",
    }


def test_extract_check_merge_and_stale(tmp_path: Path, capsys, project_root: Path) -> None:
    from korodexios_core.kernel.i18n_extract import merge_po, render_pot, stale_entries

    messages = {"hello": "a.py:1", "new one": "a.py:2"}
    pot = render_pot("demo", messages)
    assert 'msgid "new one"' in pot
    merged, added = merge_po(PO, messages)
    assert added == 1 and 'msgid "new one"' in merged
    assert merge_po(merged, messages) == (merged, 0)
    assert [e.msgid for e in stale_entries(PO, {"hello": ""})] == ["bye {name}"]

    write_root(project_root)
    code, out = run(project_root, capsys, "locale", "extract", "--check")
    assert code == 0 and "up to date" in out
    code, out = run(project_root, capsys, "locale", "extract", "--domain", "nope")
    assert code == int(ExitCode.USAGE_ERROR)
    code, out = run(project_root, capsys, "locale", "extract", "--source", str(project_root / "x"))
    assert code == int(ExitCode.USAGE_ERROR)
    source = tmp_path / "src"
    source.mkdir()
    (source / "m.py").write_text("write_line('brand new text')\n", encoding="utf-8")
    code, out = run(project_root, capsys, "locale", "extract", "--source", str(source), "--check")
    assert code == int(ExitCode.CONFIG_ERROR) and "out of date" in out


def test_extract_writes_template_and_updates_translations(
    monkeypatch, tmp_path: Path, capsys, project_root: Path
) -> None:
    from korodexios_core.kernel import i18n as i18n_module

    locales = make_domain(tmp_path)
    monkeypatch.setattr(i18n_module, "core_locales_dir", lambda: locales)
    monkeypatch.setattr(
        "korodexios_core.kernel.chassis.build_i18n",
        lambda configured: _demo(locales, configured),
    )
    source = tmp_path / "src"
    source.mkdir()
    body = "write_line('hello')\nwrite_line('fresh text')\n"
    (source / "m.py").write_text(body, encoding="utf-8")
    write_root(project_root)
    code, out = run(
        project_root, capsys, "locale", "extract", "--domain", "demo", "--source", str(source)
    )
    assert code == 0 and "strings written" in out
    assert 'msgid "fresh text"' in (locales / "demo.pot").read_text(encoding="utf-8")
    assert 'msgid "fresh text"' in (locales / "sk" / "demo.po").read_text(encoding="utf-8")
    assert "4 new" in out and "1 obsolete" in out


def _demo(locales: Path, configured: str) -> I18n:
    service = I18n(configured)
    service.register("demo", locales)
    return service

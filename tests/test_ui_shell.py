"""The shell's backend: window layout, side-list trees, dock/position, themes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.constants import ExitCode
from korodexios_core.kernel.ui_manifest import build_manifest
from korodexios_core.kernel.ui_prefs import PrefsError, PrefsStore, empty_prefs, validate_prefs
from korodexios_core.kernel.ui_theme import (
    BUILTIN,
    HIGH_CONTRAST,
    ThemeError,
    ThemeStore,
    check_theme,
    complete,
    contrast_ratio,
    css_variables,
    parse_theme,
)
from korodexios_core.kernel.ui_tree import TreeError, node, nodes_payload
from korodexios_core.modules.web_console.module import _pane_list

from .test_ipc import base_args, ipc_chassis
from .test_web_console import console_of

# -- layout preferences ----------------------------------------------------------


def test_layout_is_validated_and_patched_key_by_key(tmp_path: Path) -> None:
    store = PrefsStore(tmp_path / "ui")
    store.patch("ann", {"layout": {"sizes": {"side": 300}, "hidden": ["bottom"]}})
    prefs = store.patch("ann", {"layout": {"sizes": {"inspector": 320}, "ribbon": "labels"}})
    assert prefs["layout"] == {
        "sizes": {"side": 300, "inspector": 320},
        "hidden": ["bottom"],
        "ribbon": "labels",
    }
    cleared = store.patch("ann", {"layout": {"sizes": {"side": None}}})
    assert cleared["layout"]["sizes"] == {"inspector": 320}
    assert store.reset("ann", ("layout",))["layout"] == {}
    assert empty_prefs()["layout"] == {}


@pytest.mark.parametrize(
    "layout",
    [
        {"sizes": {"side": 20}},  # below the pane's limit
        {"sizes": {"side": True}},
        {"sizes": {"ribbon": 44}},  # the ribbon is not resizable
        {"hidden": ["sidebar"]},
        {"ribbon": "huge"},
        {"open": ["a b"]},
        {"open": ["x"] * 201},
        {"nonsense": 1},
        {"area": "Not Valid"},
        "wide",
    ],
)
def test_layout_refuses_nonsense(layout: object) -> None:
    with pytest.raises(PrefsError):
        validate_prefs({"layout": layout})


def test_layout_keeps_open_nodes_unique_and_hidden_in_pane_order() -> None:
    clean = validate_prefs(
        {"layout": {"open": ["a", "b", "a"], "hidden": ["inspector", "ribbon"], "area": "review"}}
    )["layout"]
    assert clean == {"open": ["a", "b"], "hidden": ["ribbon", "inspector"], "area": "review"}


def test_pane_list_understands_all_and_rejects_unknown() -> None:
    assert _pane_list("all") == ["ribbon", "side", "bottom", "inspector"]
    assert _pane_list(" side , bottom ") == ["side", "bottom"]
    assert _pane_list(None) == []
    with pytest.raises(ValueError):
        _pane_list("left")


# -- manifest: placement, sections, dock -----------------------------------------


def _ui(**extra: object) -> dict[str, object]:
    panel = [{"id": "p", "type": "text", "text": "hi"}]
    return {"panels": panel, **extra}


def test_manifest_carries_position_badge_sections_and_dock() -> None:
    manifest = build_manifest(
        [
            (
                "review",
                {
                    "position": "bottom",
                    "badge": "/api/v1/x/count",
                    "sections": [
                        {"id": "cases", "title": "Cases", "tree": "/api/v1/x/tree"},
                        {"id": "tiers", "tree": "/api/v1/x/tiers", "empty": "None"},
                    ],
                    "panels": [
                        {"id": "player", "type": "text", "text": "x", "dock": "bottom",
                         "locked": True},
                        {"id": "plain", "type": "text", "text": "y"},
                    ],
                },
            )
        ]
    )
    entry = manifest["modules"][0]
    assert entry["position"] == "bottom" and entry["badge"] == "/api/v1/x/count"
    assert [s["id"] for s in entry["sections"]] == ["cases", "tiers"]
    assert entry["sections"][1]["title"] == "tiers" and entry["sections"][1]["empty"] == "None"
    player, plain = entry["panels"]
    assert player["dock"] == "bottom" and player["locked"] is True
    assert "dock" not in plain and "locked" not in plain
    assert manifest["issues"] == []


def test_manifest_without_shell_keys_keeps_its_old_shape() -> None:
    entry = build_manifest([("m", _ui())])["modules"][0]
    assert set(entry) == {"module", "title", "icon", "order", "panels"}


def test_manifest_drops_bad_shell_declarations_and_says_why() -> None:
    manifest = build_manifest(
        [
            (
                "m",
                _ui(
                    position="left",
                    badge="http://elsewhere/x",
                    sections=[
                        {"id": "ok", "tree": "/api/v1/t"},
                        {"id": "ok", "tree": "/api/v1/t"},  # duplicate
                        {"id": "notree"},
                        {"id": "../x", "tree": "/api/v1/t"},
                        "nope",
                    ],
                ),
            )
        ]
    )
    entry = manifest["modules"][0]
    assert "position" not in entry and "badge" not in entry
    assert [s["id"] for s in entry["sections"]] == ["ok"]
    assert len(manifest["issues"]) >= 5


def test_an_area_with_only_sections_is_still_an_entry() -> None:
    manifest = build_manifest(
        [("m", {"sections": [{"id": "a", "tree": "/api/v1/t"}]})]
    )
    assert manifest["modules"][0]["panels"] == []


def test_bad_dock_is_reported_and_ignored() -> None:
    manifest = build_manifest(
        [("m", {"panels": [{"id": "p", "type": "text", "text": "x", "dock": "left"}]})]
    )
    assert "dock" not in manifest["modules"][0]["panels"][0]
    assert any("dock" in issue["message"] for issue in manifest["issues"])


# -- tree nodes ------------------------------------------------------------------


def test_tree_nodes_are_checked_and_unique() -> None:
    payload = nodes_payload(
        [
            {"id": "c1", "label": "Case 1", "meta": "review", "tone": "warn",
             "has_children": True, "opens": "case-detail"},
            {"id": "c2", "label": "Case 2"},
        ]
    )
    assert payload["nodes"][0] == {
        "id": "c1", "label": "Case 1", "meta": "review", "tone": "warn",
        "has_children": True, "opens": "case-detail",
    }
    assert payload["nodes"][1] == {"id": "c2", "label": "Case 2"}
    for bad in (
        lambda: node("a b", "x"),
        lambda: node("a", ""),
        lambda: node("a", "x\ny"),
        lambda: node("a", "x", tone="purple"),
        lambda: node("a", "x", opens="Bad Panel"),
        lambda: nodes_payload([{"id": "a", "label": "x"}, {"id": "a", "label": "y"}]),
        lambda: nodes_payload({"id": str(i), "label": "x"} for i in range(501)),
    ):
        with pytest.raises(TreeError):
            bad()


# -- themes ----------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(BUILTIN))
def test_every_builtin_theme_meets_aa_and_contrast_meets_aaa(name: str) -> None:
    assert all(row["ok"] for row in check_theme(BUILTIN[name], "AA")), name
    if name in HIGH_CONTRAST:
        assert all(row["ok"] for row in check_theme(BUILTIN[name], "AAA"))


def test_contrast_ratio_matches_the_wcag_extremes() -> None:
    assert contrast_ratio("#000", "#fff") == pytest.approx(21.0)
    assert contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    with pytest.raises(ThemeError):
        check_theme(BUILTIN["light"], "A")


def test_css_and_json_themes_parse_and_inherit() -> None:
    css = ":root[data-x] { --kdx-bg: #101010; --kdx-accent:#ff00aa }"
    assert parse_theme(css, ".css", "t.css") == {"bg": "#101010", "accent": "#ff00aa"}
    assert parse_theme('{"tokens": {"text": "#fff"}}', ".json", "t.json") == {"text": "#fff"}
    full = complete({"bg": "#101010"})
    assert full["bg"] == "#101010" and full["text"] == BUILTIN["graphite"]["text"]
    variables = css_variables(full)
    assert variables["--kdx-text"] == variables["--fg"] == variables["--text"]
    assert variables["--kdx-muted"] == variables["--mut"]


@pytest.mark.parametrize(
    ("text", "suffix"),
    [
        (":root { --kdx-bg: red; }", ".css"),
        (":root { --kdx-nothing: #fff; }", ".css"),
        ("body { color: #fff }", ".css"),
        ("[1]", ".json"),
        ("{", ".json"),
        ('{"tokens": {"bg": 5}}', ".json"),
        ("x" * 20000, ".css"),
    ],
)
def test_bad_theme_files_are_refused(text: str, suffix: str) -> None:
    with pytest.raises(ThemeError):
        parse_theme(text, suffix, "bad")


def test_theme_store_merges_sources_and_reports_bad_files(tmp_path: Path) -> None:
    project, user = tmp_path / "themes", tmp_path / "user"
    project.mkdir()
    user.mkdir()
    (project / "ocean.css").write_text(":root { --kdx-bg: #001a2c; }", encoding="utf-8")
    (project / "light.json").write_text('{"bg": "#ffffff"}', encoding="utf-8")  # overrides built-in
    (user / "ocean.css").write_text(":root { --kdx-bg: #002b45; }", encoding="utf-8")
    (user / "broken.css").write_text(":root { --kdx-bg: nope; }", encoding="utf-8")
    (user / "Bad Name.css").write_text(":root { --kdx-bg: #fff; }", encoding="utf-8")
    found, problems = ThemeStore(project, user).themes()
    assert found["ocean"].source == "user" and found["ocean"].tokens["bg"] == "#002b45"
    assert found["light"].source == "project" and found["light"].tokens["bg"] == "#ffffff"
    assert found["graphite"].source == "builtin"
    assert len(problems) == 2
    with pytest.raises(ThemeError, match="available"):
        ThemeStore(project).get("missing")


def test_theme_route_follows_the_callers_choice_and_survives_a_missing_theme(
    project_root,
) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        console = console_of(chassis)
        assert console.theme_payload("ann")["active"] == "graphite"
        console.prefs.patch("ann", {"settings": {"theme": "light"}})
        light = console.theme_payload("ann")
        assert light["active"] == "light" and light["tokens"]["bg"] == "#f6f5fa"
        assert "--kdx-bg: #f6f5fa;" in light["css"]
        assert console.theme_payload("bob")["active"] == "graphite"  # per person
        assert console.theme_payload("ann", "contrast")["active"] == "contrast"
        console.prefs.patch("ann", {"settings": {"theme": "system"}})
        system = console.theme_payload("ann")
        assert system["system"] == {"dark": "graphite", "light": "light"}
        console.prefs.patch("ann", {"settings": {"theme": "gone"}})
        fallback = console.theme_payload("ann")
        assert fallback["active"] == "graphite" and fallback["problems"]
        assert {"graphite", "light", "contrast", "system"} <= {
            t["name"] for t in fallback["themes"]
        }
        json.dumps(fallback)  # the payload is plain JSON
    finally:
        chassis.shutdown()


# -- the terminal does everything the shell does ------------------------------------


def test_layout_commands_round_trip(project_root, capsys) -> None:
    base = [*base_args(project_root), "ui", "layout"]
    assert main([*base, "set", "--side", "300", "--hide", "bottom,inspector", "--user", "t"]) == 0
    assert main([*base, "set", "--show", "inspector", "--ribbon", "labels", "--user", "t"]) == 0
    capsys.readouterr()
    assert main([*base, "show", "--user", "t", "--json"]) == 0
    layout = json.loads(capsys.readouterr().out)
    assert layout == {"sizes": {"side": 300}, "hidden": ["bottom"], "ribbon": "labels"}
    assert main([*base, "set", "--hide", "all", "--show", "side", "--user", "t"]) == 0
    capsys.readouterr()
    assert main([*base, "show", "--user", "t", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["hidden"] == ["ribbon", "bottom", "inspector"]
    assert main([*base, "set", "--user", "t"]) == int(ExitCode.USAGE_ERROR)  # nothing to change
    assert main([*base, "set", "--hide", "left", "--user", "t"]) == int(ExitCode.USAGE_ERROR)
    assert main([*base, "reset", "--user", "t"]) == 0
    capsys.readouterr()
    assert main([*base, "show", "--user", "t", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_theme_commands_choose_check_and_print_css(project_root, capsys, tmp_path) -> None:
    base = [*base_args(project_root), "ui", "theme"]
    assert main([*base, "set", "light", "--user", "t"]) == 0
    assert main([*base, "set", "nonsuch", "--user", "t"]) == int(ExitCode.USAGE_ERROR)
    capsys.readouterr()
    assert main([*base, "list", "--user", "t", "--json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["active"] == "light"
    assert {"graphite", "light", "contrast", "system"} <= {t["name"] for t in listing["themes"]}
    assert main([*base, "show", "--css", "--user", "t"]) == 0
    assert "--kdx-bg: #f6f5fa;" in capsys.readouterr().out
    assert main([*base, "check", "contrast", "--level", "AAA"]) == 0
    weak = tmp_path / "weak.css"
    weak.write_text(":root { --kdx-text: #888888; --kdx-bg: #909090; }", encoding="utf-8")
    capsys.readouterr()
    assert main([*base, "check", str(weak), "--json"]) == int(ExitCode.ERROR)
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and report["pairs"][0]["ok"] is False
    assert main([*base, "check", str(tmp_path / "missing.css")]) == int(ExitCode.USAGE_ERROR)
    assert main([*base, "set", "system", "--user", "t"]) == 0


# -- the shell page ----------------------------------------------------------------


def test_shell_page_ships_and_keeps_the_console_security_rules() -> None:
    from korodexios_core.modules.web_console.module import STATIC_DIR

    for name in ("shell.html", "shell.js", "shell.css"):
        assert (STATIC_DIR / name).is_file(), name
    page = (STATIC_DIR / "shell.html").read_text(encoding="utf-8")
    assert "script-src 'self'" in page and "style-src 'self'" in page
    assert "unsafe-inline" not in page and "<script>" not in page and "style=" not in page
    script = (STATIC_DIR / "shell.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script  # daemon data is only ever written as text
    assert "eval(" not in script and "setAttribute(\"style\"" not in script
    for endpoint in ("/api/v1/ui/manifest", "/api/v1/ui/theme", "/api/v1/ui/prefs"):
        assert endpoint in script


def test_console_url_can_point_at_the_shell() -> None:
    from korodexios_core.kernel.ipc import IpcDiscovery
    from korodexios_core.modules.web_console.module import SHELL_PAGE, console_url

    discovery = IpcDiscovery(
        pid=1, host="127.0.0.1", port=1234, token="tok", started_at=0.0
    )
    assert console_url(discovery).endswith("/ui/#token=tok")
    assert console_url(discovery, SHELL_PAGE).endswith("/ui/shell.html#token=tok")

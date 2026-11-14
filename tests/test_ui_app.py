"""App GUI tiers: the shared client, 'vue', 'kdx ui add', 'kdx ui types', embed protocol v1."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.cli.init_project import ProjectError, build_files, project_from_inputs
from korodexios_core.cli.ui_add import add_tier, tier_files
from korodexios_core.cli.ui_app import APP_ROOT, APP_TIERS, app_files
from korodexios_core.cli.ui_types import render_types
from korodexios_core.kernel import ui_embed
from korodexios_core.kernel.ui_manifest import build_manifest
from korodexios_core.kernel.ui_tiers import guide, tier_names

from .test_ipc import base_args
from .test_ipc_routes import make_server, probe_routes

_LEFTOVER = re.compile(r"@[A-Z]+@")


def _json_out(text: str) -> dict:
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


@pytest.mark.parametrize("tier", list(APP_TIERS))
def test_every_app_tier_carries_the_same_shared_client(tier: str) -> None:
    files = app_files(project_from_inputs("demo_app", ui=tier))
    common = sorted(p.relative_to(APP_ROOT / "common").as_posix()
                    for p in (APP_ROOT / "common").rglob("*") if p.is_file())
    for name in common:
        assert f"web/{name}" in files, name
    for name in ("auth.ts", "api.ts", "events.ts", "media.ts", "types.ts", "auth.test.ts"):
        assert f"web/src/kdx/{name}" in files
    for name in ("module.py", "kdx_module.toml", "__init__.py"):
        assert f"src/demo_app/modules/web_app/{name}" in files
    for path, text in files.items():
        assert not _LEFTOVER.search(text), (path, _LEFTOVER.findall(text))
    assert APP_TIERS[tier]["framework"] in files["web/README.md"]
    assert APP_TIERS[tier]["overrides"] in files["web/README.md"]


def test_the_vue_tier_is_a_full_app_with_its_own_toolchain() -> None:
    project = project_from_inputs("demo_app", ui="vue", pm="pnpm")
    assert (project.ui, project.pm, project.ecosystem) == ("vue", "pnpm", "fullstack")
    files = build_files(project)
    package = json.loads(files["web/package.json"])
    assert "vue" in package["dependencies"]
    assert {"vue-tsc", "@vue/test-utils", "@vitejs/plugin-vue", "vitest"} <= set(
        package["devDependencies"]
    )
    assert package["scripts"]["typecheck"] == "vue-tsc --noEmit"
    for name in ("src/App.vue", "src/main.ts", "src/overrides.ts", "src/panels/PanelCard.vue",
                 "src/panels/EmbedPanel.vue", "src/panels/MediaCell.vue", "src/App.test.ts"):
        assert f"web/{name}" in files, name
    assert 'const pm = "pnpm";' in files["web/playwright.config.ts"]
    # newer pnpm refuses esbuild's build script unless the project allows it
    assert "allowBuilds:\n  esbuild: true" in files["web/pnpm-workspace.yaml"]
    assert "web/pnpm-workspace.yaml" not in build_files(project_from_inputs("demo_app", ui="vue"))
    assert "Vue app" in files[".agents/skills/web-ui.md"]
    assert "Vue + TypeScript app" in files["README.md"]


def test_vue_is_a_tier_everywhere_an_agent_looks() -> None:
    assert "vue" in tier_names()
    data = guide()
    assert data["tiers"]["vue"]["add"] == "kdx ui add vue"
    assert "add" not in data["tiers"]["none"]
    assert data["embed"] == ui_embed.PROTOCOL
    with pytest.raises(ProjectError):
        project_from_inputs("demo", ui="static", pm="npm")  # --pm is for app tiers only


def test_the_client_and_the_shell_speak_embed_protocol_v1() -> None:
    from korodexios_core.modules.web_console.module import STATIC_DIR

    auth = (APP_ROOT / "common" / "src" / "kdx" / "auth.ts").read_text(encoding="utf-8")
    shell = (STATIC_DIR / "shell.js").read_text(encoding="utf-8")
    for text in (auth, shell):
        assert ui_embed.READY_MESSAGE in text and ui_embed.TOKEN_MESSAGE in text
    assert f"EMBED_PROTOCOL = {ui_embed.PROTOCOL_VERSION}" in auth
    assert "cookie:" in shell and "version: 1" in shell
    # the shared client never sends an empty token header
    assert 'state.token ? { [TOKEN_HEADER]: state.token } : {}' in auth


def _project(tmp_path: Path, name: str = "sonus_demo") -> Path:
    root = tmp_path / name
    assert main([*base_args(tmp_path), "init", name, "--path", str(root), "--yes",
                 "--ui", "none"]) == 0
    return root


def test_ui_add_gives_an_existing_project_an_app_tier_once(tmp_path, capsys) -> None:
    root = _project(tmp_path)
    assert not (root / "web").exists()
    args = base_args(tmp_path)
    capsys.readouterr()
    assert main([*args, "ui", "add", "vue", "--path", str(root), "--dry-run", "--json"]) == 0
    planned = _json_out(capsys.readouterr().out)
    assert planned["dry_run"] is True and "web/src/App.vue" in planned["files"]
    assert not (root / "web").exists()

    assert main([*args, "ui", "add", "vue", "--pm", "bun", "--path", str(root)]) == 0
    out = capsys.readouterr().out
    assert "bun install" in out and "ui types" in out
    assert (root / "web" / "src" / "kdx" / "auth.ts").is_file()
    assert (root / "src" / "sonus_demo" / "modules" / "web_app" / "kdx_module.toml").is_file()
    assert "sonus-demo-web" in (root / "web" / "package.json").read_text(encoding="utf-8")
    ignore = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ignore.count("node_modules/") == 1 and "web/dist/" in ignore

    assert main([*args, "ui", "add", "react", "--path", str(root), "--json"]) == 1
    refused = _json_out(capsys.readouterr().out)
    assert refused["ok"] is False and "refusing to overwrite" in refused["error"]
    assert not (root / "web" / "src" / "App.tsx").exists()  # nothing was written


def test_ui_add_matches_what_init_seeds_and_refuses_what_it_cannot(tmp_path) -> None:
    root = _project(tmp_path)
    result = add_tier(root, "static")
    seeded = tier_files(project_from_inputs("sonus_demo", ui="static"))
    assert result.files == sorted(seeded)
    for path, text in seeded.items():
        assert (root / path).read_text(encoding="utf-8") == text
    with pytest.raises(ProjectError, match="cannot add GUI tier"):
        add_tier(root, "none")
    with pytest.raises(ProjectError, match=r"pyproject\.toml"):
        add_tier(tmp_path / "nowhere", "vue")


def _contract() -> dict:
    return {
        "schema": 3,
        "core_api": 2,
        "static_mounts": [{"route_prefix": "/app"}, {"route_prefix": "/ui"}],
        "routes": [
            {"method": "GET", "path": "/api/v1/status", "auth": True, "fields": ["modules", "app"]},
            {"method": "POST", "path": "/api/v1/rpc", "auth": True, "fields": ["ok"]},
        ],
        "module_routes": [
            {"path": "/api/v1/queue", "methods": ["GET", "POST"], "auth": True,
             "module": "job_queue", "role": "operator"},
            {"path": "/api/v1/queue", "methods": ["DELETE"], "auth": True, "module": "job_queue"},
        ],
    }


def test_route_types_are_sorted_merged_and_stable() -> None:
    text = render_types(_contract())
    assert text == render_types(_contract())
    routes = '"/api/v1/queue"\n  | "/api/v1/rpc"\n  | "/api/v1/status"'
    assert f"export type RoutePath =\n  | {routes};" in text
    assert '"/api/v1/queue": { methods: ["DELETE", "GET", "POST"], auth: true, role: "operator", ' \
           'module: "job_queue" },' in text
    assert '"/api/v1/status": "app" | "modules";' in text
    assert 'export type StaticMount =\n  | "/app"\n  | "/ui";' in text
    assert "satisfies Record<RoutePath, RouteInfo>" in text
    assert "export function route<P extends RoutePath>" in text


def test_route_types_of_a_real_control_plane(tmp_path) -> None:
    server = make_server(tmp_path, routes=probe_routes())
    try:
        text = render_types(server.api_contract())
    finally:
        server.stop()
    for path in ("/health", "/api/v1/schema", "/api/v1/status", "/api/v1/probe"):
        assert f'"{path}"' in text


def test_ui_types_writes_and_checks_a_file(tmp_path, capsys) -> None:
    contract = tmp_path / "schema.json"
    contract.write_text(json.dumps(_contract()), encoding="utf-8")
    out = tmp_path / "web" / "src" / "kdx" / "schema.ts"
    args = [*base_args(tmp_path), "ui", "types", "--from", str(contract), "--out", str(out)]
    assert main(args) == 0
    assert out.read_text(encoding="utf-8") == render_types(_contract())
    assert main([*args, "--check"]) == 0
    changed = _contract()
    changed["module_routes"] = []
    contract.write_text(json.dumps(changed), encoding="utf-8")
    capsys.readouterr()
    assert main([*args, "--check"]) == 1
    assert "stale" in capsys.readouterr().out
    assert main([*base_args(tmp_path), "ui", "types", "--from", str(tmp_path / "missing")]) == 1


def _panel(**extra: object) -> tuple[dict, list]:
    declared = {"id": "rows", "type": "table", "source": "/api/v1/demo", "path": "items", **extra}
    manifest = build_manifest([("demo", {"panels": [declared]})], known_commands=frozenset())
    panels = manifest["modules"][0]["panels"] if manifest["modules"] else []
    return (panels[0] if panels else {}), manifest["issues"]


def test_refresh_on_names_event_prefixes_and_is_checked() -> None:
    panel, issues = _panel(refresh_on=["queue.", "player.track", "queue.", "*"])
    assert panel["refresh_on"] == ["queue.", "player.track", "*"] and not issues
    plain, issues = _panel()
    assert "refresh_on" not in plain and not issues  # older renderers see the old shape
    panel, issues = _panel(refresh_on=["ok.", "bad name", 3])
    assert panel["refresh_on"] == ["ok."]
    assert [issue["message"] for issue in issues] == [
        "refresh_on: 'bad name' is not an event name prefix",
        "refresh_on: 3 is not an event name prefix",
    ]
    panel, issues = _panel(refresh_on="queue.")
    assert "refresh_on" not in panel and "must be a list" in issues[0]["message"]
    panel, issues = _panel(refresh_on=[f"t{n}." for n in range(12)])
    assert len(panel["refresh_on"]) == 8 and "first 8" in issues[0]["message"]


def test_a_media_column_is_kept_and_the_shell_draws_it_safely() -> None:
    from korodexios_core.modules.web_console.module import STATIC_DIR

    panel, issues = _panel(columns=[{"key": "file", "kind": "media"}, "name"])
    assert panel["columns"][0] == {"key": "file", "label": "file", "kind": "media"} and not issues
    shell = (STATIC_DIR / "shell.js").read_text(encoding="utf-8")
    assert 'case "media": return el("td", {}, [mediaNode(value)]);' in shell
    assert '!text.startsWith("/api/")' in shell and "URL.createObjectURL" in shell
    assert "watchTopics(panel.refresh_on, draw, card)" in shell
    assert "setTimeout(streamLive, 0)" in shell  # start once the card is in the page
    assert "state.token ? { [TOKEN_HEADER]: state.token } : {}" in shell  # never an empty header
    assert "KdxToken.drop();" in shell

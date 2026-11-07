"""GUI tiers: the guide an agent reads, the environment probe, and 'kdx init --ui'."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.cli.init_project import ProjectError, project_from_inputs
from korodexios_core.constants import ExitCode
from korodexios_core.kernel.ui_tiers import TIERS, guide, probe_environment, tier_names

from .test_ipc import base_args


def _probe(**kwargs):
    defaults = {
        "which": lambda name: None,
        "run": lambda command: "v1.0.0",
        "environ": {},
        "platform": "linux",
        "exists": lambda path: False,
        "home": Path("/home/x"),
    }
    defaults.update(kwargs)
    return probe_environment(**defaults)


def test_the_guide_describes_every_tier_with_a_way_to_create_it() -> None:
    data = guide()
    assert data["default"] == "none" and set(data["tiers"]) == set(TIERS) == set(tier_names())
    for name, tier in data["tiers"].items():
        assert tier["init"] == f"kdx init NAME --ui {name}"
        assert tier["choose_when"] and tier["avoid_when"] and tier["testing"]["headless"]
    assert any("doctor" in step for step in data["decide"])
    assert any("terminal" in rule.lower() for rule in data["rules"])


def test_probe_on_a_bare_linux_host_reports_no_browser_and_no_display() -> None:
    report = _probe()
    assert report["tools"]["node"] == {"found": False, "path": None, "version": None}
    assert report["browsers"] == [] and report["playwright_browsers"] is False
    assert report["display"] == {"available": False, "reason": "no DISPLAY or WAYLAND_DISPLAY"}
    assert report["testing"] == {
        "api_and_html": True, "headless_browser": False, "headed_browser": False,
        "recommended": {"install": "npx playwright install chromium"}}
    assert report["tiers"]["none"]["ready"] and report["tiers"]["static"]["ready"]  # Python only


def test_probe_finds_tools_browsers_and_a_display() -> None:
    present = {"node": "/bin/node", "bun": "/bin/bun", "chromium": "/bin/chromium"}
    report = _probe(which=present.get, environ={"DISPLAY": ":0"})
    assert report["tools"]["node"]["version"] == "v1.0.0"
    assert report["tools"]["bun"]["found"] and not report["tools"]["pnpm"]["found"]
    assert report["browsers"] == [{"name": "chromium", "path": "/bin/chromium"}]
    assert report["display"]["available"] is True
    assert report["testing"]["headless_browser"] and report["testing"]["headed_browser"]


def test_probe_knows_playwright_caches_ssh_and_windows_paths(tmp_path: Path) -> None:
    cache = tmp_path / ".cache" / "ms-playwright"
    (cache / "chromium-1100").mkdir(parents=True)
    report = _probe(exists=Path.exists, home=tmp_path)
    assert report["playwright_browsers"] is True
    assert report["testing"]["headless_browser"] and not report["testing"]["headed_browser"]

    ssh = _probe(platform="darwin", environ={"SSH_CONNECTION": "1 2 3 4"})
    assert ssh["display"] == {"available": False, "reason": "this is an SSH session"}
    desktop = _probe(platform="darwin")
    assert desktop["display"]["available"] is True

    chrome = tmp_path / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("", encoding="utf-8")
    windows = _probe(platform="win32", environ={"ProgramFiles": str(tmp_path)}, exists=Path.exists)
    assert [b["name"] for b in windows["browsers"]] == ["chrome"]

    configured = tmp_path / "pw"
    (configured / "chromium-1").mkdir(parents=True)
    assert _probe(environ={"PLAYWRIGHT_BROWSERS_PATH": str(configured)}, exists=Path.exists)[
        "playwright_browsers"] is True


def test_init_ui_option_and_the_ecosystem_alias_agree() -> None:
    assert project_from_inputs("demo").ui == "none"
    assert project_from_inputs("demo", ui="static").ecosystem == "fullstack"
    assert project_from_inputs("demo", ecosystem="fullstack").ui == "static"
    assert project_from_inputs("demo", ui="none").fullstack is False
    assert project_from_inputs("demo", ui="static", ecosystem="fullstack").ui == "static"
    for kwargs in (
        {"ui": "angular"},
        {"ui": "static", "ecosystem": "python"},
        {"ui": "static", "pm": "bun"},
        {"ui": "react", "pm": "yarn"},
        {"ui": "none", "ecosystem": "fullstack"},
    ):
        with pytest.raises(ProjectError):
            project_from_inputs("demo", **kwargs)


def test_init_seeds_web_only_for_a_gui_tier_and_documents_the_choice(tmp_path, capsys) -> None:
    args = base_args(tmp_path)
    plain = tmp_path / "plain"
    assert main([*args, "init", "plain_app", "--path", str(plain), "--yes", "--ui", "none"]) == 0
    assert not (plain / "web").exists()
    readme = (plain / "README.md").read_text(encoding="utf-8")
    assert "GUI tier: `none`" in readme and "ui guide" in readme

    web = tmp_path / "web_app"
    assert main([*args, "init", "web_app", "--path", str(web), "--yes", "--ui", "static"]) == 0
    assert (web / "web" / "index.html").is_file()
    assert "GUI tier: `static`" in (web / "README.md").read_text(encoding="utf-8")
    capsys.readouterr()
    assert main([*args, "init", "bad_app", "--path", str(tmp_path / "bad"), "--yes",
                 "--ui", "angular"]) == int(ExitCode.ERROR)
    assert "unknown ui tier" in capsys.readouterr().out


def test_terminal_prints_the_guide_and_the_doctor_report(capsys) -> None:
    args = base_args(Path("."))
    assert main([*args, "ui", "guide", "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{"):out.rindex("}") + 1])["default"] == "none"
    assert main([*args, "ui", "guide"]) == 0
    assert "how to decide" in capsys.readouterr().out
    assert main([*args, "ui", "doctor", "--json"]) == 0
    out = capsys.readouterr().out
    assert "headless_browser" in json.loads(out[out.index("{"):out.rindex("}") + 1])["testing"]
    assert main([*args, "ui", "doctor"]) == 0
    assert "tier static" in capsys.readouterr().out


def test_react_tier_needs_node_with_npm_or_bun() -> None:
    bare = _probe()
    assert bare["tiers"]["react"] == {"ready": False, "missing": ["node (with npm) or bun"]}
    node_only = _probe(which={"node": "/bin/node"}.get)
    assert node_only["tiers"]["react"]["ready"] is False  # Node without npm is not enough
    assert _probe(which={"node": "/n", "npm": "/n"}.get)["tiers"]["react"]["ready"] is True
    assert _probe(which={"bun": "/b"}.get)["tiers"]["react"]["ready"] is True


def test_react_tier_seeds_the_app_the_module_and_the_chosen_package_manager(tmp_path) -> None:
    from korodexios_core.cli.init_project import build_files

    project = project_from_inputs("demo_app", ui="react", pm="bun")
    assert (project.ecosystem, project.ui, project.pm) == ("fullstack", "react", "bun")
    files = build_files(project)
    for name in (
        "web/package.json", "web/tsconfig.json", "web/vite.config.ts", "web/playwright.config.ts",
        "web/src/App.tsx", "web/src/panels.tsx", "web/src/overrides.tsx", "web/src/App.test.tsx",
        "web/e2e/smoke.spec.ts", "web/README.md",
        "src/demo_app/modules/web_app/module.py", "src/demo_app/modules/web_app/kdx_module.toml",
        ".agents/skills/web-ui.md",
    ):
        assert name in files, name
    package = json.loads(files["web/package.json"])
    assert package["name"] == "demo-app-web"
    assert {"test", "e2e", "build", "typecheck"} <= set(package["scripts"])
    assert "react" in package["dependencies"] and "vitest" in package["devDependencies"]
    json.loads(files["web/tsconfig.json"])
    assert 'const pm = "bun";' in files["web/playwright.config.ts"]
    assert "uv run demo-app run" in files["web/playwright.config.ts"]
    assert "bun install" in files["web/README.md"] and "bun run e2e" in files["web/README.md"]
    assert "@" not in "".join(
        line for line in files["web/README.md"].splitlines() if "@DIST@" in line or "@PM@" in line
    )
    assert "bun run typecheck" in files[".agents/skills/web-ui.md"]
    assert "web/playwright-report/" in files[".gitignore"] and "data/ui/" in files[".gitignore"]
    assert "React" in files["README.md"] and "GUI tier: `react`" in files["README.md"]
    assert not any(name.startswith("web/src/main.js") for name in files)


def test_the_seeded_web_app_module_serves_dist_only_once_it_is_built(tmp_path) -> None:
    import importlib.util
    from types import SimpleNamespace

    from korodexios_core.cli.init_project import build_files

    source = build_files(project_from_inputs("demo_app", ui="react"))[
        "src/demo_app/modules/web_app/module.py"
    ]
    module_file = tmp_path / "web_app_module.py"
    module_file.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("web_app_module", module_file)
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    module = loaded.MODULE
    assert module.static_dirs() == ()  # no context yet
    module._context = SimpleNamespace(paths=SimpleNamespace(root=tmp_path))
    module.__class__.paths = property(lambda self: self._context.paths)  # the context is a stub
    assert module.static_dirs() == ()  # not built yet
    (tmp_path / "web" / "dist").mkdir(parents=True)
    (mount,) = module.static_dirs()
    assert mount.route_prefix == "/app" and mount.auth is False
    assert Path(mount.directory) == tmp_path / "web" / "dist"


def test_init_pm_flag_reaches_the_react_seed(tmp_path) -> None:
    args = base_args(tmp_path)
    target = tmp_path / "pm_app"
    assert main([*args, "init", "pm_app", "--path", str(target), "--yes", "--ui", "react",
                 "--pm", "pnpm"]) == 0
    assert "pnpm run e2e" in (target / "web" / "README.md").read_text(encoding="utf-8")
    assert (target / "web" / "src" / "App.tsx").is_file()


def test_the_recommended_browser_setting_follows_what_is_installed(tmp_path) -> None:
    chrome = _probe(which={"google-chrome": "/bin/chrome"}.get, environ={"DISPLAY": ":0"})
    assert chrome["testing"]["recommended"] == {"PW_CHANNEL": "chrome", "PW_HEADED": "1"}
    edge = _probe(which={"msedge": "/bin/edge"}.get)
    assert edge["testing"]["recommended"] == {"PW_CHANNEL": "msedge"}
    (tmp_path / ".cache" / "ms-playwright" / "chromium-1").mkdir(parents=True)
    cached = _probe(exists=Path.exists, home=tmp_path, environ={"DISPLAY": ":0"})
    assert cached["testing"]["recommended"] == {"PW_HEADED": "1"}
    # the cache may hold another Chromium revision: an installed browser still wins
    both = _probe(which={"chromium": "/bin/chromium"}.get, exists=Path.exists, home=tmp_path)
    assert both["testing"]["recommended"] == {}  # chromium is not a Chrome/Edge channel


def test_known_limits_are_published_for_agents_and_in_the_seeded_readme() -> None:
    from korodexios_core.cli.init_project import build_files

    notes = guide()["notes"]
    assert {note["applies_to"] for note in notes} == {"react", "static"}
    assert all(note["id"] and note["text"] for note in notes)
    readme = build_files(project_from_inputs("demo_app", ui="react", pm="pnpm"))["web/README.md"]
    assert "Known limits" in readme and "pnpm run typecheck && pnpm run test" in readme
    assert "@" not in readme.replace("@types", "")

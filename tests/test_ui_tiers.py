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
        "api_and_html": True, "headless_browser": False, "headed_browser": False}
    assert all(tier["ready"] for tier in report["tiers"].values())  # none/static need Python only


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
        {"ui": "react"},
        {"ui": "static", "ecosystem": "python"},
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
                 "--ui", "react"]) == int(ExitCode.ERROR)
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

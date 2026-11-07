"""'kdx ui test': headless first, headed on request, skipped steps say why."""

from __future__ import annotations

import json
from pathlib import Path

from korodexios_core.cli.entry import main
from korodexios_core.cli.ui_test import detect_package_manager, run_command, run_gui_tests

from .test_ipc import base_args

CLEAN = {"modules": [{"module": "demo"}], "issues": []}
FULL = {
    "testing": {"headless_browser": True, "headed_browser": True,
                "recommended": {"PW_CHANNEL": "chrome", "PW_HEADED": "1"}},
    "display": {"available": True, "reason": "desktop session"},
}
BARE = {
    "testing": {"headless_browser": False, "headed_browser": False,
                "recommended": {"install": "npx playwright install chromium"}},
    "display": {"available": False, "reason": "no DISPLAY or WAYLAND_DISPLAY"},
}


def web_project(tmp_path: Path, *, modules: bool = True, lock: str = "") -> Path:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text("{}", encoding="utf-8")
    if modules:
        (tmp_path / "web" / "node_modules").mkdir()
    if lock:
        (tmp_path / "web" / lock).write_text("", encoding="utf-8")
    return tmp_path


class Recorder:
    def __init__(self, fail: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.fail = fail

    def __call__(self, command, cwd, env, timeout):  # type: ignore[no-untyped-def]
        self.calls.append((command, dict(env)))
        failing = bool(self.fail) and command[-1] == self.fail
        return (1, "boom\n" * 3) if failing else (0, "ok")


def run(root: Path, **kwargs):  # type: ignore[no-untyped-def]
    options = {"manifest": CLEAN, "environment": FULL, "runner": Recorder(),
               "which": lambda name: f"/bin/{name}", "environ": {}}
    options.update(kwargs)
    return run_gui_tests(root, **options)


def test_a_project_without_a_web_app_only_checks_the_manifest(tmp_path: Path) -> None:
    report = run(tmp_path)
    assert [(s.name, s.status) for s in report.steps] == [
        ("manifest", "passed"), ("web app", "skipped")]
    assert report.ok


def test_manifest_issues_fail_the_run(tmp_path: Path) -> None:
    bad = {"modules": [], "issues": [{"module": "demo", "panel": "p", "message": "broken"}]}
    report = run(tmp_path, manifest=bad)
    assert not report.ok and "demo.p: broken" in report.steps[0].detail


def test_headless_runs_first_and_headed_only_when_asked_and_possible(tmp_path: Path) -> None:
    root = web_project(tmp_path)
    recorder = Recorder()
    report = run(root, runner=recorder, headed=True)
    assert [s.name for s in report.steps] == [
        "manifest", "typecheck", "component tests", "e2e (headless)", "e2e (headed)"]
    assert report.ok
    commands = [call[0][1:] for call in recorder.calls]
    assert commands == [["run", "typecheck"], ["run", "test"], ["run", "e2e"], ["run", "e2e"]]
    headless_env, headed_env = recorder.calls[2][1], recorder.calls[3][1]
    assert "PW_HEADED" not in headless_env and headed_env["PW_HEADED"] == "1"
    assert headless_env["PW_CHANNEL"] == "chrome"  # the recommended installed browser

    only_headless = run(root, runner=Recorder())
    assert [s.name for s in only_headless.steps][-1] == "e2e (headless)"


def test_steps_that_cannot_run_here_are_skipped_with_the_reason(tmp_path: Path) -> None:
    root = web_project(tmp_path)
    report = run(root, environment=BARE, headed=True)
    by_name = {s.name: s for s in report.steps}
    assert by_name["e2e (headless)"].status == "skipped"
    assert "npx playwright install chromium" in by_name["e2e (headless)"].detail
    assert by_name["e2e (headed)"].status == "skipped"
    assert "no DISPLAY" in by_name["e2e (headed)"].detail
    assert report.ok  # skipped is not failed

    no_browser_with_display = {**BARE, "display": {"available": True, "reason": "desktop"}}
    again = run(root, environment=no_browser_with_display, headed=True)
    assert "no browser" in {s.name: s for s in again.steps}["e2e (headed)"].detail


def test_a_failing_step_is_reported_with_its_output_tail(tmp_path: Path) -> None:
    report = run(web_project(tmp_path), runner=Recorder(fail="test"))
    failed = next(s for s in report.steps if s.status == "failed")
    assert failed.name == "component tests" and failed.exit_code == 1
    assert failed.output.count("boom") == 3 and not report.ok


def test_missing_node_modules_asks_for_install_or_installs(tmp_path: Path) -> None:
    root = web_project(tmp_path, modules=False)
    skipped = run(root)
    assert skipped.steps[-1].status == "skipped" and "--install" in skipped.steps[-1].detail
    recorder = Recorder()
    installed = run(root, runner=recorder, install=True)
    assert recorder.calls[0][0][1:] == ["install"] and installed.ok
    failing = run(root, runner=Recorder(fail="install"), install=True)
    assert [s.name for s in failing.steps][-1] == "install" and not failing.ok


def test_package_manager_comes_from_the_request_the_lockfile_or_npm(tmp_path: Path) -> None:
    web = tmp_path / "web"
    web.mkdir()
    assert detect_package_manager(web) == "npm"
    (web / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    assert detect_package_manager(web) == "pnpm"
    assert detect_package_manager(web, "bun") == "bun"
    recorder = Recorder()
    project = tmp_path / "proj"
    project.mkdir()
    web_project(project, lock="bun.lock")
    run(project, runner=recorder)
    assert recorder.calls[0][0][0] == "/bin/bun"
    assert run(project, which=lambda name: None).steps[-1].detail.endswith("is not installed here")
    unknown = run(project, pm="yarn")
    assert unknown.steps[-1].status == "failed"


def test_run_command_reports_exit_codes_timeouts_and_missing_programs(tmp_path: Path) -> None:
    import sys

    ok = run_command([sys.executable, "-c", "print('hi')"], tmp_path, {}, 20)
    assert ok[0] == 0 and "hi" in ok[1]
    bad = run_command([sys.executable, "-c", "import sys; sys.exit(3)"], tmp_path, {}, 20)
    assert bad[0] == 3
    slow = run_command([sys.executable, "-c", "import time; time.sleep(5)"], tmp_path, {}, 0.5)
    assert slow[0] == 124 and "timed out" in slow[1]
    assert run_command(["definitely-not-a-program-xyz"], tmp_path, {}, 5)[0] == 127


def test_the_command_prints_json_and_sets_the_exit_code(project_root, capsys) -> None:
    args = base_args(project_root)
    assert main([*args, "ui", "test", "--json"]) == 0
    out = capsys.readouterr().out
    report = json.loads(out[out.index("{"):out.rindex("}") + 1])
    assert report["ok"] is True and report["steps"][0]["name"] == "manifest"
    assert main([*args, "ui", "test"]) == 0
    assert "manifest: passed" in capsys.readouterr().out

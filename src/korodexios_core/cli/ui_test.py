"""'kdx ui test': run every GUI test this machine can run, headless first, headed on request.

One command for a person or an agent: it checks the module manifest, and when the project has
a ``web/`` app it runs the type check, the component tests and the end-to-end tests with the
package manager the project uses. The end-to-end run is headless; ``--headed`` adds a second
run with a visible browser window, but only when ``kdx ui doctor`` found a display - a step
that cannot run here is reported as skipped with the reason, never as a silent pass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..kernel.ui_tiers import PACKAGE_MANAGERS

STEP_TIMEOUT = 600.0
TAIL_LINES = 40
_LOCKFILES = (
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
    ("pnpm-lock.yaml", "pnpm"),
    ("package-lock.json", "npm"),
)

Runner = Callable[[list[str], Path, Mapping[str, str], float], "tuple[int, str]"]


@dataclass(slots=True)
class Step:
    """One test step and what became of it."""

    name: str
    status: str = "pending"  # passed | failed | skipped
    detail: str = ""
    seconds: float = 0.0
    exit_code: int | None = None
    output: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON view."""
        return {
            "name": self.name, "status": self.status, "detail": self.detail,
            "seconds": round(self.seconds, 2), "exit_code": self.exit_code, "output": self.output,
        }


@dataclass(slots=True)
class GuiTestReport:
    """The outcome of a whole run."""

    steps: list[Step] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when no step failed (skipped steps are not failures)."""
        return not any(step.status == "failed" for step in self.steps)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON view."""
        return {"ok": self.ok, "steps": [step.as_dict() for step in self.steps]}


def detect_package_manager(web: Path, requested: str = "") -> str:
    """Return the package manager of *web*: the requested one, a lockfile's, else npm."""
    if requested:
        return requested
    for name, manager in _LOCKFILES:
        if (web / name).is_file():
            return manager
    return "npm"


def run_command(
    command: list[str], cwd: Path, env: Mapping[str, str], timeout: float
) -> tuple[int, str]:
    """Run *command*; return (exit code, combined output). A timeout is exit code 124."""
    try:
        done = subprocess.run(
            command, cwd=cwd, env=dict(env), capture_output=True, text=True,
            timeout=timeout, check=False, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return 124, f"{out}\ntimed out after {timeout:.0f}s"
    except OSError as exc:
        return 127, str(exc)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def run_gui_tests(
    root: Path,
    *,
    manifest: Mapping[str, Any],
    environment: Mapping[str, Any],
    headed: bool = False,
    install: bool = False,
    pm: str = "",
    runner: Runner = run_command,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] | None = None,
) -> GuiTestReport:
    """Run the GUI tests of the project at *root* and return what happened."""
    report = GuiTestReport()
    report.steps.append(_manifest_step(manifest))
    web = Path(root) / "web"
    if not (web / "package.json").is_file():
        step = Step("web app", "skipped", "no web/package.json: this project has no web app")
        report.steps.append(step)
        return report
    manager = detect_package_manager(web, pm)
    if manager not in PACKAGE_MANAGERS:
        report.steps.append(Step("web app", "failed", f"unknown package manager {manager!r}"))
        return report
    executable = which(manager)
    if executable is None:
        report.steps.append(Step("web app", "skipped", f"{manager} is not installed here"))
        return report
    env = dict(os.environ if environ is None else environ)
    if not (web / "node_modules").is_dir():
        if install:
            _run(report, "install", [executable, "install"], web, env, runner)
        else:
            report.steps.append(
                Step("install", "skipped", f"node_modules is missing: run '{manager} install' "
                     "or pass --install")
            )
            return report
    if not report.ok:
        return report
    _run(report, "typecheck", [executable, "run", "typecheck"], web, env, runner)
    _run(report, "component tests", [executable, "run", "test"], web, env, runner)
    testing = environment.get("testing", {})
    recommended = dict(testing.get("recommended", {}))
    e2e_env = dict(env)
    if recommended.get("PW_CHANNEL"):
        e2e_env.setdefault("PW_CHANNEL", recommended["PW_CHANNEL"])
    if testing.get("headless_browser"):
        e2e_env.pop("PW_HEADED", None)
        _run(report, "e2e (headless)", [executable, "run", "e2e"], web, e2e_env, runner)
    else:
        report.steps.append(
            Step("e2e (headless)", "skipped",
                 "no browser found; run '" + recommended.get("install", "npx playwright install "
                                                              "chromium") + "' first")
        )
    if headed:
        if testing.get("headed_browser"):
            _run(report, "e2e (headed)", [executable, "run", "e2e"],
                 web, {**e2e_env, "PW_HEADED": "1"}, runner)
        else:
            reason = _display_reason(environment)
            report.steps.append(Step("e2e (headed)", "skipped", reason))
    return report


def _display_reason(environment: Mapping[str, Any]) -> str:
    display = environment.get("display", {})
    if not display.get("available"):
        return f"no display here ({display.get('reason', 'unknown')})"
    return "no browser found for a headed run"


def _manifest_step(manifest: Mapping[str, Any]) -> Step:
    issues = list(manifest.get("issues", []))
    count = len(manifest.get("modules", []))
    if issues:
        text = "; ".join(
            f"{i['module']}{'.' + i['panel'] if i.get('panel') else ''}: {i['message']}"
            for i in issues
        )
        return Step("manifest", "failed", text)
    return Step("manifest", "passed", f"{count} module(s) declare a GUI, no issues")


def _run(
    report: GuiTestReport, name: str, command: list[str], cwd: Path, env: Mapping[str, str],
    runner: Runner,
) -> None:
    step = Step(name)
    started = time.monotonic()
    code, output = runner(command, cwd, env, STEP_TIMEOUT)
    step.seconds = time.monotonic() - started
    step.exit_code = code
    step.status = "passed" if code == 0 else "failed"
    lines = output.strip().splitlines()
    step.output = "\n".join(lines[-TAIL_LINES:]) if code != 0 else ""
    step.detail = " ".join(command[1:])
    report.steps.append(step)

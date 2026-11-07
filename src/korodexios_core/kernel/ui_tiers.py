"""GUI tiers and the facts an agent needs to choose one.

A project can have no GUI of its own, a dependency-free static one, or (later) a bundled
single-page app. Which one fits is a decision the person or the AI agent makes, so the
chassis does not guess: it publishes the choices as data (:func:`guide`, ``kdx ui guide
--json``) and the facts about this machine (:func:`probe_environment`, ``kdx ui doctor
--json``) - Node, Bun, a browser, a display - so the choice rests on what can really run
here, including how the GUI can be tested (headless first, with a display when there is one).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

DEFAULT_TIER = "none"
PACKAGE_MANAGERS = ("npm", "bun", "pnpm")
_BROWSERS = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
    "msedge",
    "microsoft-edge",
    "firefox",
)

TIERS: dict[str, dict[str, Any]] = {
    "none": {
        "summary": "No GUI files in the project; the built-in console shows every module.",
        "choose_when": [
            "the project is a daemon, a CLI or an API and people mostly use the terminal",
            "an operator dashboard is enough: tabs, tables, forms and buttons per module",
            "you want the least to maintain: nothing is built, nothing is installed",
        ],
        "avoid_when": ["the product needs its own screens, branding or interaction"],
        "needs": ["python"],
        "dependencies": [],
        "how": (
            "Declare a [ui] table in each module's kdx_module.toml (docs/AGENTS_GUIDE.md "
            "section 8.1) and run 'kdx ui'. Check it with 'kdx ui manifest --json'."
        ),
        "testing": {
            "headless": "pytest: assert 'kdx ui manifest --json' has your panels and no issues",
            "headed": "optional: 'kdx ui' and look at the tab",
        },
    },
    "static": {
        "summary": "Plain HTML, CSS and ES modules in web/; no build step, no Node packages.",
        "choose_when": [
            "a small own page is needed: a status view, a form, a few screens",
            "the host has no Node or Bun, or installing them is not wanted",
            "an AI agent should be able to change the page without a toolchain",
        ],
        "avoid_when": [
            "many interactive screens share state (a framework earns its weight there)",
            "the page needs component libraries from npm",
        ],
        "needs": ["python"],
        "dependencies": [],
        "how": (
            "kdx init NAME --ui static seeds web/ (index.html, src/main.js, src/styles.css) that "
            "talks to the control plane at /api/v1/*. Serve it from a module with "
            "static_dirs() or open it with 'npm --prefix web run dev'."
        ),
        "testing": {
            "headless": "pytest: start the chassis, fetch the page and the API it calls "
            "(urllib); no browser needed",
            "headed": "optional: open the page in a browser to judge layout",
        },
    },
}


def tier_names() -> tuple[str, ...]:
    """Return the tiers a project can be created with."""
    return tuple(TIERS)


def guide() -> dict[str, Any]:
    """Return the decision data an agent reads before choosing a GUI tier."""
    return {
        "default": DEFAULT_TIER,
        "tiers": {name: dict(tier, id=name, init=_init_line(name)) for name, tier in TIERS.items()},
        "package_managers": list(PACKAGE_MANAGERS),
        "decide": [
            "Start from 'none': give each module a [ui] table and look at 'kdx ui'.",
            "Move to 'static' only when the project needs a page of its own.",
            "Run 'kdx ui doctor --json' first: a tier whose tools are missing here is not ready.",
            "Test headless first; run headed checks only when doctor reports a display.",
            "Keep the choice in docs/BRIEF.md so the next agent does not have to guess.",
        ],
        "rules": [
            "The GUI is a client of the control plane (/api/v1/*); it never holds core logic.",
            "Everything the GUI can do must also work from the kdx CLI (terminal first).",
            "A person's GUI choices (labels, hidden panels, form values) live in "
            "'kdx ui prefs', not in the project's code.",
            "UI texts go through the language service; machine output stays English.",
        ],
    }


def _init_line(name: str) -> str:
    return f"kdx init NAME --ui {name}"


def probe_environment(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[[list[str]], str] | None = None,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    exists: Callable[[Path], bool] = Path.exists,
    home: Path | None = None,
) -> dict[str, Any]:
    """Return what this machine can run: runtimes, browsers, a display, ready tiers."""
    env = os.environ if environ is None else environ
    system = sys.platform if platform is None else platform
    runner = run or _version_of
    tools: dict[str, dict[str, Any]] = {}
    for name in ("node", "npm", "bun", "pnpm"):
        path = which(name)
        tools[name] = {
            "found": path is not None,
            "path": path,
            "version": runner([path, "--version"]) if path else None,
        }
    browsers = _browsers(which, env, system, exists, home or Path.home())
    display = _display(env, system)
    playwright = _playwright_cache(env, system, exists, home or Path.home())
    headless = bool(browsers) or playwright
    report: dict[str, Any] = {
        "platform": system,
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "tools": tools,
        "browsers": browsers,
        "playwright_browsers": playwright,
        "display": display,
        "testing": {
            "api_and_html": True,
            "headless_browser": headless,
            "headed_browser": bool(display["available"]) and bool(browsers or playwright),
        },
    }
    report["tiers"] = {
        name: _readiness(name, tool_found={k: v["found"] for k, v in tools.items()})
        for name in TIERS
    }
    return report


def _readiness(name: str, *, tool_found: Mapping[str, bool]) -> dict[str, Any]:
    needs = [tool for tool in TIERS[name]["needs"] if tool != "python" and not tool_found.get(tool)]
    return {"ready": not needs, "missing": needs}


def _version_of(command: list[str]) -> str | None:
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (done.stdout or done.stderr).strip()
    return text.splitlines()[0] if text else None


def _display(env: Mapping[str, str], system: str) -> dict[str, Any]:
    if system.startswith("linux") or system.startswith("freebsd"):
        for key in ("DISPLAY", "WAYLAND_DISPLAY"):
            if env.get(key):
                return {"available": True, "reason": f"{key} is set"}
        return {"available": False, "reason": "no DISPLAY or WAYLAND_DISPLAY"}
    if env.get("SSH_CONNECTION") or env.get("SSH_TTY"):
        return {"available": False, "reason": "this is an SSH session"}
    return {"available": True, "reason": "desktop session"}


def _browsers(
    which: Callable[[str], str | None],
    env: Mapping[str, str],
    system: str,
    exists: Callable[[Path], bool],
    home: Path,
) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for name in _BROWSERS:
        path = which(name)
        if path and path not in seen:
            seen.add(path)
            found.append({"name": name, "path": path})
    extra: list[Path] = []
    if system.startswith("win"):
        for base in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            root = env.get(base)
            if root:
                extra += [
                    Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe",
                    Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                ]
    elif system == "darwin":
        extra += [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
            home / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
        ]
    for candidate in extra:
        if str(candidate) not in seen and exists(candidate):
            seen.add(str(candidate))
            found.append({"name": candidate.stem.lower(), "path": str(candidate)})
    return found


def _playwright_cache(
    env: Mapping[str, str], system: str, exists: Callable[[Path], bool], home: Path
) -> bool:
    configured = env.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured and configured != "0":
        root = Path(configured)
    elif system.startswith("win"):
        root = Path(env.get("LOCALAPPDATA", str(home / "AppData" / "Local"))) / "ms-playwright"
    elif system == "darwin":
        root = home / "Library" / "Caches" / "ms-playwright"
    else:
        root = home / ".cache" / "ms-playwright"
    if not exists(root):
        return False
    try:
        return any(entry.name.startswith("chromium") for entry in root.iterdir())
    except OSError:
        return False

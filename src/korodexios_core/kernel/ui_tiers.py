"""GUI tiers and the facts an agent needs to choose one.

A project can have no GUI of its own, a dependency-free static one, or a bundled
single-page app (``react`` or ``vue``; see :mod:`.ui_tier_data`). Which one fits is a decision
the person or the AI agent makes, so the chassis does not guess: it publishes the choices as
data (:func:`guide`, ``kdx ui guide --json``) and the facts about this machine
(:func:`probe_environment`, ``kdx ui doctor --json``) - Node, Bun, a browser, a display - so
the choice rests on what can really run here, including how the GUI can be tested (headless
first, with a display when there is one).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import ui_embed
from .ui_tier_data import NOTES, STORAGE, TIERS

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
APP_TIERS = ("react", "vue")
"""Tiers that build a bundled app; 'kdx ui add <tier>' adds one to an existing project."""


def tier_names() -> tuple[str, ...]:
    """Return the tiers a project can be created with."""
    return tuple(TIERS)


def guide() -> dict[str, Any]:
    """Return the decision data an agent reads before choosing a GUI tier."""
    return {
        "default": DEFAULT_TIER,
        "tiers": {name: _tier(name, tier) for name, tier in TIERS.items()},
        "package_managers": list(PACKAGE_MANAGERS),
        "notes": [dict(note) for note in NOTES],
        "storage": [dict(item) for item in STORAGE],
        "embed": dict(ui_embed.PROTOCOL),
        "decide": [
            "Start from 'none': give each module a [ui] table and look at 'kdx ui'.",
            "Move to 'static' for one small page of your own, or to 'react' or 'vue' for many "
            "screens with shared state; an existing project adopts one with 'kdx ui add'.",
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


def _tier(name: str, tier: dict[str, Any]) -> dict[str, Any]:
    entry = dict(tier, id=name, init=f"kdx init NAME --ui {name}")
    if name != DEFAULT_TIER:
        entry["add"] = f"kdx ui add {name}"
    return entry


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
    for name in ("node", "npm", "bun", "pnpm", "docker", "podman"):
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
    report["testing"]["recommended"] = _recommended(browsers, playwright, display)
    report["container"] = {
        "inside": _inside_container(env, exists),
        "engines": [name for name in ("docker", "podman") if tools[name]["found"]],
    }
    report["tiers"] = {
        name: _readiness(name, tool_found={k: v["found"] for k, v in tools.items()})
        for name in TIERS
    }
    return report


def _inside_container(env: Mapping[str, str], exists: Callable[[Path], bool]) -> str:
    """Return the kind of container this process runs in ('' when on a plain host).

    Inside a container there is normally no display, so headed tests are skipped by the
    display probe already; this tells an agent *why*, and that the files it writes live in a
    layer unless a volume was mounted.
    """
    if exists(Path("/.dockerenv")):
        return "docker"
    if exists(Path("/run/.containerenv")):
        return "podman"
    if env.get("KUBERNETES_SERVICE_HOST"):
        return "kubernetes"
    return str(env.get("container", ""))


def _recommended(
    browsers: list[dict[str, str]], playwright: bool, display: Mapping[str, Any]
) -> dict[str, str]:
    """Return the Playwright settings that make browser tests work on this machine."""
    advice: dict[str, str] = {}
    channel = ""
    for browser in browsers:
        name = browser["name"]
        if name in ("chrome", "google-chrome", "google-chrome-stable"):
            channel = "chrome"
            break
        if name in ("msedge", "microsoft-edge"):
            channel = channel or "msedge"
    # An installed browser is the reliable choice: Playwright's own cache may hold a Chromium
    # of another revision than the one the project's Playwright wants, which only shows when
    # the first test fails to launch.
    if channel:
        advice["PW_CHANNEL"] = channel
    elif not playwright:
        advice["install"] = "npx playwright install chromium"
    if display["available"] and (playwright or channel):
        advice["PW_HEADED"] = "1"
    return advice


def _readiness(name: str, *, tool_found: Mapping[str, bool]) -> dict[str, Any]:
    missing: list[str] = []
    for need in TIERS[name]["needs"]:
        if need == "python":
            continue
        if need == "node-or-bun":
            # Bun runs Vite itself; Node needs its package manager (npm) beside it.
            if not (tool_found.get("bun") or (tool_found.get("node") and tool_found.get("npm"))):
                missing.append("node (with npm) or bun")
        elif not tool_found.get(need):
            missing.append(need)
    return {"ready": not missing, "missing": missing}


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

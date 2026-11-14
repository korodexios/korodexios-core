"""The app GUI tiers (``react``, ``vue``): a Vite + TypeScript app that draws the module manifest.

``kdx init NAME --ui react|vue`` and ``kdx ui add react|vue`` write ``web/`` plus a tiny module
(``web_app``) that serves the built ``web/dist`` on the control plane at ``/app/``. The files
ship as real sources under ``resources/app/``:

- ``common/`` - shared by every app tier: the framework-neutral client ``src/kdx/`` (API calls,
  the auth rules, the embed handshake, live events, media), its tests, the Playwright
  config and smoke test, the styles and the README;
- ``react/``, ``vue/`` - how that tier draws panels: package.json, build config, components;
- ``module/`` - the ``web_app`` module that serves ``web/dist``.

A tier differs only in how it renders panels; the client, the auth rules and the manifest
contract are one copy, so the tiers and the built-in shell cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .init_project import ProjectTemplate

APP_ROOT = Path(__file__).resolve().parent.parent / "resources" / "app"
APP_TIERS: dict[str, dict[str, str]] = {
    "react": {"framework": "React", "overrides": "src/overrides.tsx", "ext": "tsx"},
    "vue": {"framework": "Vue", "overrides": "src/overrides.ts", "ext": "vue"},
}
"""Tier -> the facts the shared files mention (framework name, where overrides go)."""

PM_COMMANDS: dict[str, dict[str, str]] = {
    "npm": {"install": "npm install", "run": "npm run"},
    "bun": {"install": "bun install", "run": "bun run"},
    "pnpm": {"install": "pnpm install", "run": "pnpm run"},
}
TEMPLATE_SUFFIX = ".template"
PNPM_WORKSPACE = (
    "# pnpm refuses dependency build scripts unless they are allowed. esbuild (used by Vite)\n"
    "# has one: pnpm 11+ reads allowBuilds, pnpm 10 reads onlyBuiltDependencies.\n"
    "allowBuilds:\n"
    "  esbuild: true\n"
    "onlyBuiltDependencies:\n"
    "  - esbuild\n"
)


def _read_tree(root: Path) -> dict[str, str]:
    """Return every file under *root*, keyed by its POSIX path relative to *root*."""
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            name = path.relative_to(root).as_posix().removesuffix(TEMPLATE_SUFFIX)
            files[name] = path.read_text(encoding="utf-8")
    return files


def _tokens(project: ProjectTemplate, tier: str) -> dict[str, str]:
    commands = PM_COMMANDS[project.pm]
    facts = APP_TIERS[tier]
    return {
        "@DIST@": project.distribution,
        "@TITLE@": project.title,
        "@PM@": project.pm,
        "@SCRIPT@": project.script,
        "@INSTALL@": commands["install"],
        "@RUN@": commands["run"],
        "@TIER@": tier,
        "@FRAMEWORK@": facts["framework"],
        "@OVERRIDES@": facts["overrides"],
    }


def app_files(project: ProjectTemplate, tier: str = "") -> dict[str, str]:
    """Return the files of an app tier (``project.ui`` by default), keyed by project path."""
    name = tier or project.ui
    if name not in APP_TIERS:
        raise ValueError(f"{name!r} is not an app tier ({', '.join(APP_TIERS)})")
    tokens = _tokens(project, name)

    def fill(text: str) -> str:
        for token, value in tokens.items():
            text = text.replace(token, value)
        return text

    web = {**_read_tree(APP_ROOT / "common"), **_read_tree(APP_ROOT / name)}
    files = {f"web/{path}": fill(text) for path, text in web.items()}
    if project.pm == "pnpm":
        files["web/pnpm-workspace.yaml"] = PNPM_WORKSPACE
    module_dir = f"src/{project.package}/modules/web_app"
    for path, text in _read_tree(APP_ROOT / "module").items():
        files[f"{module_dir}/{path}"] = fill(text)
    return files


def app_tree_lines(project: ProjectTemplate) -> list[str]:
    """Return the README tree lines that describe an app tier's files."""
    framework = APP_TIERS[project.ui]["framework"]
    return [
        "web/",
        f"  src/                   # {framework} + TypeScript app: draws /api/v1/ui/manifest",
        "  src/kdx/               # shared client: API, auth, embed handshake, live events",
        "  e2e/                   # Playwright tests (headless; PW_HEADED=1 for a window)",
        "  README.md              # commands for the chosen package manager",
        "  modules/web_app/       # serves web/dist at /app/ once built",
    ]


def app_skill(project: ProjectTemplate) -> tuple[str, list[str]]:
    """Return the summary and body of the seeded ``web-ui`` skill for an app tier."""
    facts = APP_TIERS[project.ui]
    run = PM_COMMANDS[project.pm]["run"]
    return (
        f"Extend the {project.title} {facts['framework']} app without breaking the contract.",
        [
            "## Contract",
            "- The app is a client of @BT@/api/v1/*@BT@; it holds no core logic.",
            f"- A module gets a tab by declaring a @BT@[ui]@BT@ table; no {facts['framework']}"
            " is needed.",
            "- Per-person state (labels, hidden panels, form values) lives in",
            "  @BT@/api/v1/ui/prefs@BT@.",
            "- Auth, the embed handshake and live events live in @BT@web/src/kdx/@BT@ (shared",
            "  by every app tier); change them there only together with the core.",
            "- The token travels in the URL fragment or the sign-in form, never in committed code.",
            "",
            "## Steps",
            f"1. @BT@{project.script} ui manifest --json@BT@ shows what the app will draw.",
            f"2. @BT@{run} dev@BT@ in web/ while @BT@{project.script} run@BT@ is running.",
            f"3. @BT@{project.script} ui types --out web/src/kdx/schema.ts@BT@ after a route",
            "   changes.",
            f"4. @BT@{run} typecheck && {run} test@BT@ before every commit.",
            f"5. @BT@{run} e2e@BT@ for the real daemon; @BT@PW_HEADED=1@BT@ only when",
            "   @BT@kdx ui doctor@BT@ reports a display.",
            f"6. Draw one panel yourself with @BT@overridePanel()@BT@ in web/{facts['overrides']}.",
        ],
    )


__all__ = ["APP_ROOT", "APP_TIERS", "PM_COMMANDS", "app_files", "app_skill", "app_tree_lines"]

"""What a GUI tier is, when to choose it, and the known limits: data for ``kdx ui guide``.

Kept apart from :mod:`.ui_tiers`, which probes the machine; this module only describes. A note's
``applies_to`` is a tier, ``app`` (every app tier: react, vue) or ``container``.
"""

from __future__ import annotations

from typing import Any

from .i18n import N_

TIERS: dict[str, dict[str, Any]] = {
    "none": {
        "summary": N_("No GUI files in the project; the built-in console shows every module."),
        "choose_when": [
            N_("the project is a daemon, a CLI or an API and people mostly use the terminal"),
            N_("an operator dashboard is enough: tabs, tables, forms and buttons per module"),
            N_("you want the least to maintain: nothing is built, nothing is installed"),
        ],
        "avoid_when": [N_("the product needs its own screens, branding or interaction")],
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
        "summary": N_("Plain HTML, CSS and ES modules in web/; no build step, no Node packages."),
        "choose_when": [
            N_("a small own page is needed: a status view, a form, a few screens"),
            N_("the host has no Node or Bun, or installing them is not wanted"),
            N_("an AI agent should be able to change the page without a toolchain"),
        ],
        "avoid_when": [
            N_("many interactive screens share state (a framework earns its weight there)"),
            N_("the page needs component libraries from npm"),
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
    "react": {
        "summary": N_("Vite + React + TypeScript app in web/ that draws the module manifest."),
        "choose_when": [
            N_("the product needs its own screens, many interactive views or shared state"),
            N_("agents and developers should work with the most widely known UI stack"),
            N_("you want component tests (Vitest) and end-to-end tests (Playwright)"),
        ],
        "avoid_when": [
            N_("a module [ui] table or one static page is enough (less to build and maintain)"),
            N_("neither Node nor Bun can be installed on the build host"),
        ],
        "needs": ["node-or-bun"],
        "dependencies": ["react", "react-dom", "vite", "typescript", "vitest", "playwright"],
        "how": (
            "kdx init NAME --ui react [--pm npm|bun|pnpm] (or 'kdx ui add react' in an existing "
            "project) seeds web/ and the web_app module that serves web/dist at /app/. The app "
            "draws /api/v1/ui/manifest with the shared client web/src/kdx/ and honours "
            "/api/v1/ui/prefs; replace a panel with overridePanel() in web/src/overrides.tsx."
        ),
        "testing": {
            "headless": "vitest run (components, jsdom) and playwright test (real daemon, "
            "headless Chromium; PW_CHANNEL=chrome uses an installed browser)",
            "headed": "PW_HEADED=1 playwright test when 'kdx ui doctor' reports a display",
        },
    },
    "vue": {
        "summary": N_("Vite + Vue 3 + TypeScript app in web/ that draws the module manifest."),
        "choose_when": [
            N_("the product needs its own screens and the team prefers Vue's single-file "
               "components"),
            N_("you want component tests (Vitest + @vue/test-utils) and end-to-end tests "
               "(Playwright)"),
        ],
        "avoid_when": [
            N_("a module [ui] table or one static page is enough (less to build and maintain)"),
            N_("neither Node nor Bun can be installed on the build host"),
        ],
        "needs": ["node-or-bun"],
        "dependencies": ["vue", "vite", "typescript", "vue-tsc", "vitest", "@vue/test-utils",
                         "playwright"],
        "how": (
            "kdx init NAME --ui vue [--pm npm|bun|pnpm] (or 'kdx ui add vue' in an existing "
            "project) seeds web/ and the web_app module that serves web/dist at /app/. Same "
            "contract and shared client (web/src/kdx/) as react; replace a panel with "
            "overridePanel() in web/src/overrides.ts."
        ),
        "testing": {
            "headless": "vitest run (components, jsdom) and playwright test (real daemon, "
            "headless Chromium; PW_CHANNEL=chrome uses an installed browser)",
            "headed": "PW_HEADED=1 playwright test when 'kdx ui doctor' reports a display",
        },
    },
}


NOTES: list[dict[str, str]] = [
    {
        "id": "react-prefs-read-mostly",
        "applies_to": "app",
        "text": N_("An app tier (react, vue) reads the saved GUI choices (labels, hidden panels, "
        "order, last tab) and writes remembered form values and pinned defaults. Renaming, hiding, "
        "reordering and own shortcut buttons are edited in the built-in console ('kdx ui', "
        "Customize) or with 'kdx ui prefs patch'; the app then shows them."),
    },
    {
        "id": "package-managers",
        "applies_to": "app",
        "text": N_("npm, pnpm 12 and bun 1.4 were each verified by hand on Linux with both app "
        "tiers (install, typecheck, component tests); npm also end to end. The chassis tests "
        "run none of them, so run '<pm> run typecheck && <pm> run test' first. With pnpm the "
        "seeded web/pnpm-workspace.yaml allows esbuild's build script (newer pnpm refuses it)."),
    },
    {
        "id": "npm-install-scripts",
        "applies_to": "app",
        "text": N_("Newer npm versions may block the esbuild install script. The build normally "
        "works anyway; if vite fails with an esbuild error, approve it ('npm install-scripts "
        "approve esbuild') and reinstall."),
    },
    {
        "id": "e2e-needs-project-env",
        "applies_to": "app",
        "text": N_("'<pm> run e2e' starts the project's own daemon with 'uv run <script> run': run "
        "'uv sync --extra dev' in the project first, so the chassis dependency resolves."),
    },
    {
        "id": "browser-for-e2e",
        "applies_to": "app",
        "text": N_("Playwright needs a browser: 'kdx ui doctor' prints the recommended setting "
        "(PW_CHANNEL=chrome|msedge for an installed browser, or 'npx playwright install "
        "chromium'). Headed runs (PW_HEADED=1) need a display; doctor says if there is one."),
    },
    {
        "id": "container-engines",
        "applies_to": "container",
        "text": N_("The container files are verified with Docker running Linux containers: "
        "on Debian "
        "and with Docker Desktop on Windows (Linux engine), and with rootless Podman 5.4 on "
        "Debian 13 inside an LXC (build and run with --network=host, ids from 'kdx container "
        "fix'). Podman compose providers were not tried; Windows containers (the Windows "
        "engine) are not supported - the image is Linux. The build stage needs network "
        "access (package index, and git for the default git chassis source)."),
    },
    {
        "id": "container-state",
        "applies_to": "container",
        "text": N_("State lives in the app-var and app-data volumes: 'docker compose down -v' "
        "deletes "
        "it. The control plane needs KDX_TOKEN and is published on 127.0.0.1 only; put TLS in "
        "front of it before widening the bind."),
    },
    {
        "id": "static-is-not-served",
        "applies_to": "static",
        "text": N_("The static tier's web/ is not served by the chassis on its own. Serve it with "
        "a module's static_dirs() or 'npm --prefix web run dev'."),
    },
]
"""Known limits of each tier, shown by 'kdx ui guide' so an agent plans around them."""


STORAGE: list[dict[str, str]] = [
    {
        "need": "settings an operator changes (intervals, flags, URLs)",
        "use": "a config key in kdx.yml (ConfigField); no database",
    },
    {
        "need": "what a person chose in the GUI (labels, hidden panels, form values)",
        "use": "the GUI preferences ('kdx ui prefs'); no database",
    },
    {
        "need": "a small state written by one process (a counter, a cursor, a last-run time)",
        "use": "a JSON file in context.durable_module_dir(name); no database",
    },
    {
        "need": "work that must survive a restart (jobs, retries)",
        "use": "the durable queue: context.job_queue(); no database of your own",
    },
    {
        "need": "relational or large data: many rows, queries, several writers, history",
        "use": "context.db(name): SQLite in data/db/, ordered migrations; inspect with 'kdx db'",
    },
]
"""Where each kind of state belongs; a database is the answer only for the last row."""

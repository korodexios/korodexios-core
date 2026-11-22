# CLAUDE.md - korodexios-core

@AGENTS.md

Everything in AGENTS.md (commands, layout, rules, verification) applies. This file
adds what AGENTS.md does not say: project state and working preferences.

## Project in one line

Headless microkernel chassis that seeds every Korodexios project. Python >= 3.11,
runtime deps only PyYAML + rich. Console entry points: `kdx` (primary) and `korodexios`.
Version lives in `src/korodexios_core/version.py` (single source of truth); current: 0.8.1.

## Working preferences

- **Terminal first.** Every capability must be fully usable from the `kdx` CLI (with
  `--json` where it fits). The web console (`kdx ui`, `modules/web_console`) is only
  an add-on layer, never the only way to do something. Audit new work for GUI-only
  paths before calling it done.
- **No TUI.** A Textual/TUI front end is explicitly not wanted.
- **Language (this project only).** The owner writes in Slovak; reply in Slovak. Every file
  of the project is English, with no exception: code, diagnostics, machine payloads, CLI text,
  public docs and the internal notes in `docs-dev/` (git-ignored, never published; translated
  from Slovak on 2026-11-14). Slovak lives only in the translation catalogues and other
  deliberate i18n content (installer texts, language names, test data). Do not copy this to
  other projects.
- **i18n (phase 36, merged into `main`).** The core has a
  language service: `kernel/i18n.py` (stdlib only, reads `.po` directly, no compile step),
  config key `core.locale` (`--locale`, `KDX_CORE__LOCALE`, `kdx.local.yml`), catalogues in
  `src/korodexios_core/locales/` (`korodexios.pot`; sk, cs, de, es, fr, pl - the non-Slovak
  ones are machine translations nobody native has reviewed), CLI `kdx locale
  show|list|set|check`. UI strings go through `context.translate()`; stderr diagnostics,
  logs and `--json` stay English. Modules/projects add a domain with
  `context.i18n.register(domain, dir)`. New languages: `docs/TRANSLATING.md` (`kdx locale add|todo|apply`). Web console: domain `korodexios-web` (`modules/web_console/locales/`), route `/api/v1/ui/i18n`, language selector in the top bar; dynamic JS texts with values stay English. `KDX_LOCALE` maps to `core.locale`; seeded `i18n.py` uses the service.

- **i18n workflow.** Rendering helpers (`print_section`, key labels, table headers,
  static `write_line`/`write_status`, command/argument `help=`) are translated centrally
  by `render.set_translator`; dynamic text needs `_t(request, "... {field}", field=x)`.
  After adding or changing UI text run `kdx locale extract`, then fill the new `msgstr`
  lines in `locales/sk/korodexios.po`. `make check` fails (tests) when the template is
  stale or `sk` is incomplete.
- **What gets translated.** Only what the user reads: help text, status messages, table
  and key labels. Command and flag names (`kdx status`, `--json`, `locale set`) stay
  English in every language, so scripts, docs and support look the same everywhere.
  Never translate `--json` keys/values, logs, stderr diagnostics, exit codes, the
  `[✓] [?] [!] [i]` tags, or config keys.

## Ask first (outward-facing / hard to reverse)

- No `git push`, no tag (e.g. `v0.5.3`), no merge to `main`: the owner decides.
- Never run `kdx service install` (registers a logon task on the owner's machine);
  offer it, do not do it unasked.
- Never commit control-plane users or the audit log (see commit f78f747); keep
  `var/` and runtime state out of git.

## Current state (2026-11-22)

- v0.8.1 (owner asked for version, commit and push; branch `feature/mcp-command-roles`): downstream
  proposal 17 from assay-wiki. MCP tools need the same role as the command on rpc
  (`kernel/mcp.py` `required_role` and `command_tools` start from `viewer`): `CliCommand.role` can
  lower a tool below operator, a group is a floor (an unmarked group is an operator floor, so a
  project marks the group too), `danger` stays admin, `modules.mcp.role` stays the ceiling. A test
  checks MCP and `chassis._required_role` agree. `make check` passed on Linux.

## State on 2026-11-19

- v0.8.0 (owner asked for merge, commit and push from the Linux dev host `kleo@10.50.50.3`; branch
  `feature/module-scopes-reload`): module scopes (`kernel/scope.py`, `context.scope(name)`: subscribe,
  schedule, routes, mounts, `on_dispose` undone newest first after the module's `stop()`; used by
  `metrics`, `backup_engine`, `heartbeat`, `job_queue`, `webhooks`) and `kdx module-reload NAME
  [--cascade]` / `POST /api/v1/modules/reload` (module `module_admin`, `kernel/module_reload.py`:
  prepare first, swap second, roll back on failure; a rename or changed `depends_on` needs a
  restart; modules register through a scope in `start()`). Verified on Linux with `make check`
  and against a real daemon; found there: the bytecode cache served the old code for a file
  rewritten in the same second, so reload drops it. Lesson again: test on Linux before a release.

## State on 2026-11-14

- v0.7.4 (owner asked for commit, merge, tag and push; branch `feature/post-073-cleanup`):
  the rest of the 2026-11-08 review (V2 typed `config show --json`, V3 dynamic imports in the
  rules test, V4 coverage floor 90, V7 Windows console close/logoff/shutdown, V9 bounded process
  end in `kernel/process_group.py`, V10 queue `fail()` atomic, V11 no double task run, V13
  Windows blocking lock), `kdx ui guide` and `kdx doctor` translated (`kernel.i18n.N_`, core-only
  words through `scan_tree(domain=)`), shell stream/media fixes. Windows-only code (V7, V13) is
  type-checked for win32 but not run on Windows. The repo has a local git identity
  "Korodexios Engineering" (`.git/config`); the global one holds the owner's personal e-mail.
  Never `git checkout` the locales folder to undo an extract: it discards the branch's translations.

- v0.7.3 (owner asked for tag, commit and push; branch `feature/app-client-vue` merged into `main`,
  ROADMAP phase 41): downstream proposal 006, only the general parts. Tier `vue`; the shared client
  `web/src/kdx/` (templates are real files in `resources/app/{common,react,vue,module}`, generator
  `cli/ui_app.py`, `ui_react.py` removed); `kdx ui add <tier>` (`cli/ui_add.py`); `kdx ui types`
  (`cli/ui_types.py`); embed protocol v1 with `cookie` (`kernel/ui_embed.py`); manifest `refresh_on`
  and cell kind `media` (`kernel/ui_cells.py`); shell sends no empty header and retries once after a
  401 in cookie mode. Verified: `make check` steps, both app tiers built and tested in `node:22`
  Docker, and headless Chromium (Playwright Docker image) against a scratch daemon in cookie and
  header modes. `kdx ui add` + `kdx ui test --install` pass for both app tiers with npm, pnpm 12 and
  bun 1.4 (pnpm needed the seeded `web/pnpm-workspace.yaml` allowing esbuild). This Linux host now
  has, in user space without sudo: Node 22 (`~/.local/opt/node`), pnpm via corepack, bun
  (`~/.local/opt/bun`), GNU make (unpacked .deb in `~/.local/opt/make`), all linked in
  `~/.local/bin`. The owner installed Chromium's system libraries (apt, 2026-11-14), so Playwright's
  own Chromium (`~/.cache/ms-playwright`) runs headless here; `kdx ui test` e2e passed for vue/npm
  and react/pnpm. Dev machine setup (internal, git-ignored): `docs-dev/DEVELOPMENT.md`.
  `reference/` is the original dev checkout, kept only until the owner deletes it (git-ignored,
  unused by tests). Everything it had that this checkout lacked - local tags never pushed
  (v0.1.0-v0.3.2, v0.3.1-diag, v0.5.6) - was copied here as local tags.

## State on 2026-11-11

- v0.7.2 (owner asked for tag, commit and push; from a downstream proposal N4/N5): the console remembers its
  token by server policy (`core.ipc.ui_token_store|ui_token_ttl|ui_token_cookie`, default `always` in
  `localStorage`; `token-store.js` shared by both consoles; `/api/v1/ui/token-policy` and `/ui/session`
  in `modules/web_console/token_session.py`), and `reload` applies `core.ipc.allowed_hosts` and
  the cookie option live (`kernel/ipc_hosts.py`, `reload.apply_live_ipc`); a machine's own host names stay
  valid when allowed_hosts is set. Not verified in a real browser, only by tests and Python-level HTTP calls.

## State on 2026-11-10

- v0.7.1 is tagged and pushed (narrow shell: ribbon on the left, side list as a drawer; classic-console links fixed). v0.7.0 (tagged, pushed): one front end, from a downstream proposal:
  `/ui` redirects to `/ui/` (301); `Range`/206/416 on static mounts and `IpcResponse.file(range_header=)`
  (`kernel/http_files.py`); panel type `embed` (`kernel/ui_embed.py`, `renderEmbed` in `shell.js`, token
  by `postMessage`, mounts opt in with `embeddable=True`); `/ui/` serves the shell, the classic console
  is `console.html`, config key `modules.web_console.frontend`, `kdx ui --console`; the shell follows the
  system theme by default. Size debt went down, not up. Verified in the browser pane on a scratch
  daemon (light and dark); not yet against a real project.

## State on 2026-11-08

- v0.6.9 is tagged and merged into `main` (owner asked for the version, tag and push). It only fixes the CI that was red on Linux/macOS since 0.6.7 (so 0.6.7 and 0.6.8 have tags but no release artifacts): `ctypes.WinDLL` behind a platform check, and the seeded size-ratchet test without stale bytecode. Lesson: run `mypy` and the tests on the Linux test host before a release, not only on Windows. v0.6.8 held the rest of the review of 2026-11-08: K1 `docs check` exits 1, R5 one-byte Windows lock, R6 wait for requests on stop, second signal forces exit, S4 secrets owner-only before they are written, I6 global help and the interactive layer translated, D/X/V items. The items left open then (V2, V3, V4, V7, V9-V11, V13, guide texts, doctor labels) were done on 2026-11-14, branch `feature/post-073-cleanup`. v0.6.7 held the review's phases 1-5 and a downstream proposal done in full. Size numbers in the conventions are triggers for a decision, not caps (split by job, never by line); `kdx docs check|update`, `kdx conventions|playbook --check` (exit 1 + diff since 0.6.8, `cli/docs_sync.py`); `kdx init` seeds `make check-docs`, a CI step and `tests/test_size_ratchet.py` (baselines at zero; debt may only shrink). Same afternoon: `kdx init` also seeds `tests/test_rules.py` (no cross-module imports, no stubs), a coverage floor, pip-audit, pre-commit (`cli/seed_quality.py`). The core runs both tests on itself (`tests/test_rules.py`, `tests/test_size_ratchet.py`; recorded debt 33 files / 130 functions / 28 signatures, may only shrink: lower the number when you clean something, never raise it).

## State on 2026-11-07

- v0.6.6 (owner asked for commit, tag and push): shell panels may use `{node}`, tree answers page (`more` node, `page_nodes`), long values fold in detail views (`detail.kinds`), phone layout under 700 px; fixed the text-column cell crash. Verified in the browser pane on a scratch module only.
- v0.6.5 (owner asked for version, tag and push): shell completed (palette, shortcut sheet, split panes, language, neutral icons). v0.6.4 (pushed): the one-window shell. Backend:
  `kernel/ui_theme.py` (`--kdx-*` tokens, aliases for older names, `.css`/`.json` themes from
  `<root>/themes/` and `data/ui/themes/`, WCAG check), `kernel/ui_tree.py` (node shape of a tree
  route), prefs `layout`, manifest `position`/`badge`/`[[ui.sections]]`/`dock`/`locked`; CLI
  `kdx ui layout|theme|--shell`. Front end: `modules/web_console/static/shell.{html,js,css}`
  draws every panel type (the classic console stays at `/ui/`); later added: command palette,
  shortcut sheet, split panes, translated texts. Principle: the core ships universal tools and names
  no project; projects adapt it themselves. Never tried against a real project, only a demo module.
- v0.6.3 (tagged): settings editor (`kdx config set|unset|schema`, `config/editor.py`,
  `cli/config_edit.py`, `/api/v1/ui/config/schema` and `/edit`, `settings` panel), table typed
  cells / row actions / detail, form `fields` (`choices_from`, textarea, slider). From a proposal
  sent by a downstream project; items 4-8 (status strip, upload and Range, theme tokens,
  event-driven panels, `embed`) were deliberately left out (theme tokens came in 0.6.4).

- `main` is at v0.6.2 (the owner asked for merge, tag and push): the GUI manifest (`[ui]` in
  `kdx_module.toml`, `/api/v1/ui/manifest`) with per-user remembered choices (`kdx ui prefs`), GUI tiers
  (`kdx ui guide|doctor|test`, `kdx init --ui none|static|react --pm`), the opt-in database
  (`context.db`, `kdx db`), containers (`kdx init --container`, `kdx container doctor|fix|env`,
  `scripts/distro-check.sh`, CI `distros` job) and README/agent setup docs. Verified on the Linux
  test host (see memory `test-container-198`): Docker, rootless Podman in the LXC, seven distribution
  images; Windows containers are not supported (Docker Desktop here has them disabled). CI on GitHub
  was not run for the new `distros` job before the tag.
- Working rules learned: never kill processes or containers on the test host (only your own);
  `kdx container fix` is the only thing that touches system ids and it always asks first.

## Earlier on 2026-11-06 (v0.6.1)

- v0.6.1 (tagged, pushed): v0.6.0 plus the Linux mypy fix and the wiki in the
  LLM-wiki layout (`raw/`, `SCHEMA.md`, `index.md`, `log.md`, `[[wikilinks]]`, `kdx wiki ingest|log`,
  `kdx wiki export` into a user's own wiki). Tag `v0.6.0` points at a commit whose CI failed
  (no release from it). Linux test host: see memory `test-container-198`.

## Earlier on 2026-11-06

- Branch `feature/agentic-integrations` (not merged, not pushed, not tagged) holds v0.6.0: the
  November audit fixes plus the agentic surface - modules `mcp` (`kdx mcp serve|tools|config`,
  `POST /api/v1/mcp`), `webhooks` (signed outbound, `/api/v1/webhooks/inbound`), `realtime`
  (WebSocket `/api/v1/ws`, `kernel/websocket.py`), Bearer tokens, `kdx agents sync`, playbook
  sections 10-12, `docs/INTEGRATIONS.md`, and modules `metrics` (`/metrics`, `kdx metrics`,
  `context.metrics`), `wiki` (`kdx wiki`, public `docs/wiki/`, internal `docs-dev/wiki/`), plus
  `kdx proposal show|export|send` with `CONTRIBUTING.md` and the issue form. The owner decides
  merge, tag and push.
- `main` carries the v0.5.6 release commit, not yet pushed (tag `v0.5.6` exists only locally).

## Earlier state (2026-11-03)

- `main` is at v0.5.2 (tagged); every earlier feature/review branch is merged into it.
- v0.5.0 adds, inside core API v2: scheduler `jitter`/`window`/`days` and `schedule_at`,
  `context.limiter` + `kdx limiter`, `context.run_process`, the durable queue
  (`context.job_queue`, module `job_queue`, `kdx queue`, `/api/v1/queue`), and the `kdx init`
  pyproject fix (`allow-direct-references`). The owner asked for commit, tag and push of it.
- Still open from the owner's request list: publishing to PyPI (owner's account, not done).
- v0.4.2 held the fixes of `docs-dev/REVIEW-2026-11.md` and the public caller API
  (`Caller`, `current_caller`, `IpcRequest.caller`, `CliRequest.caller`, jobs keep the caller).
- Review documents: `docs-dev/REVIEW-2026-10.md`, `docs-dev/REVIEW-2026-11.md`,
  `docs-dev/REVIEW-2026-11-08.md` (English since 2026-11-14). Other docs: `docs/ARCHITECTURE.md`,
  `CLI_REFERENCE.md`, `ROADMAP.md`, `TUTORIAL.md`, `AGENTS_GUIDE.md`.

## Environment notes

- Host is Windows 11. If `make` is unavailable, run the same steps through uv:
  `uv run ruff check src tests`, `uv run mypy`, `uv run pytest -q`.
  `windows-check.ps1` is the Windows verification script.
- Docs or help that mention the CLI lead with `kdx`, not `korodexios`.

# CLAUDE.md - korodexios-core

@AGENTS.md

Everything in AGENTS.md (commands, layout, rules, verification) applies. This file
adds what AGENTS.md does not say: project state and working preferences.

## Project in one line

Headless microkernel chassis that seeds every Korodexios project. Python >= 3.11,
runtime deps only PyYAML + rich. Console entry points: `kdx` (primary) and `korodexios`.
Version lives in `src/korodexios_core/version.py` (single source of truth); current: 0.6.7.

## Working preferences

- **Terminal first.** Every capability must be fully usable from the `kdx` CLI (with
  `--json` where it fits). The web console (`kdx ui`, `modules/web_console`) is only
  an add-on layer, never the only way to do something. Audit new work for GUI-only
  paths before calling it done.
- **No TUI.** A Textual/TUI front end is explicitly not wanted.
- **Language (this project only).** The owner writes in Slovak; reply in Slovak. The
  chassis itself is English-only (diagnostics, machine payloads, CLI text, docs), except
  the internal Slovak notes in `docs-dev/` (git-ignored, never published). Do not copy this to other projects.
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

## Current state (2026-11-08)

- v0.6.7 (owner asked for commit, version bump, tag and push; not merged into `main`, branch `feature/review-2026-11-08-fixes`): the review of 2026-11-08 fixed (B1-B5, S1, S5, R1-R4, K2-K4, I1-I5, T1-T3, part of D/X; left: K1, B6 release pin check, I6, R5, R6, S4, Q1, Q2, D9, D4, D7, D8, D11, D12, X3, X5, X6), and a downstream proposal done in full. Size numbers in the conventions are triggers for a decision, not caps (split by job, never by line); `kdx docs check|update`, `kdx conventions|playbook --check` (exit 3 + diff, `cli/docs_sync.py`); `kdx init` seeds `make check-docs`, a CI step and `tests/test_size_ratchet.py` (baselines at zero; debt may only shrink). Same afternoon: `kdx init` also seeds `tests/test_rules.py` (no cross-module imports, no stubs), a coverage floor, pip-audit, pre-commit (`cli/seed_quality.py`). The core runs both tests on itself (`tests/test_rules.py`, `tests/test_size_ratchet.py`; recorded debt 33 files / 132 functions / 29 signatures, may only shrink: lower the number when you clean something, never raise it).

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
- v0.4.2 held the fixes of `docs-dev/REVIEW-2026-11.sk.md` and the public caller API
  (`Caller`, `current_caller`, `IpcRequest.caller`, `CliRequest.caller`, jobs keep the caller).
- Review documents: `docs-dev/REVIEW-2026-10.sk.md`, `docs-dev/REVIEW-2026-11.sk.md` (both
  Slovak, the only exceptions to the English-only rule). Other docs: `docs/ARCHITECTURE.md`,
  `CLI_REFERENCE.md`, `ROADMAP.md`, `TUTORIAL.md`, `AGENTS_GUIDE.md`.

## Environment notes

- Host is Windows 11. If `make` is unavailable, run the same steps through uv:
  `uv run ruff check src tests`, `uv run mypy`, `uv run pytest -q`.
  `windows-check.ps1` is the Windows verification script.
- Docs or help that mention the CLI lead with `kdx`, not `korodexios`.

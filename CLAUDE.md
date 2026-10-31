# CLAUDE.md - korodexios-core

@AGENTS.md

Everything in AGENTS.md (commands, layout, rules, verification) applies. This file
adds what AGENTS.md does not say: project state and working preferences.

## Project in one line

Headless microkernel chassis that seeds every Korodexios project. Python >= 3.11,
runtime deps only PyYAML + rich. Console entry points: `kdx` (primary) and `korodexios`.
Version lives in `src/korodexios_core/version.py` (single source of truth); current: 0.4.0.

## Working preferences

- **Terminal first.** Every capability must be fully usable from the `kdx` CLI (with
  `--json` where it fits). The web console (`kdx ui`, `modules/web_console`) is only
  an add-on layer, never the only way to do something. Audit new work for GUI-only
  paths before calling it done.
- **No TUI.** A Textual/TUI front end is explicitly not wanted.
- **Language (this project only).** The owner writes in Slovak; reply in Slovak. The
  chassis itself is English-only (diagnostics, machine payloads, CLI text, docs), except
  `docs/REVIEW-2026-10.sk.md`. Do not copy this to other projects.
- **i18n (phase 36, branch `feature/i18n-service`, committed locally, not pushed).** The core has a
  language service: `kernel/i18n.py` (stdlib only, reads `.po` directly, no compile step),
  config key `core.locale` (`--locale`, `KDX_CORE__LOCALE`, `kdx.local.yml`), catalogues in
  `src/korodexios_core/locales/` (`korodexios.pot`; sk, cs, de, es, fr, pl - the non-Slovak
  ones are machine translations nobody native has reviewed), CLI `kdx locale
  show|list|set|check`. UI strings go through `context.translate()`; stderr diagnostics,
  logs and `--json` stay English. Modules/projects add a domain with
  `context.i18n.register(domain, dir)`. New languages: `docs/TRANSLATING.md` (`kdx locale add|todo|apply`). Still open: web console switcher (`KDX_LOCALE` now maps to `core.locale`).

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

- No `git push`, no tag (e.g. `v0.4.0`), no merge to `main`: the owner decides.
- Never run `kdx service install` (registers a logon task on the owner's machine);
  offer it, do not do it unasked.
- Never commit control-plane users or the audit log (see commit f78f747); keep
  `var/` and runtime state out of git.

## Current state (2026-10-31)

- Branch `review/security-web-console`; steps done: users/roles + audit (phase 33),
  terminal jobs/events/stop/cancel (34), `kdx service` (35), release commit v0.4.0.
- Review document: `docs/REVIEW-2026-10.sk.md`. Other docs: `docs/ARCHITECTURE.md`,
  `CLI_REFERENCE.md`, `ROADMAP.md`, `TUTORIAL.md`, `AGENTS_GUIDE.md`.

## Environment notes

- Host is Windows 11. If `make` is unavailable, run the same steps through uv:
  `uv run ruff check src tests`, `uv run mypy`, `uv run pytest -q`.
  `windows-check.ps1` is the Windows verification script.
- Docs or help that mention the CLI lead with `kdx`, not `korodexios`.

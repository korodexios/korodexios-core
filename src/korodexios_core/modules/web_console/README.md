# web_console

A browser GUI served by the daemon itself. It is a thin client like the CLI: it reads the
core's routes and runs module commands through `POST /api/v1/rpc`, so it never holds logic
of its own. Built-in; discovered from `korodexios_core/modules/web_console/`.

## Contract

- Static mount: `/ui/` serves `static/` (`index.html`, `app.js`, `app.css`). The files hold
  no secret, so the mount needs no token; a strict Content-Security-Policy allows only the
  page's own scripts and same-origin requests, and every daemon value is written as text.
- Routes (token required):
  - `GET /api/v1/ui/catalog` - every runnable module command with its declared
    `CliArgument`s; the page builds a form per command from it.
  - `GET /api/v1/ui/logs?lines=N` - the last `N` lines (1..5000) of the log file.
  - `GET /api/v1/ui/config` - every resolved key with its layer; secrets masked.
  - `GET /api/v1/ui/config/schema` - the declared settings with value and layer; the
    `settings` panel types its form from it.
  - `POST /api/v1/ui/config/edit` (admin, audited) - change the local layer
    (`kdx.local.yml`) and reload; the same code as `kdx config set|unset`.
  - `GET /api/v1/ui/audit` (admin) - the newest audit records.
  - `GET /api/v1/ui/events` - bus events as server-sent events (`IpcStream`); the page
    reads it with `fetch`, so the token travels in a header, never in a URL. The terminal
    follows the core route `GET /api/v1/events` instead (`kdx events`).
  - `GET /api/v1/ui/theme` - the active theme with its tokens and CSS, and the themes on offer.
  - `GET /api/v1/ui/i18n` - the shell's texts in the caller's language (domain `korodexios-web`).
- Module panels: `GET /api/v1/ui/manifest?locale=` returns the GUI every loaded module
  declares in its `[ui]` table (or `Module.ui_spec()`), validated by
  `kernel/ui_manifest.py`; `static/modules.js` draws one tab per module (table, keyvalue,
  log, text, form, actions panels) with no per-module code. Bad panels are dropped and
  listed under `issues`.
- Preferences: `GET|PUT|PATCH|DELETE /api/v1/ui/prefs` keeps the caller's own choices
  (rename/hide/reorder, own shortcut buttons, remembered and pinned form values, last tab) in
  `data/ui/prefs-<user>.json` (`kernel/ui_prefs.py`), edited from the terminal with
  `kdx ui prefs show|patch|reset`.
- Commands run as background jobs (`"background": true` on `/api/v1/rpc`, then
  `GET /api/v1/jobs?id=`), so output appears while it grows. The catalog carries each
  command's `remote` and `danger` flags: a `danger` command needs a second click, a
  `remote=False` one is shown as terminal-only.
- CLI contributed: `kdx ui [--no-browser] [--shell|--console]`, `kdx ui manifest [--json]`,
  `kdx ui doctor|test|guide`, `kdx ui prefs show|patch|reset`, `kdx ui layout show|set|reset`
  and `kdx ui theme list|show|set|check`. `/ui/` is the one-window shell (ribbon, side
  list, panels, split panes); the classic console is `/ui/console.html` and `modules.web_console.frontend`
  (`shell` | `console`) picks which one `/ui/` serves. `--shell` and `--console` open either by name. With a live daemon it opens the page; with
  none it boots the chassis in the foreground and opens the console once the control plane
  is up. The token rides in the URL fragment (`/ui/#token=...`), which a browser never
  sends to a server; the page moves it into `sessionStorage` and clears the address bar.
- Manifest: `kdx_module.toml` (`priority = 90`, `entrypoint = "MODULE"`).

A new module needs no GUI code: its `register_cli` commands appear in the console with a
form per command. A command hidden from the wizard (`show_in_wizard=False`) is hidden here
too.

## Configuration keys (`modules.web_console.*`)

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | gate registration at discovery |
| `open_browser` | `true` | `kdx ui` opens a browser; `false` only prints the URL |

## How to test

`pytest -q tests/test_web_console.py` covers the catalog, the routes, the served page, and
both `kdx ui` paths; the live check is `kdx ui`.

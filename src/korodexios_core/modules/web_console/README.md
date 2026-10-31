# web_console

A browser GUI served by the daemon itself. It is a thin client like the CLI: it reads the
core's routes and runs module commands through `POST /api/v1/rpc`, so it never holds logic
of its own. Built-in; discovered from `korodexios_core/modules/web_console/`.

## Contract

- Static mount: `/ui/` serves `static/` (`index.html`, `app.js`, `app.css`). The files hold
  no secret, so the mount needs no token; a strict Content-Security-Policy allows only the
  page's own scripts and same-origin requests, and every daemon value is written as text.
- Routes (token required, read-only):
  - `GET /api/v1/ui/catalog` - every runnable module command with its declared
    `CliArgument`s; the page builds a form per command from it.
  - `GET /api/v1/ui/logs?lines=N` - the last `N` lines (1..5000) of the log file.
  - `GET /api/v1/ui/config` - every resolved key with its layer; secrets masked.
  - `GET /api/v1/ui/events` - bus events as server-sent events (`IpcStream`); the page
    reads it with `fetch`, so the token travels in a header, never in a URL.
- Commands run as background jobs (`"background": true` on `/api/v1/rpc`, then
  `GET /api/v1/jobs?id=`), so output appears while it grows. The catalog carries each
  command's `remote` and `danger` flags: a `danger` command needs a second click, a
  `remote=False` one is shown as terminal-only.
- CLI contributed: `kdx ui [--no-browser]`. With a live daemon it opens the console; with
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

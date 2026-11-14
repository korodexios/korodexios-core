# @TITLE@ - web app

@FRAMEWORK@ + TypeScript + Vite (GUI tier `@TIER@`). It is a client of the chassis control plane (`/api/v1/*`): it reads
`/api/v1/ui/manifest` and draws one tab per module that declares a `[ui]` table in its
`kdx_module.toml` (docs/AGENTS_GUIDE.md section 8.1). Nothing here holds core logic.

## Commands (package manager: @PM@)

```bash
@INSTALL@          # once
@RUN@ dev          # http://127.0.0.1:5173, proxies /api to the daemon (KDX_DAEMON overrides)
@RUN@ typecheck
@RUN@ test         # Vitest, jsdom: no browser, no daemon
@RUN@ build        # web/dist, served by the daemon at /app/ once built (module web_app)
@RUN@ e2e          # Playwright against a real daemon; headless unless PW_HEADED=1
```

Open the app with the token in the URL fragment: `kdx ui` prints such a link, or use
`http://127.0.0.1:5173/#token=<token from var/chassis.ipc>`. The fragment never reaches a server
and is removed from the address bar at once; without it the app shows a sign-in form.

## The shared client (`src/kdx/`)

Every app tier (`react`, `vue`) carries the same framework-neutral client; only the panel
drawing differs. Keep project code out of it, so a newer core can replace it as a whole.

- `api.ts` - `api()`, `manifest()`, `prefs()`, `catalog()`; one request path for everything.
- `auth.ts` - who the app is: the `X-Kdx-Token` header (sent only while a token is held), the
  HttpOnly session cookie (`core.ipc.ui_token_cookie`), or nobody. A 401 to a request that
  carried the header drops that token and retries once on the cookie. Inside an `embed` panel
  it asks the host once (protocol v1, `kernel/ui_embed.py` of the core); `serveEmbed()` is the
  host side for embed panels this app draws.
- `events.ts` - `events()` streams `/api/v1/ui/events`; `onTopics()` is what a panel's
  `refresh_on` uses (one shared stream).
- `media.ts` - `kind = "media"` cells: the path itself with the cookie, a blob with a header.
- `schema.ts` - written by `@SCRIPT@ ui types --out web/src/kdx/schema.ts` from the live
  daemon's `/api/v1/schema`; a removed route then fails the type check.

## Testing

Headless first. `kdx ui doctor --json` says whether this machine has a browser and a display.
`PW_CHANNEL=chrome` (or `msedge`) uses an installed browser instead of a Playwright download
(`npx playwright install chromium`); `PW_HEADED=1` opens the window when there is a display.

## Known limits (also in `kdx ui guide`)

- Renaming, hiding, reordering and own shortcut buttons are edited in the built-in console
  (`kdx ui`, Customize) or with `kdx ui prefs patch`; this app shows them and writes only
  remembered form values, pinned defaults and the last tab.
- npm, pnpm 12 and bun 1.4 were verified on Linux (install, typecheck, component tests); the
  core's own tests run none of them, so run `@RUN@ typecheck && @RUN@ test` first. With pnpm,
  `pnpm-workspace.yaml` allows esbuild's build script, which newer pnpm refuses otherwise.
- A newer npm may block the esbuild install script. If vite fails with an esbuild error, run
  `npm install-scripts approve esbuild` and install again.
- `@RUN@ e2e` starts the daemon with `uv run @SCRIPT@ run`: run `uv sync --extra dev` in the
  project root first.

## Extending

- A new module needs no code here: declare `[ui]` panels and they appear.
- To draw one panel yourself, call `overridePanel("<module>.<panel>", Component)` from
  `@OVERRIDES@`.
- The person's saved choices (labels, hidden panels, order, remembered form values, last tab)
  come from `/api/v1/ui/prefs` (`kdx ui prefs`); keep per-person state there, not in code.

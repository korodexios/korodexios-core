# korodexios-core

Industrial-grade, headless **microkernel chassis** that seeds every Korodexios project.
The core is a daemon-style engine that owns lifecycle, configuration, structured logging,
module discovery, and signal-safe shutdown. User interfaces (CLI, web console, REST) are thin
clients that talk to the same core and never reimplement it.

- **Package:** `korodexios-core` (import name `korodexios_core`)
- **Names:** the project, package, and repository are all `korodexios-core`; `kdx` is only the
  short command - everything you type starts with `kdx` (`korodexios` works the same)
- **Python:** 3.11+ (developed on 3.13), `uv`-first, `venv` + `pip` compatible
- **Bootstrap:** uv (Astral) owns the environment - `uv venv`, `uv sync --extra dev`,
  `uv run kdx ...`, `uv tool install korodexios-core`, `uv lock` for a consumer's own pins
- **Dependencies:** `pyyaml` (config files), `rich` (the wizard layer)
- **Release:** `v0.7.0`, core API frozen at `v2`

**Install in one go:** `./install.sh` (Linux, macOS) or `.\install.ps1` (Windows) - a guided,
coloured installer in English or Slovak (`--dry-run` shows the steps without running them).

---

## Documentation

| Document | Audience | What it answers |
| --- | --- | --- |
| [docs/TUTORIAL.md](docs/TUTORIAL.md) | humans, new | five minutes from `kdx init` to a running custom module |
| [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md) | humans, operators | every command, flag, exit code, install path, LAN and auth option |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | humans, engineers | microkernel design, config flow, event bus, IPC, signals, API freeze |
| [docs/ROADMAP.md](docs/ROADMAP.md) | everyone | the phase history and what ships next |
| [CHANGELOG.md](CHANGELOG.md) | humans, operators | notable changes per release, newest first |
| [docs/TRANSLATING.md](docs/TRANSLATING.md) | translators, agents | adding a language: `kdx locale add`, `todo`, `apply` |
| [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) | integrators, agents | MCP for AI agents, n8n/Zapier/Make webhooks in and out, WebSocket, tokens |
| [docs/AGENTS_GUIDE.md](docs/AGENTS_GUIDE.md) | AI agents, LLMs | contract-first reference: golden rules, module blueprint, manifest spec, verification loop |
| [llms.txt](llms.txt) | AI agents, LLMs | byte-identical mirror of the agent guide, sized for a context window |
| [AGENTS.md](AGENTS.md) | both | the working agreement: commands, layout, rules, the verification bar |
| [.agents/skills/](.agents/skills/) | AI agents | playbooks: project setup, CLI wizard UX, verification |

`docs/AGENTS_GUIDE.md` and `llms.txt` are kept byte-identical by a test, so an agent can
read either and hold the same contract. `kdx init` seeds a tailored `docs/AGENTS_GUIDE.md`
(and `AGENTS.md` with the rule files of every agent) into each project it generates.

---

## Quickstart

```bash
uv venv --python 3.13            # uv (Astral) owns the env: no pip, no ensurepip needed
uv sync --extra dev              # resolve and install dev tools; or: uv pip install -e ".[dev]"
uv run kdx --version             # run any command inside that environment
source .venv/bin/activate        # or stay activated and drop the 'uv run' prefix

kdx --version                    # byte-identical from korodexios too
kdx doctor                       # environment, config provenance, modules (--json for CI)
kdx status                       # query the running daemon over its control plane
kdx logs -n 50                   # tail the rotating log file (no POSIX tail needed)
kdx logs --rotate                # archive the log; the daemon reopens the path
kdx modules list                 # what auto-discovery found
kdx config show --key core.      # resolved values with the layer each came from
kdx run --once                   # boot, one tick, clean stop (CI friendly)
kdx run --host 0.0.0.0 --port 18123   # expose the control plane to the LAN
kdx heartbeat status             # a module-provided command
kdx system diagnostics           # interactive diagnostics menu (pick a section)
kdx backup run --dry-run         # simulate; --exclude patterns are honoured
kdx module new my_plugin        # scaffold a module (manifest, config schema, test) into the project
kdx locale check                 # are the message catalogues complete (kdx locale extract refreshes them)
kdx init my_service --path ../my_service   # seed a whole new project from the chassis
kdx wizard                       # interactive numbered-menu wizard (TTY only)
kdx ui                           # browser console: status, every module command, logs, config
kdx tls init && kdx run --tls    # https control plane (self-signed, via openssl)
kdx users add jana --role operator   # a personal token with a role; kdx audit shows who did what
kdx rpc --follow backup run      # run inside the daemon with live output; kdx jobs / kdx events / kdx stop
kdx service install              # start the daemon at logon (Task Scheduler / systemd / launchd)
```

Pin the chassis the way a consumer project does, entirely with uv:

```bash
uv tool install korodexios-core   # a global kdx on PATH, no project involved
uv pip install "korodexios-core @ git+https://github.com/korodexios/korodexios-core.git@v0.7.0"
uv lock                          # and 'uv add' for this project's own pinned dependencies
```

Without uv, `python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
still works - until the interpreter is PEP 668 `EXTERNALLY-MANAGED` and ships no `pip` and
no `ensurepip`, which is why uv leads the docs and the `Makefile` runs under `uv run` whenever
uv is on `PATH`.

CI and non-interactive runs pass `--yes`, plus `--no-log-file` on a read-only
filesystem. `kdx run --once` never prompts and never hangs: the chassis auto-detects
non-TTY stdin and forces `core.cli.interactive=false`. Development shortcuts:
`make install`, `make lint`, `make typecheck`, `make test`, `make check`, `make demo`.

## What the chassis guarantees

- **Precedence is sacred.** `CLI > env > kdx.local.yml > kdx.yml > defaults`. The env
  layer maps `KDX_CORE__IPC__PORT` to `core.ipc.port`; the canonical
  `KORODEXIOS_` prefix is understood too. `kdx config show` names the layer of every
  key and masks secret-shaped values (`--reveal` to see them); `kdx config check` fails CI
  on a key nothing reads or a module schema violation; and `kdx --env-alias LEGACY=core.key`
  gives an old environment variable a home in that same provenance.
- **Clean shutdown.** `SIGINT` exits `130`, `SIGTERM` exits `143`, an operator stop
  (including `POST /api/v1/shutdown`) exits `0`; `SIGBREAK` on Windows joins the same
  gate. Module `stop()` hooks always run, in reverse order, inside `stop_timeout`.
- **One daemon per root.** `var/chassis.lock` is claimed before any module starts; a second
  daemon on the same root exits `1` naming the live holder instead of binding a fallback
  port and looking healthy while doing nothing.
- **Reload without exit.** SIGHUP (POSIX) and `kdx reload` re-read the layers, publish the
  changed keys on the bus, and call each module's `on_config_reload` - the daemon keeps
  running and its port never moves.
- **Delegation, not a second boot.** `kdx rpc COMMAND` and `kdx --remote COMMAND` post one
  `POST /api/v1/rpc` to the running daemon and exit with its code.
- **Zero stubs.** Every branch runs. `kdx init` writes a project whose own `make check`
  is green on arrival, with a smoke test that boots the chassis in-process.
- **Stdlib first.** PyYAML parses configuration, rich drives the terminal; everything
  else is the standard library. A new dependency needs a paragraph of justification.
- **Headless first.** The CLI is a thin client of the same in-process core the web console or a
  REST client will use. Modules never write to stdout.
- **Crash-proof CLI.** Numbered menus, `[default]` in brackets, `while True` validation
  loops, and no traceback at the operator.
- **Diagnostics to stderr.** Status tags `[✓] [?] [!] [i]` are locale-neutral, stdout
  stays clean for scripting, and `--json` payloads are machine-stable.

## Layout

```text
src/korodexios_core/
  version.py / constants.py / exceptions.py   # versions, exit codes, error hierarchy
  paths.py / validate.py / units.py           # root discovery, polite validators, formatters
  config/    # LayeredConfig: five layers, dotted keys, typed getters, provenance
  logging/   # structured logging: stderr + rotating file, cooperative rotation
  events/    # EventBus: thread-safe pub/sub, wildcards, recursion guard
  kernel/    # the microkernel: contract, CLI contract, context, discovery, registry,
             # chassis, signals, ipc
  cli/       # thin clients: argparse surface, rich wizard, core commands,
             # project templating (init_project.py), scaffolding (scaffold.py)
  modules/   # built-in modules (heartbeat, system_info, backup_engine, job_queue, web_console, metrics,
             # mcp_server, realtime, webhooks, wiki) - a pure
             # discovery root: adding code here is the intended extension point
docs/        # the documentation suite, including the roadmap
.agents/skills/  # canonical playbooks, seeded into every project
tests/       # pytest; no network, no sleeps longer than one lifecycle tick
```

## Control plane

`kdx run` binds `127.0.0.1:18123` by default and writes `var/chassis.ipc` (mode `0600`)
with `pid`, `port`, `host`, and `token`. Six endpoints answer: `GET /health`,
`GET /api/v1/schema` (the machine-readable contract), `GET /api/v1/status`,
`POST /api/v1/shutdown`, `POST /api/v1/reload`, and `POST /api/v1/rpc`; authenticated reads
require the token (`X-Kdx-Token`, compared in constant time) unless `core.ipc.auth: false`,
which opens reads only - reload and RPC always need the token. A request from a foreign
web origin (or a DNS-rebinding `Host`) is refused, so a browser tab cannot drive the daemon.
Modules add their own paths declaratively with `ipc_routes()`, and the contract endpoint
lists them. Set `core.ipc.host: 0.0.0.0` to serve
the LAN (the default port, 18123, avoids the usual 8000/8080/8123 neighbours; change it with
`core.ipc.port`), and `kdx status --target host:port` to query a remote chassis.

## Agents and automation

- **MCP server:** every module command is a tool for AI agents. `kdx mcp serve` (stdio, for
  Claude Code, Cursor, VS Code, Windsurf) and `POST /api/v1/mcp` (Streamable HTTP, for remote
  agents and n8n); `kdx mcp config --write` registers it, `kdx mcp tools` lists the tools.
- **Webhooks:** bus events out to n8n, Zapier, Make, Slack or ntfy, signed with HMAC-SHA256
  (`modules.webhooks.targets`, `kdx webhooks test`); events in through
  `POST /api/v1/webhooks/inbound` with a contributor token.
- **WebSocket:** `/api/v1/ws` - subscribe to events, run commands, publish, on one connection.
- **Rules for every agent:** `kdx agents sync` writes CLAUDE.md, GEMINI.md, Cursor, Copilot,
  Windsurf and Cline rule files from one `AGENTS.md` (`--check` in CI); `kdx playbook` is the
  agent playbook. Tokens work as `Authorization: Bearer` too.

- **Measurements:** `context.metrics` (counter, gauge, summary, collector), host CPU, memory
  and disk built in; `GET /metrics` for Prometheus/Grafana, `?format=json` for Home Assistant,
  `kdx metrics` in the terminal, optional `core.metrics.snapshot` events for push bridges.
- **Project wiki:** `kdx wiki` in the LLM-wiki layout (raw sources, `SCHEMA.md`, `index.md`,
  `log.md`, `[[wikilinks]]`) - public `docs/wiki/`, internal git-ignored `docs-dev/wiki/`, one file
  per language (`page.sk.md`); `kdx wiki export` merges it into a wiki or Obsidian vault you run.
- **Improving the core:** agents draft `kdx proposal` notes instead of editing the core; the
  owner reviews and sends them (`kdx proposal send`) - see [CONTRIBUTING.md](CONTRIBUTING.md).

See [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

## Seeding a project

`kdx init my_service` writes a chassis project: `pyproject.toml` with a console script,
`kdx.yml`, `Makefile`, `README.md`, `AGENTS.md` (plus the rule files every coding agent
reads, and `.mcp.json`), `docs/`, `.agents/skills/`,
`src/<pkg>/cli.py`, a first module with a manifest, and smoke tests that boot the
chassis in-process. Nothing is a placeholder: the generated tree passes `ruff`, strict
`mypy`, and `pytest` on arrival, and its chassis dependency is pinned to the
repository, so `uv pip install -e .` (or plain `pip install -e .`) works before any index
publish; `kdx init --chassis-source index` switches the pin to the index. A git pin needs
network and uv at install time - that is the one thing `--chassis-source git` asks for. The project owns its user-facing language through
`i18n.translate()` (stdlib gettext, source strings as the fallback), so the chassis
stays locale-neutral and English logs and IPC payloads stay machine-readable.

## GUI, database and containers

Everything below works from the terminal first; the web console is only an add-on layer.

- **GUI from the modules.** A module declares a `[ui]` table in its `kdx_module.toml` and the web
  console (`kdx ui`) draws a tab for it - no HTML or JavaScript. A person can rename, hide and
  reorder tabs, add their own shortcut buttons and keep form values and defaults; it is stored per
  user on the daemon (`kdx ui prefs`). `kdx ui manifest --json` shows exactly what the browser gets.
- **Choosing a GUI.** `kdx ui guide` explains the tiers (`none`, `static`, `react`) and when to pick
  each; `kdx ui doctor` reports what this machine can run (Node, Bun, browsers, a display, a
  container engine). `kdx init NAME --ui none|static|react [--pm npm|bun|pnpm]` creates the project;
  `kdx ui test [--headed]` runs the GUI tests, headless first.
- **A database only when needed.** `context.db()` is an opt-in SQLite file with migrations;
  nothing is created unless a module asks. `kdx db list|show|query|backup` looks inside, read-only.
- **Containers.** `kdx init NAME --container` adds a `Dockerfile`, `compose.yaml` and
  `.dockerignore` (Docker and Podman). Ids are read, never assumed: `kdx container doctor` reports
  the user and id mapping and `kdx container fix` sets up rootless Podman after explaining why `sudo`
  is needed and asking - the chassis never sees your password. Verified on Debian, Ubuntu, Fedora,
  Alpine and Arch images (`scripts/distro-check.sh`).

## Installed and configured by an AI agent

The chassis expects an AI agent to install and set it up, so every decision is available as data
(`--json`) and nothing system-wide changes without an explicit answer. In this order:

```bash
./install.sh                      # or: uv tool install korodexios-core   (asks before it installs anything)
kdx doctor --json                 # environment, configuration provenance, modules
kdx playbook                      # the working guide for the agent (also docs/AGENTS_GUIDE.md, llms.txt)
kdx ui doctor --json              # what can run here: Node/Bun, browsers, display, container engines
kdx ui guide --json               # the GUI tiers, known limits and where each kind of state belongs
kdx container doctor --json       # only if containers are wanted: uid/gid, id mapping, rootless readiness
kdx init my_service --ui none     # add --ui react / --container as the project needs
```

Rules for the agent: read the machine with the `doctor` commands before choosing; never hard-code a
uid or edit `/etc/subuid`, `/etc/passwd` or an existing account (`kdx container fix` proposes and asks);
say which steps were skipped (`kdx ui test` reports `skipped` with a reason); and keep the choice in
`docs/BRIEF.md`. A person reading this can use the same commands - they are plain CLI.

## Core API freeze

The package root `__all__` is the public surface, versioned as `CORE_API_VERSION`
(`v2`). Modules import only from the package root:

```python
from korodexios_core import CliArgument, CliCommand, ConfigField, IpcRoute, Module
```

Additive changes are allowed inside `v2`; anything that breaks an import or a signature
moves the major version. Everything added since `v2` shipped (instance lock, async runtime,
supervision, IPC routes, config schemas, named locks, scheduler, limiter, child processes,
durable queue, language service) is additive and exported; [CHANGELOG.md](CHANGELOG.md) has
the per release list. The wheel ships a `py.typed` marker.

## Roadmap

The phase history and what ships next live in [docs/ROADMAP.md](docs/ROADMAP.md). Publishing
to an index (PyPI via trusted publishing) is still open; there is no plan for a TUI, the
terminal CLI and the web console are the two front ends.

## License

MIT — see [LICENSE](LICENSE).

# CLI Reference

Every command is a thin client of the core: the same behaviour is available through
the Python API. All examples use the primary alias `kdx`.

## Invocation

```bash
kdx <command> [subcommand] [flags]     # primary short alias
korodexios <command>                   # canonical name
```

Help text follows the alias you invoke (`usage: kdx ...`), so screenshots match the
docs. `kdx --help` lists every command, including the ones modules contributed.

## Distribution and installation

| Install | Command | Notes |
| --- | --- | --- |
| uv, in the chassis repository | `uv venv --python 3.13 && uv sync --extra dev && uv run kdx doctor` | uv (Astral) owns the environment; `make` resolves to `uv run` when uv is on `PATH` |
| guided installer | `./install.sh` (Linux, macOS) or `.\install.ps1` (Windows) | four coloured steps - check, install, verify, first project; asks before installing uv; `--lang sk`, `--yes`, `--dry-run` (`-Language`, `-Yes`, `-DryRun` on Windows) |
| uv tool | `uv tool install korodexios-core` | a global `kdx` on PATH, no project involved |
| git pin, the consumer path | `uv pip install "korodexios-core @ git+https://github.com/korodexios/korodexios-core.git@v0.5.1"` | the documented pin before an index publish; needs network |
| git bootstrap, ssh form | `pip install "korodexios-core @ git+ssh://git@github.com/korodexios/korodexios-core.git@v0.5.1"` | what `kdx init --chassis-source git` writes into the seed |
| pipx, or venv + pip | `pipx install korodexios-core`; `python3 -m venv .venv && pip install -e ".[dev]"` | the fallback when uv is absent - and it fails as written on a PEP 668 `EXTERNALLY-MANAGED` interpreter with no `pip` and no `ensurepip` |
| from an index | `uv pip install korodexios-core`, or `pip install korodexios-core` | once published; a consumer pins it with `uv lock` |

A consumer project pins the chassis with uv: `uv lock` records the resolved git pin in
`uv.lock`, and `uv run kdx ...` runs any command inside that environment. This repository
carries `uv.lock` and `[tool.uv] package = true`, so `uv sync` builds the chassis in place.

The wheel carries `py.typed` and every built-in module manifest
(`kdx_module.toml`), so a plugin installed into the same environment can read the
manifests it needs. The sdist carries `docs/`, `README.md`, `AGENTS.md`, `llms.txt`,
`.agents/skills/`, `kdx.yml`, and the tests, so an sdist install can run `make check`.

## Global options

| Option | Effect | Default |
| --- | --- | --- |
| `--version` | print chassis and core API version, exit | - |
| `--home PATH` | chassis root directory | discovered root |
| `--config FILE` | explicit config file (`kdx.yml`, `korodexios.yml`); a relative path is read under `--home` | first candidate |
| `--env-file FILE` | explicit `.env` path; a relative path is read under `--home` | `<root>/.env` |
| `--log-level LEVEL` | console log level (diagnostics go to stderr) | `INFO` |
| `--log-json` | emit JSON log lines | off |
| `--no-log-file` | skip the rotating log file | file enabled |
| `--modules-dir PATH` | extra plugin directory (repeatable) | - |
| `--ipc-host HOST` | control-plane bind address (`0.0.0.0` for LAN) | `127.0.0.1` |
| `--ipc-port PORT` | control-plane port (`0` = ephemeral) | `8123` |
| `--auth` / `--no-auth` | require or allow anonymous control-plane reads | auth on |
| `--ipc-token TOKEN` | static shared secret for the control plane | empty (ephemeral) |
| `--ipc-timeout SECONDS` | client timeout for control-plane reads | `2.0` |
| `--tls` | serve the control plane over https (`core.ipc.tls`) | off |
| `--tls-ca FILE` | certificate to trust when calling a remote https daemon | system store |
| `--no-ipc` | do not open the control plane in `kdx run` | ipc enabled |
| `--instance-lock` / `--no-instance-lock` | claim or skip `var/chassis.lock` for this run | lock claimed |
| `--asyncio` / `--no-asyncio` | start or skip the chassis-managed asyncio loop | loop off |
| `--env-alias NAME=KEY` | map a legacy environment variable onto a dotted key (repeatable) | - |
| `--remote` | delegate the command to a running daemon over IPC, then exit | - |
| `--allow-anonymous-shutdown` | unauthenticated clients may stop the daemon | off |
| `--tick-interval SECONDS` | lifecycle tick interval | `1.0` |
| `--stop-timeout SECONDS` | budget for module `stop()` hooks | `5.0` |
| `--logs-poll-interval SECONDS` | follow poll interval for `kdx logs` | `0.5` |
| `--log-max-bytes BYTES` | rotating log size limit | `1048576` |
| `--log-backup-count COUNT` | rotating log archives kept | `5` |
| `--cli-width COLUMNS` | terminal table width for the CLI | `100` |
| `--confirmations` / `--no-confirmations` | ask before destructive module actions | on |
| `--no-autodiscover` | disable module auto-discovery for this run | autodiscover on |
| `--enable-module NAME` / `--disable-module NAME` | force one module on or off (repeatable) | - |
| `--name NAME` | chassis display name (banner, doctor) | `korodexios` |
| `--locale LOCALE` | language of help and messages (`core.locale`); command and flag names stay English | `en` |
| `--no-color` | disable coloured output | colour on a TTY |
| `-q, --quiet` | warnings and errors only | - |
| `-v, --verbose` | debug logging and tracebacks | - |
| `-y, --yes` | accept defaults, never prompt (auto-detected for non-TTY) | - |

Two bootstrap keys stay config/env-only: `core.distribution` (packaging metadata) and
`core.env.prefix` (it selects the env layer the CLI would itself override).

## `kdx run`

```bash
kdx run                       # boot and run until interrupted or stopped remotely
kdx run --once                # publish one tick and stop: the CI-safe smoke test
kdx run --duration 60         # stop automatically after 60 seconds
kdx run --host 0.0.0.0 --port 8123    # LAN-reachable control plane
```

Exit codes: `0` clean or operator stop, `130` SIGINT, `143` SIGTERM.

## `kdx status`

```bash
kdx status                    # read the local discovery file, print the live report
kdx status --json             # machine-readable report
kdx status --target http://10.50.50.198:8123 --token SECRET   # remote daemon
kdx status --host 127.0.0.1 --port 8123                        # explicit local endpoint
```

Reports endpoint, auth policy, shutdown policy, the source of the endpoint, chassis
state, uptime, ticks, and every module's health. A 401 prints a hint to pass `--token`
or set `KDX_CORE__IPC__TOKEN`.
Since `v0.3.0` the JSON payload carries the project's identity triple - name, version,
contract directory - under `project`: the same one `kdx doctor` prints.

## `kdx reload`

```bash
kdx reload                      # ask the running daemon to re-read its configuration
kdx reload --json               # {"reloaded": true, "changed": [...]}
```

The daemon stays up: the layers are re-read with the same precedence, the changed keys
are published as `core.config.reloaded`, and every module's optional `on_config_reload`
hook runs with its scoped view. Log level changes apply immediately; moving a path role
(`core.paths.*`, `core.log.dir`) needs a restart. With no daemon answering the command
prints the note and exits `0` - a reload is never an error, and `kdx doctor` tells you
whether SIGHUP is wired.

## `kdx rpc`

```bash
kdx rpc heartbeat status                 # run a module command inside the daemon
kdx rpc heartbeat status --args '{"lines": 5}'
kdx rpc backup run --json                # the daemon's reply, verbatim
kdx rpc db index rebuild --args "{"lines": 8}"}   # a nested path, any depth
```

The client reads `var/chassis.ipc`, posts one `POST /api/v1/rpc`, and prints the answer:
`ok`, `command`, and `result` on success, `error` plus the daemon's HTTP status otherwise.
Exit code is `0` when the daemon's command exited `0`, else `1`; an unknown command is `404`
on the wire and `1` here. `kdx --remote heartbeat status` is the same delegation spelled
as a global flag: the client never boots a chassis of its own.

The command path nests as deep as the module declared it: the daemon walks
`CliCommand.subcommands` token by token, so `kdx rpc db index rebuild` is one rpc call. Its
reply carries `path` and `depth`, and a name that resolves to a group with no handler answers
`404` naming the group that has no such child.

## `kdx doctor`

```bash
kdx doctor                    # environment, paths, config provenance, modules, IPC
kdx doctor --json             # the same report as JSON
```

Works without a running daemon - it boots the chassis in-process, so it is the first
command to run when something looks misconfigured. The report covers the instance lock
(held, stale, and whether a daemon answers), the asyncio loop, watched workers, and the
diagnostics every module contributes; `kdx doctor --json` carries the same under
`instance`, `async`, `supervision`, `scheduler`, `locks`, `static`, and `diagnostics`. Scheduled
tasks that keep failing and lock files left behind by a dead process report diagnostics of
their own, so the run explains a stuck task and an abandoned lock without a module having to
mention either. A module in `failed` health or an `error` diagnostic exits the command `1`,
so it works as a deployment gate.

The report asks each module for `diagnostics()` and `health()`, so the command initializes the
discovered modules first; `modules.modules[].declarations_after_init` says whether a module's
routes, schemas, and checks were read with its context in hand. A module that only
answers those hooks with its configuration therefore contributes real findings here rather
than a `ModuleContractError` in the log. The built-in modules contribute their own rows
here since `v0.2.2`: `backup_engine` reports whether its source tree is readable and its
destination writable, and `heartbeat` reports whether its ticker is alive on the chassis
clock. Since `v0.2.3` a backup run that stepped aside for its named lock adds a `lock` row,
and `kdx backup run` answers that refusal with exit `1` and a hint instead of a traceback.
Since `v0.2.4` `system_info` reports its load sampler too: whether the watcher thread is alive,
parked by configuration, parked because the platform has no load average, or stopped on its
own - and a thread that stopped on its own is what worker supervision marks `failed`.
Since `v0.2.5` the same rows carry the periodic sweep: `backup_engine` says whether `auto_sweep`
is scheduled, how many copies it has landed, and how many stepped aside for a copy already
running. Since `v0.2.7` they carry retention too: a `retention` row says whether `auto_prune`
sweeps on the clock or is left to the copy, how many archives it aged out, how much space that
took back, and when it last ran - and `kdx status` and `kdx doctor --json` name the task the
scheduler is still waiting on (`active_task`) when a stop overruns `core.lifecycle.stop_timeout`.
The locks directory is read with the same care: an editor's swap file, a dot file, and a name
no lock could answer to are never reported as a held or stale lock.

Since `v0.2.10` the chassis is honest about the modules it did not load. A discovery failure
is a `discovery` check at `warning` severity by default and at `error` when
`core.modules.strict_discovery` is on - the same switch that makes `kdx run --once` exit `3` on
a `requires_core` mismatch instead of `0`, while `kdx doctor` still runs and reports why. A
module whose own schema declares `enabled` or `priority` contributes a `module-setting`
warning, and `modules.modules[].settings_collisions` names the keys per module. The effective
environment aliases are reported under `config.aliases` whatever shape they were declared in.
Since `v0.3.0` the report prints the project's identity - `name`, `version`, `contract_dir` -
as a section of its own and carries it under `project`, so one triple is reported by doctor,
`kdx status --json`, and `kdx contracts check` alike. A declared contract file that drifted
or went missing contributes a `contracts` check at `warning`: the chassis verifies the set
and never rewrites a byte, leaving the finding for the project to settle.

## `kdx logs`

```bash
kdx logs -n 50                # last 50 lines (default)
kdx logs -f                   # follow until Ctrl-C
kdx logs -f --timeout 5       # follow for 5 seconds (CI friendly)
kdx logs --raw                # only log lines, no decorations
kdx logs --clear              # truncate the log file (asks first; --yes accepts)
kdx logs --rotate             # archive the file and start a fresh one
```

No POSIX `tail` required: the file lives under the configured log directory and is
resolved from configuration, not a hardcoded path. Rotation names the archive
`kdx.log.<timestamp>`, keeps the newest `core.log.backup_count` archives, and is
cooperative: the running chassis notices the renamed file on its next write and
reopens the path, so it never keeps appending into the archive.

## `kdx reset`

```bash
kdx reset                     # list what would be removed (dry run, the default)
kdx reset --json              # the same plan as JSON
kdx reset --apply             # delete it (asks first on a TTY; non-TTY and --yes proceed)
```

Clears the disposable development data and nothing else. The disposable roots are
`core.reset.disposable` (default: the data directory, `var/lib`). The durable directory
(`data/`, which holds the control-plane users and the audit log) and every root in
`core.reset.durable` are never touched, and a disposable root that overlaps a durable root,
equals the project root, or contains the runtime state, log, or configuration paths is refused
with exit code 3 instead of being trimmed. Files named in `core.reset.keep` (default
`.gitkeep`) stay, and so do the directories that hold them. Symbolic links and Windows
junctions are removed as links and never followed. `--apply` refuses while the daemon runs.

## `kdx txn`

```bash
kdx txn list [--json]         # transactions, newest first; unfinished ones are counted
kdx txn show ID [--json]      # phase, and what each file hashed to before and after
kdx txn recover [--json]      # finish or roll back whatever a crash left in flight
kdx txn undo ID [--json]      # take back a finished transaction
```

A module that changes several files at once builds a `TransactionManager`
(`korodexios_core.kernel.txn`) and calls `run([TxnOp.write(path, text), TxnOp.delete(path)])`.
The transaction is journalled phase by phase (`prepared`, `backed_up`, `applied`,
`committed`, `finalized`) under `data/txn`, which `kdx reset` never touches. A failure rolls
the files back before it is raised; a crash is settled by `recover()` (call it from the
owner's start-up) or `kdx txn recover`: before `committed` the files are restored from their
backups, after it the transaction is finished. `undo` refuses with `result-changed` when any
file no longer hashes to what the transaction left, so a later change is never overwritten.
The project supplies an `allow(path)` callback (which relative paths may be touched) and a
`reason(code, fields)` callback (how a failure code reads); codes are stable.

## `kdx modules`

```bash
kdx modules list              # name, version, state, origin, dependencies, description
kdx modules list --json
kdx modules show <name>       # spec plus live health for one module (or all)
kdx modules create <name> --path src/<pkg>/modules [--title TEXT] [--force] [--json]
```

`create` scaffolds `__init__.py`, `module.py`, and `kdx_module.toml` - zero stubs,
immediately runnable.

Since `v0.2.10` `list` also names the modules the chassis did not load, each with the reason
that decided it - `disabled by its manifest`, `not listed in core.modules.enabled`, `listed in
core.modules.disabled`, or `modules.<name>.enabled is false` - and the optional-dependency rows
beside them. `--json` carries the health block plus `skipped`, `optional`, and `failures`. The
chassis logs the same summary once at boot, on stderr.

## `kdx config`

```bash
kdx config show                       # every resolved key with its source
kdx config show --key core.log        # only keys under a prefix
kdx config show --json                # mapping as JSON
kdx config show --reveal              # show secret values instead of ***redacted***
kdx config file                       # which file won, and the local override if any
kdx config check                      # unknown keys, module schema violations
kdx config check --json               # the same report for machines
```

Sources: `built-in default`, `config file`, `local override file`, `environment`,
`CLI override`. Environment aliases work in either declared shape - the mapping under
`core.env.aliases` in a config file (which the loader flattens to `core.env.aliases.NAME`
leaves) and the `NAME=key` list exported as `<PREFIX>_CORE__ENV__ALIASES` - and a properly
prefixed variable still outranks an alias mapped to the same key.

`kdx config check` reports two high-confidence classes of typo: a `core.*` key with no
built-in default and no mapping default above it, and a `modules.<name>.*` key whose module
was never discovered. A mapping-valued default is a namespace, so the subkeys a project
declares under it - `core.env.aliases.LEGACY_RUN_MODE` - are valid paths and never fail the
run. Each suggestion stays inside the namespace the typo named: a sibling key of the
module the typo named, and never an unrelated loaded module. A foreign top-level key
(`greeting`, `app.name`, `project.owner`) is the project's own
it is listed as information and never fails the command. The chassis logs the same
findings once at boot, on stderr, and `kdx doctor --json` carries them under
`config.unknown`. The command exits `3` (configuration error) when it finds a key
nothing reads, so CI can gate on it. The second half of the report is declarative
module schemas: `config_schema()` hooks are read when a module registers and collected again
after `init(context)` ran, validated against the module's scoped keys, and printed under
"Module configuration schemas" - the three built-in proof modules declare
`backup_engine: 10 keys`, `heartbeat: 1 keys`, and `system_info: 4 keys` out of the box, so a
project is validated against a real schema before it owns one. A required
key with no value, a value that will not coerce, or a value outside the declared
`choices` is an error and exits `3`; the JSON payload carries them under `schemas.issues`.

## `kdx contracts`

```bash
kdx contracts check             # hash every declared file, report drift
kdx contracts check --json      # the same report, every finding included
```

A declaration is a row of `path`, `sha256`, `classification`, and `merge_policy`, contributed
by a module's `contracts()` hook or by the embedding app through
`ChassisOptions.project_contracts` / `Chassis.register_contracts(rows)`. A relative path
resolves under `core.project.contract_dir` (default `contracts` under the home); a row that
declares no hash states only that the file must exist.

| Situation | Result |
| --- | --- |
| every declared file hashes as declared | exit `0`, one summary line |
| a file changed, or was never installed | exit `3`, the named files, `--json` carries every finding |
| nothing declared a file set | exit `0` with a note: contracts never fail a boot |

The chassis verifies and never rewrites: regenerating a file is the project's decision. The
classification and merge-policy words are the project's own vocabulary - the chassis stores
them, prints them, and never reads them. `kdx doctor` carries the same report as a `contracts`
check at `warning` severity while anything drifted.

## `kdx init`

```bash
kdx init my_app                       # interactive wizard (name, ecosystem, author)
kdx init my_app --path ../my_app --minimal --yes
kdx init my_app --ecosystem fullstack # adds web/ static client and web-ui skill
kdx init my_app --force               # write into a directory that already holds files
kdx init my_app --chassis-source index # how the project resolves the chassis
```

The seed ships `kdx.yml`, `.env.example`, `Makefile`, `README.md`, `AGENTS.md`,
`docs/AGENTS_GUIDE.md`, `docs/ARCHITECTURE.md`, `.agents/skills/`, a first module, and
smoke tests. It passes `make check` on arrival.

The chassis dependency is pinned to the repository by default
(`korodexios-core @ git+https://github.com/korodexios/korodexios-core.git@v0.5.1`), so a fresh
venv can `pip install -e .` the seeded project before any index publish. Pass
`--chassis-source index` once the package is on an index; an unknown choice is refused
with a hint. A git pin needs network and uv at install time - `uv venv`,
`uv sync --extra dev`, then `uv run <name> run` - which is what the seed prints as its next
step, with `pip install -e '.[dev]' && <name> run` as the one-line fallback.

## `kdx wizard`

Interactive menu over the same declarative command tree the modules register. Numbered
choices, `[default]` in brackets, validation loops, `[OK] [?] [!] [i]` tags. Non-TTY
runs and `--yes` skip the prompts.

## `kdx version`

Prints the chassis version, the core API version, and the Python runtime line.

## Command nesting and arguments

Module commands are trees, not flat names, and every surface reads the same tree:

```bash
kdx db index rebuild --table page --lines 8   # argparse builds this from subcommands
kdx wizard                                    # the menu descends group by group
kdx rpc db index rebuild                      # the daemon walks the same path
```

- A group is a `CliCommand` with `subcommands` and no handler; asking for the group alone
  prints its usage (CLI) or answers `404` (rpc) - never a silent no-op.
- An option declared `required=True` is honoured: leaving it out exits `2`. It is refused for
  `store_true`, `count`, and `*_const` actions, because a flag is `False` when absent.
- A positional argument with `nargs="?"` and a `default` lands on that default, not on
  `None`; a positional is mandatory unless its `nargs` says otherwise.
- Duplicate or blank names inside one tree are rejected when the command is built, so a typo
  fails at import time instead of three levels down.

The chassis primitives, and the keys that configure them:

| Setting | Config key | Env | Default |
| --- | --- | --- | --- |
| Durable storage role | `core.paths.durable_dir` | `KDX_CORE__PATHS__DURABLE_DIR` | `data` |
| Periodic tasks on | `core.scheduler.enabled` | `KDX_CORE__SCHEDULER__ENABLED` | `true` |
| Scheduler on its own thread | `core.scheduler.threaded` | `KDX_CORE__SCHEDULER__THREADED` | `false` |
| Serve module static mounts | `core.ipc.static` | `KDX_CORE__IPC__STATIC` | `true` |
| Refuse a boot on any discovery failure | `core.modules.strict_discovery` | `KDX_CORE__MODULES__STRICT_DISCOVERY` | `false` |
| Plugin roots shipped as an installed distribution | `core.modules.foreign_roots` | `KDX_CORE__MODULES__FOREIGN_ROOTS` | `[]` |
| State the project's own name | `core.project.name` | `KDX_CORE__PROJECT__NAME` | `core.name`, then the home directory name |
| State the project's own version | `core.project.version` | `KDX_CORE__PROJECT__VERSION` | installed version of `core.distribution`, then the chassis version |
| Point at the verified file set | `core.project.contract_dir` | `KDX_CORE__PROJECT__CONTRACT_DIR` | `contracts` under the home |
| Declare *no opinion* tokens | `core.config.sentinels` | `KDX_CORE__CONFIG__SENTINELS` | `[]` |

Named locks live in `&lt;state_dir&gt;/locks/&lt;name&gt;.lock`: `kdx doctor` lists them, and shutdown
releases every name the chassis took. Files that must outlive a moved `var/` belong under
`core.paths.durable_dir` (default `data/`), and recurring work belongs in
`context.schedule(interval_seconds, callback)`.

## Built-in module commands

```bash
kdx system info | diagnostics | disk      # host OS, CPU, memory, disk
kdx heartbeat status [--json]             # counters and the configured interval
kdx backup plan | run | retention | configure   # dry-run, execute, ask what retention did, or reconfigure
```

Each is registered by its module through `register_cli`, so argparse and the wizard
both know about them.

`backup_engine` reads three clock knobs, each opt-in and each reported by `kdx doctor`:
`modules.backup_engine.schedule_interval` copies on the chassis clock (task `auto_sweep`),
`modules.backup_engine.auto_prune_days` decides how long a stamped copy survives (`0` keeps
every copy), and `modules.backup_engine.prune_interval` (`v0.2.7`) gives retention its own beat
(task `auto_prune`) so aging an archive never waits for the next copy to land.

`kdx backup retention [--json]` answers what that policy has done without reading JSON by
hand: the window and sweep beat from the configuration, the stamped copies standing in the
destination (count, bytes, oldest and newest, how many already stand past the window), and
the sweep record (runs, last sweep, removed, reclaimed). Only folders whose own name is a
backup stamp count - the same rule a sweep deletes by - and the report never removes
anything. The counters name the process that answers: a running daemon's own record comes
through `kdx status`, `kdx doctor`, or `kdx --remote backup retention`, which runs the
report inside the daemon; the control plane also carries it at `GET /api/v1/backup/retention`.

## LAN and auth options

```bash
kdx run --host 0.0.0.0 --port 8123        # bind the control plane to all interfaces
kdx run --no-auth                         # anonymous /health and /api/v1/status
kdx run --ipc-token my-static-lan-key     # stable secret across restarts
kdx status --target http://HOST:8123 --token my-static-lan-key
```

| Setting | Config key | Env | CLI |
| --- | --- | --- | --- |
| Bind address | `core.ipc.host` | `KDX_CORE__IPC__HOST` | `--ipc-host` / `run --host` |
| Port | `core.ipc.port` | `KDX_CORE__IPC__PORT` | `--ipc-port` / `run --port` |
| Read auth | `core.ipc.auth` | `KDX_CORE__IPC__AUTH` | `--auth` / `--no-auth` |
| Shared secret | `core.ipc.token` | `KDX_CORE__IPC__TOKEN` | `--ipc-token` |
| Anonymous stop | `core.ipc.allow_anonymous_shutdown` | `KDX_CORE__IPC__ALLOW_ANONYMOUS_SHUTDOWN` | - |
| LAN host names | `core.ipc.allowed_hosts` | `KDX_CORE__IPC__ALLOWED_HOSTS` | - |

A wildcard bind (`0.0.0.0`, `::`) is announced as `127.0.0.1` in `var/chassis.ipc`, so
local clients need no change; remote clients pass `--target`. On a LAN bind, list the
names and addresses clients dial in `core.ipc.allowed_hosts` (for example
`[nas.lan, 192.168.1.20]`): a request with any other `Host` is refused with `403`, which
closes DNS rebinding. Loopback names are always accepted.

### https in three commands

```bash
kdx tls init --host nas.lan          # self-signed cert in var/tls/ (needs openssl)
kdx run --tls --host 0.0.0.0         # serve https on the LAN
kdx --tls-ca cert.pem status --target https://nas.lan:8123 --token SECRET   # from elsewhere
```

Local clients (`kdx status`, `kdx --remote`, `kdx ui`) need nothing extra: the discovery
file names the certificate. A browser shows a warning for a self-signed certificate once;
on the daemon's own machine plain http on `127.0.0.1` is already private, so TLS matters
for LAN access.

`--no-auth` opens **reads** only. `POST /api/v1/rpc` and `POST /api/v1/reload` always need
the token, and shutdown needs it unless `core.ipc.allow_anonymous_shutdown` is set. The
control plane speaks plain HTTP: on a LAN the token travels in clear, so reach a remote
daemon over a VPN (WireGuard, Tailscale), an SSH tunnel
(`ssh -L 8123:127.0.0.1:8123 host`), or a TLS reverse proxy rather than an open network.

## Start at logon (`kdx service`)

```bash
kdx service install --print     # show exactly what would be registered; change nothing
kdx service install             # start the daemon at every logon, and start it now
kdx service status              # registered? does the daemon answer?
kdx service uninstall           # stop it cleanly, then remove the registration
```

Each host's own service manager does the work, as the current user and without
administrator rights:

| Host | Registration | Restart on failure |
| --- | --- | --- |
| Windows | Task Scheduler task `\Korodexios\<name>` at logon, defined by XML in `var/service/`, run through `pythonw.exe` (no console window) | 3 times, a minute apart |
| Linux | systemd user unit `~/.config/systemd/user/<name>.service` (`loginctl enable-linger USER` to start at boot without logging in) | `Restart=on-failure` |
| macOS | LaunchAgent `~/Library/LaunchAgents/dev.korodexios.<name>.plist` | `KeepAlive` on failure |

The daemon runs as `<python> -m korodexios_core --home <root> run`, so it keeps working when
the `kdx` shim is not on `PATH`; its output goes to the rotating log (`kdx logs`).
`--service NAME` picks another name, for several projects on one machine.

## Jobs, live events, and stop (`kdx jobs`, `kdx events`, `kdx stop`)

Everything the web console does against a daemon works in the terminal too.

```bash
kdx rpc --follow backup run -- --source D:/photos    # run in the daemon, print output live
kdx rpc --background backup run                      # start it, return the job id at once
kdx jobs list                                        # id, state, user, command, exit, time
kdx jobs show job-3 --follow                         # attach to a running job
kdx jobs cancel job-3                                # ask it to stop at its next safe point
kdx events                                           # live bus events (routine ticks hidden)
kdx events 'core.job.**' --json -n 10                # filtered, machine-readable, bounded
kdx stop                                             # stop the daemon cleanly (admin)
```

Every command takes `--target URL --token T` for another machine. `kdx rpc --follow` has no
timeout: it starts a job and polls it, so a long backup is fine; Ctrl+C stops watching, not
the job. A job's exit code becomes the command's exit code; a cancelled job exits `130`.

Cancelling is cooperative: the job's command checks `request.cancelled()` between units of
work. `backup run` checks before every file, keeps what it already copied, and says so. A
command that never checks runs to its end. Only the job's owner or an admin may cancel it.

Arguments after `--` reach the command exactly as typed - a path with dots, slashes, or
spaces arrives whole.

## Rate limiters (`kdx limiter`)

```bash
kdx limiter list [--json]                          # used today, left today, cooldown
kdx limiter cooldown external-api --hours 24 --reason "HTTP 429"
kdx limiter clear external-api                     # end a cooldown early
```

The state is `data/limiters/<name>.json`, shared with the running daemon through a named lock.
A cooldown set here stops every `limiter.acquire()` of that name in every process. `kdx doctor`
reports a cooling limiter and a spent daily budget.

## Durable queue (`kdx queue`)

```bash
kdx queue submit render --payload '{"id": 7}' [--max-attempts 3] [--delay 60] [--json]
kdx queue list [--state queued|running|done|failed|cancelled] [-n 50] [--json]
kdx queue show 7 [--json]
kdx queue cancel 7                                 # only a queued task can be recalled
kdx queue retry 7                                  # failed or cancelled -> queued, attempts reset
kdx queue purge --days 30
```

The queue file is `data/jobs/queue.sqlite3`; the daemon runs a task when a module registered a
handler for its kind (`context.job_queue().handle(kind, fn)`). Over the control plane:
`GET /api/v1/queue[?state=&limit=&id=]` (viewer), `POST /api/v1/queue` with
`{"kind", "payload", "max_attempts", "delay"}`, `POST /api/v1/queue/cancel` and
`/retry` with `{"id"}` (operator). `kdx jobs` is a different thing: background RPC commands
that end with the daemon.

## Users, roles, and the audit log

One shared token is one operator. For a team, give each person a token of their own:

```bash
kdx users add jana --role operator     # prints Jana's token once
kdx users list                         # who has access, with which role
kdx users role jana viewer             # change a role
kdx users rotate jana                  # new token; the old one stops working
kdx users remove jana                  # access ends at once
kdx audit -n 20                        # who did what over the control plane
kdx audit --user jana --action rpc --json
```

| Role | May |
| --- | --- |
| `viewer` | status, logs, configuration, events, jobs - read only |
| `contributor` | also call module routes that declare `role="contributor"` (a drop-box); runs no command |
| `operator` | also run module commands, in the foreground or as jobs |
| `admin` | also commands marked `danger`, reload, stop, the audit log in the GUI |

Users live in `data/users.json` (only SHA-256 hashes of tokens, owner-only file), and a
running daemon sees a change on its next request. The shared `core.ipc.token` keeps working
as the built-in admin `owner`, so a one-person setup needs nothing new. A teammate uses
their token exactly like the shared one: `kdx status --target http://HOST:8123 --token T`,
`kdx --remote ...`, or the web console's token field. `GET /api/v1/whoami` answers who a
token belongs to.

Every action over the control plane - commands, jobs, reloads, stops, and every refused
attempt (wrong token, role too weak) - is one line in `data/audit.jsonl` (rotates at 5 MiB,
one generation kept). Reads are not recorded. Roles limit what the control plane lets a
caller ask for; every command still runs under the daemon's operating-system account.

## Web console (`kdx ui`)

```bash
kdx ui                 # open the browser console of the running daemon
kdx ui --no-browser    # print the URL instead (headless host, SSH session)
```

The built-in `web_console` module serves a browser GUI from the daemon itself at
`http://127.0.0.1:8123/ui/`: an overview (modules, scheduler, memory, control plane), every
module command as a form generated from its declared `CliArgument`s, the log tail with
follow mode, the resolved configuration with provenance (secrets masked), diagnostics, and
**Reload config** / **Stop daemon** buttons. When no daemon is running, `kdx ui` boots one
in the foreground (as `kdx run` would) and opens the console once the control plane is up.

The page is a thin client: every command runs as a background job through
`POST /api/v1/rpc`, so the GUI shows the output while it grows, and the same output and
exit code the terminal would. A command a module marked `danger=True` (such as
`backup run`) needs a second click; one marked `remote=False` (such as the interactive
`backup configure`) is shown as "terminal" with the line to run there. The **Events** tab
streams bus events live (routine ticks hidden by default). `kdx ui` passes the token in the URL fragment
(`/ui/#token=...`), which never reaches a server or a log; the page keeps it in
`sessionStorage` only. Disable the console with `--disable-module web_console`, or keep it
from opening a browser with `modules.web_console.open_browser: false`.

## Languages (`kdx locale`)

Help text and messages follow `core.locale` (`--locale sk`, `KDX_CORE__LOCALE`, `kdx.local.yml`,
`kdx.yml`; `auto` reads `LC_ALL`/`LANG`). Shipped: en, sk, cs, de, es, fr, pl. Command names, flags,
`--json`, logs and stderr stay English.

| Command | Effect |
| --- | --- |
| `kdx locale show` | active language and where it came from |
| `kdx locale list` | available languages per domain |
| `kdx locale set LOCALE` | write `core.locale` into `kdx.local.yml` |
| `kdx locale check [--language L]` | completeness of catalogues; exit 3 if incomplete |
| `kdx locale extract [--check]` | refresh the template from the source |
| `kdx locale add L` / `todo L [--json]` / `apply L FILE` | add a language, list what is missing, merge translations |

See [TRANSLATING.md](TRANSLATING.md).

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | success, or an operator-requested stop |
| `1` | handled failure (chassis or CLI error with a hint) |
| `2` | usage error (argparse) |
| `3` | configuration error (an unknown key from `kdx config check`, a refused boot when `core.modules.strict_discovery` is on, or a drifted or missing file from `kdx contracts check`) |
| `4`-`125` | a command handler's own verdict code, passed through unchanged |
| `130` | SIGINT; Windows `SIGBREAK` joins the same gate |
| `143` | SIGTERM |

## Scripting rules

- A `--log-json` line keeps the types the module passed: a count is a JSON number, a flag
  is a boolean, a nested mapping stays a mapping; only a `Path`, an exception, or another
  object arrives as shortened text.
- `var/chassis.ipc` is published by a rename from a sibling the process owns, never truncated
  in place: a script that polls the file during boot reads a complete announcement or the
  previous one, never a half-written payload it cannot decode.
- The control plane answers one request per connection: a client that offers HTTP/1.1
  keep-alive still gets one response and a closed socket, so a script never waits for a
  second answer on a socket the chassis will not reuse.
- Non-TTY runs never prompt; `--yes` also forces defaults in a TTY.
- `--json` is available on `doctor`, `status`, `modules list`, `modules show`,
  `modules create`, `config show`, `config check`, `reload`, `rpc`, and the module status
  commands.
- `kdx reload` exits `0` even with no daemon running; `kdx rpc` and `kdx --remote` exit `1`
  when nothing answers, because the operator asked for work that did not happen.
- A module command's exit code is the integer its handler returned, verdict codes
  included, so `kdx` can gate a deployment on `kdx index check` exiting `4` and not only on `0`/`1`.
- Diagnostics go to stderr, UI output to stdout: redirect stderr away and stdout stays
  parseable.
- `--home PATH` makes any command runnable from anywhere; it beats `KDX_HOME`.

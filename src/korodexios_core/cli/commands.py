"""Core commands contributed by the chassis itself, not by any module."""

from __future__ import annotations

import sys
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .. import jsonio
from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..exceptions import ConfigurationError
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.contracts import CONTRACT_NAMED_ROWS
from ..kernel.ipc import (
    DEFAULT_PORT,
    DISCOVERY_NAME,
    RELOAD_PATH,
    STATUS_PATH,
    IpcDiscovery,
    IpcError,
    is_loopback_host,
)
from ..version import format_version
from .config_edit import edit_commands
from .render import (
    dump_json,
    format_bool,
    print_key_values,
    print_section,
    print_table,
    tr,
    write_line,
    write_status,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

RUN_HELP = "Boot the chassis, run the lifecycle, then stop cleanly."


def wizard_command() -> CliCommand:
    """Return the root-wizard command; hidden from the wizard itself."""
    return CliCommand(
        name="wizard",
        help="Open the interactive command wizard.",
        handler=_wizard_command,
        group="core",
        show_in_wizard=False,
    )


def _wizard_command(request: CliRequest) -> int:
    """Run the numbered-menu wizard over every registered command."""
    from .ux import UserExperience
    from .wizard import RootWizard

    surface = request.options.get("surface")
    commands = list(surface.commands()) if surface is not None else []
    if not commands:
        write_line(f"{TAG_NOTE} no commands are registered yet")
        return int(ExitCode.SUCCESS)
    ux = UserExperience(request.context.config)
    return RootWizard(ux, request.context, commands, options=dict(request.options)).run()


def core_commands(chassis: Chassis) -> list[CliCommand]:
    """Build the chassis command set bound to *chassis*."""
    return [
        CliCommand(
            name="version",
            help="Print the chassis and core API version.",
            handler=_version_command,
            group="core",
        ),
        CliCommand(
            name="doctor",
            help="Inspect the environment, configuration, logging, and modules.",
            args=(
                CliArgument(
                    ("--json",),
                    dest="as_json",
                    action="store_true",
                    help="Emit the report as JSON.",
                ),
            ),
            handler=_doctor_command,
            group="core",
        ),
        CliCommand(
            name="logs",
            help="Show, follow, truncate, or rotate the chassis log file.",
            args=(
                CliArgument(
                    ("-n", "--lines"),
                    dest="lines",
                    metavar="N",
                    type="int",
                    help="print the last N lines [50]",
                ),
                CliArgument(
                    ("-f", "--follow"),
                    dest="follow",
                    action="store_true",
                    help="stream newly appended lines until Ctrl-C",
                ),
                CliArgument(
                    ("--clear",),
                    dest="clear",
                    action="store_true",
                    help="truncate the log file (asks first)",
                ),
                CliArgument(
                    ("--rotate",),
                    dest="rotate",
                    action="store_true",
                    help="archive the log file and start a fresh one",
                ),
                CliArgument(
                    ("--raw",),
                    dest="raw",
                    action="store_true",
                    help="print only log lines, no decorations",
                ),
                CliArgument(
                    ("--timeout",),
                    dest="timeout",
                    metavar="SECONDS",
                    type="float",
                    help="stop --follow after SECONDS (CI friendly)",
                ),
            ),
            handler=_logs_command,
            group="core",
        ),
        CliCommand(
            name="init",
            help="Scaffold a new project from the chassis template.",
            args=(
                CliArgument(
                    ("name",),
                    nargs="?",
                    metavar="NAME",
                    help="project name; prompted when omitted",
                ),
                CliArgument(
                    ("--path",),
                    dest="path",
                    metavar="DIR",
                    help="target directory (default: ./<name>)",
                ),
                CliArgument(
                    ("--author",),
                    dest="author",
                    metavar="TEXT",
                    help="author or owner [Korodexios]",
                ),
                CliArgument(
                    ("--ecosystem",),
                    dest="ecosystem",
                    metavar="CHOICE",
                    help="python | fullstack (older word; --ui is the precise one)",
                ),
                CliArgument(
                    ("--ui",),
                    dest="ui",
                    metavar="TIER",
                    help="GUI tier: none | static | react [none]; see 'kdx ui guide'",
                ),
                CliArgument(
                    ("--container",),
                    dest="container",
                    action="store_true",
                    help="also write a Dockerfile, compose.yaml and .dockerignore",
                ),
                CliArgument(
                    ("--pm",),
                    dest="pm",
                    metavar="MANAGER",
                    help="package manager of the react tier: npm | bun | pnpm [npm]",
                ),
                CliArgument(
                    ("--minimal",),
                    dest="minimal",
                    action="store_true",
                    help="bare chassis: no example modules",
                ),
                CliArgument(
                    ("--force",),
                    dest="force",
                    action="store_true",
                    help="write into a directory that already holds files",
                ),
                CliArgument(
                    ("--chassis-source",),
                    dest="chassis_source",
                    metavar="CHOICE",
                    help="how the project resolves the chassis: git | index [git]",
                ),
            ),
            handler=_init_command,
            group="core",
        ),
        CliCommand(
            name="status",
            help="Query a running chassis over its control plane (local or remote).",
            args=(
                CliArgument(
                    ("--json",),
                    dest="as_json",
                    action="store_true",
                    help="Emit the live report as JSON.",
                ),
                CliArgument(
                    ("--target",),
                    dest="target",
                    metavar="URL",
                    help="query http://host:port directly, skipping the local discovery file",
                ),
                CliArgument(
                    ("--host",),
                    dest="target_host",
                    metavar="HOST",
                    help="query this host (with --port) instead of the local endpoint",
                ),
                CliArgument(
                    ("--port",),
                    dest="target_port",
                    metavar="PORT",
                    type="int",
                    help="port of --host [core.ipc.port]",
                ),
                CliArgument(
                    ("--token",),
                    dest="target_token",
                    metavar="TOKEN",
                    help="shared token for a remote endpoint with auth enabled",
                ),
            ),
            handler=_status_command,
            group="core",
        ),
        CliCommand(
            name="reload",
            help="Ask a running daemon to re-read its configuration (SIGHUP over IPC).",
            args=(
                CliArgument(
                    ("--json",),
                    dest="as_json",
                    action="store_true",
                    help="Emit the reload summary as JSON.",
                ),
                CliArgument(
                    ("--target",),
                    dest="target",
                    metavar="URL",
                    help="post to http://host:port directly, skipping the local discovery file",
                ),
                CliArgument(
                    ("--token",),
                    dest="target_token",
                    metavar="TOKEN",
                    help="shared token for a remote endpoint with auth enabled",
                ),
            ),
            handler=_reload_command,
            group="core",
        ),
        CliCommand(
            name="rpc",
            help="Run one module command inside a live daemon instead of booting a chassis.",
            args=(
                CliArgument(
                    ("rpc_command",),
                    metavar="COMMAND",
                    nargs="*",
                    help="module command path to delegate, for example: kdx rpc heartbeat status",
                ),
                CliArgument(
                    ("--args",),
                    dest="args_json",
                    metavar="JSON",
                    help="arguments as a JSON object, for example --args JSON",
                ),
                CliArgument(
                    ("--json",),
                    dest="as_json",
                    action="store_true",
                    help="Emit the daemon's reply as JSON.",
                ),
                CliArgument(
                    ("--background",),
                    dest="background",
                    action="store_true",
                    help="Start it as a job and return its id at once (see 'kdx jobs').",
                ),
                CliArgument(
                    ("-f", "--follow"),
                    dest="follow",
                    action="store_true",
                    help="Run it as a job and print its output live; no timeout.",
                ),
                CliArgument(
                    ("--target",),
                    dest="target",
                    metavar="URL",
                    help="post to http://host:port directly, skipping the local discovery file",
                ),
                CliArgument(
                    ("--token",),
                    dest="target_token",
                    metavar="TOKEN",
                    help="shared token for a remote endpoint with auth enabled",
                ),
            ),
            handler=_rpc_command,
            group="core",
        ),
        CliCommand(
            name="modules",
            help="Inspect auto-discovered modules.",
            group="core",
            subcommands=(
                CliCommand(
                    name="list",
                    help="List discovered modules and their state.",
                    args=(
                        CliArgument(
                            ("--json",),
                            dest="as_json",
                            action="store_true",
                            help="Emit the listing as JSON.",
                        ),
                    ),
                    handler=_modules_list_command,
                    module_name="core",
                ),
                CliCommand(
                    name="create",
                    help="Scaffold a new module package (asks for a name when omitted).",
                    args=(
                        CliArgument(
                            ("name",), nargs="?", help="Module name, for example backup_engine."
                        ),
                        CliArgument(
                            ("--path",),
                            dest="path",
                            metavar="DIR",
                            help="Directory to create the module in.",
                        ),
                        CliArgument(
                            ("--title",),
                            dest="title",
                            metavar="TEXT",
                            help="One-line module description.",
                        ),
                        CliArgument(
                            ("--force",),
                            dest="force",
                            action="store_true",
                            help="Replace an existing module directory.",
                        ),
                        CliArgument(
                            ("--json",),
                            dest="as_json",
                            action="store_true",
                            help="Emit the created files as JSON.",
                        ),
                    ),
                    handler=_modules_create_command,
                    module_name="core",
                ),
                CliCommand(
                    name="show",
                    help="Show one module in detail (initializes modules).",
                    args=(
                        CliArgument(("name",), nargs="?", help="Module name; omit for all."),
                        CliArgument(
                            ("--json",),
                            dest="as_json",
                            action="store_true",
                            help="Emit the detail as JSON.",
                        ),
                    ),
                    handler=_modules_show_command,
                    module_name="core",
                ),
            ),
        ),
        CliCommand(
            name="module",
            help="Create modules (kdx module new NAME writes the module trio and a test).",
            group="core",
            subcommands=(
                CliCommand(
                    name="new",
                    help="Scaffold a module in the project's modules directory, with a test.",
                    args=(
                        CliArgument(
                            ("name",), nargs="?", help="Module name, for example backup_engine."
                        ),
                        CliArgument(
                            ("--description", "--title"),
                            dest="title",
                            metavar="TEXT",
                            help="One-line module description.",
                        ),
                        CliArgument(
                            ("--path",),
                            dest="path",
                            metavar="DIR",
                            help="Directory to create the module in.",
                        ),
                        CliArgument(
                            ("--force",),
                            dest="force",
                            action="store_true",
                            help="Replace an existing module directory.",
                        ),
                        CliArgument(
                            ("--json",),
                            dest="as_json",
                            action="store_true",
                            help="Emit the created files as JSON.",
                        ),
                    ),
                    handler=_modules_create_command,
                    module_name="core",
                ),
            ),
        ),
        CliCommand(
            name="config",
            help="Show, prove, and validate the resolved configuration.",
            group="core",
            subcommands=(
                CliCommand(
                    name="show",
                    help="Print every resolved key with the layer that won.",
                    args=(
                        CliArgument(
                            ("--key",), metavar="KEY", help="Only show keys starting with KEY."
                        ),
                        CliArgument(
                            ("--json",),
                            dest="as_json",
                            action="store_true",
                            help="Emit the mapping as JSON.",
                        ),
                        CliArgument(
                            ("--reveal",),
                            dest="reveal",
                            action="store_true",
                            help="print sensitive values in clear instead of masked",
                        ),
                    ),
                    handler=_config_show_command,
                    module_name="core",
                ),
                CliCommand(
                    name="file",
                    help="Print the discovered config and env file paths.",
                    handler=_config_file_command,
                    module_name="core",
                ),
                CliCommand(
                    name="check",
                    help="Report keys nothing reads; exit 3 when a key is a typo.",
                    args=(
                        CliArgument(
                            ("--json",),
                            dest="as_json",
                            action="store_true",
                            help="Emit the report as JSON.",
                        ),
                    ),
                    handler=_config_check_command,
                    module_name="core",
                ),
                *edit_commands(),
            ),
        ),
        CliCommand(
            name="run",
            help=RUN_HELP,
            args=(
                CliArgument(
                    ("--once",),
                    action="store_true",
                    help="Publish one tick and stop (CI and smoke tests).",
                ),
                CliArgument(
                    ("--duration",),
                    metavar="SECONDS",
                    type="float",
                    help="Stop automatically after SECONDS.",
                ),
                CliArgument(
                    ("--host",),
                    dest="ipc_host",
                    metavar="HOST",
                    help="bind the control plane here (0.0.0.0 for LAN access)",
                ),
                CliArgument(
                    ("--port",),
                    dest="ipc_port",
                    metavar="PORT",
                    type="int",
                    help="control-plane port (0 for an ephemeral port)",
                ),
            ),
            handler=_run_command,
            group="core",
        ),
        CliCommand(
            name="contracts",
            help="Verify the project's declared file set without rewriting it.",
            group="core",
            subcommands=(
                CliCommand(
                    name="check",
                    help="Hash every declared file and report drift; exit 3 on drift.",
                    args=(
                        CliArgument(
                            ("--json",),
                            dest="as_json",
                            action="store_true",
                            help="Emit the report as JSON.",
                        ),
                    ),
                    handler=_contracts_check_command,
                    module_name="core",
                ),
            ),
        ),
    ]


def _version_command(request: CliRequest) -> int:
    """Print the version banner."""
    write_line(format_version())
    return int(ExitCode.SUCCESS)


def _doctor_command(request: CliRequest) -> int:
    """Render the environment inspection report.

    The report asks each module for diagnostics and health, so the chassis initializes
    the discovered modules first: a hook that reads its configuration or paths needs the
    context `init(context)` handed it, and a module that was only discovered would
    otherwise be asked too early.
    """
    chassis = _chassis(request)
    chassis.ensure_initialized()
    report = chassis.health_report()
    interactive = _interactivity(chassis)
    report["interface"] = interactive
    language = request.context.i18n
    report["language"] = {"locale": language.locale, "available": list(language.locales())}
    if request.flag("as_json"):
        dump_json(report)
        return int(ExitCode.SUCCESS)

    print_section("Chassis")
    print_key_values(
        [
            ("app", f"{report['app']['name']} ({report['app']['distribution']})"),
            ("version", report["app"]["version"]),
            ("core api", report["app"]["core_api"]),
            ("state", report["app"]["state"]),
            ("uptime", f"{report['app']['uptime_seconds']:.3f} s"),
        ]
    )
    project = dict(report.get("project") or {})
    contracts = dict(report.get("contracts") or {})
    print_section("Project identity")
    print_key_values(
        [
            ("name", project.get("name", "(unknown)")),
            ("version", project.get("version", "unknown")),
            ("contract directory", project.get("contract_dir", "-")),
        ]
    )
    if contracts.get("declared"):
        drifted = [dict(item) for item in (contracts.get("drifted") or [])]
        missing = [str(item) for item in (contracts.get("missing") or [])]
        print_section("Contracts")
        print_key_values(
            [
                ("contract directory", project.get("contract_dir", "-")),
                ("declared", contracts.get("declared", 0)),
                ("match", len(list(contracts.get("verified") or []))),
                ("drifted", len(drifted)),
                ("missing", len(missing)),
            ]
        )
        if drifted or missing:
            print_table(
                [["drift", str(row.get("path", ""))] for row in drifted[:8]]
                + [["missing", path] for path in missing[:8]],
                headers=("state", "path"),
            )

    print_section("Runtime")
    print_key_values(
        [
            ("python", report["runtime"]["python"]),
            ("executable", report["runtime"]["executable"]),
            ("platform", report["runtime"]["platform"]),
            ("pid", report["runtime"]["pid"]),
            ("cwd", report["runtime"]["cwd"]),
            ("stdout encoding", report["runtime"]["encoding"]["stdout"]),
            ("stderr encoding", report["runtime"]["encoding"]["stderr"]),
            ("stdin tty", interactive["stdin_tty"]),
            ("stdout tty", interactive["stdout_tty"]),
            ("interactive mode", format_bool(interactive["interactive"])),
        ]
    )
    print_section("Language")
    print_key_values(
        [
            ("active", language.locale),
            ("available", ", ".join(language.locales())),
        ]
    )
    print_section("Paths")
    print_key_values([(key, value) for key, value in report["paths"].items()])
    print_section("Configuration")
    print_key_values(
        [
            ("config file", report["config"]["file"]),
            ("env file", report["config"]["env_file"]),
            ("env prefix", report["config"]["prefix"]),
            ("known keys", report["config"]["keys"]),
        ]
    )
    print_section("Logging")
    print_key_values([(key, value) for key, value in report["logging"].items()])
    print_section("Signals")
    print_key_values([(key, value) for key, value in report["signals"].items()])
    ipc = dict(report.get("ipc") or {})
    memory = dict(report.get("memory") or {})
    print_section("Control plane")
    print_key_values(
        [
            ("running", format_bool(bool(ipc.get("running")))),
            (
                "endpoint",
                f"{ipc.get('host', '-')}:{ipc.get('port', '-')}" if ipc.get("running") else "-",
            ),
            ("requests served", ipc.get("requests", 0)),
            ("errors served", ipc.get("errors", 0)),
            ("discovery file", report.get("ipc_file", "-")),
            (
                "module routes",
                ", ".join(str(route.get("path")) for route in ipc.get("routes") or [])
                or "(none)",
            ),
            ("process memory", memory.get("rss", "-")),
        ]
    )
    instance = dict(report.get("instance") or {})
    async_block = dict(report.get("async") or {})
    supervision = dict(report.get("supervision") or {})
    diagnostics = dict(report.get("diagnostics") or {})
    print_section("Instance and concurrency")
    holder = instance.get("holder")
    print_key_values(
        [
            ("instance lock", str(instance.get("lock", "-"))),
            ("lock held", format_bool(bool(instance.get("held")))),
            ("lock holder", str((holder or {}).get("pid", "-")) if holder else "(free)"),
            ("daemon", str(instance.get("daemon") or "(not running)")),
            ("asyncio loop", format_bool(bool(async_block.get("running")))),
            (
                "async tasks",
                f"submitted={async_block.get('submitted', 0)} "
                f"completed={async_block.get('completed', 0)} "
                f"failed={async_block.get('failed', 0)}",
            ),
            ("workers watched", supervision.get("watched", 0)),
            ("workers dead", supervision.get("dead", 0) or "(none)"),
        ]
    )
    tasks = dict(report.get("scheduler") or {})
    locks = dict(report.get("locks") or {})
    mounts = list(report.get("static") or [])
    stale_locks = ", ".join(str(name) for name in locks.get("stale") or [])
    print_section("Chassis primitives")
    print_key_values(
        [
            (
                "scheduled tasks",
                f"scheduled={tasks.get('scheduled', 0)} runs={tasks.get('runs', 0)} "
                f"failures={tasks.get('failures', 0)}"
                + ("" if tasks.get("enabled", True) else " (scheduler paused)"),
            ),
            (
                "task names",
                ", ".join(str(task.get("name")) for task in tasks.get("tasks") or []) or "(none)",
            ),
            (
                "named locks",
                f"held={locks.get('held', 0)} on_disk={len(locks.get('on_disk') or [])}"
                + (f" stale={stale_locks}" if stale_locks else ""),
            ),
            (
                "lock names",
                ", ".join(str(name) for name in locks.get("names") or []) or "(none)",
            ),
            (
                "static mounts",
                ", ".join(str(mount.get("route_prefix")) for mount in mounts) or "(none)",
            ),
        ]
    )
    print_section("Diagnostics")
    checks = list(diagnostics.get("checks") or [])
    if not checks:
        write_status("no diagnostics findings", tag=TAG_OK)
    for item in checks:
        severity = str(item.get("severity", "info"))
        tag = TAG_ERROR if severity in ("error", "warning") else TAG_NOTE
        hint = f" [{item['hint']}]" if item.get("hint") else ""
        write_line(
            f"  {tag} {item.get('module', 'chassis')}/{item.get('name', '-')}: "
            f"{item.get('message', '')}{hint}"
        )
    print_section("Modules")
    modules = report["modules"]
    dependencies = chassis.registry.dependency_report()
    print_key_values(
        [
            ("total", modules["total"]),
            ("failed", modules["failed"] or "(none)"),
            ("unknown dependencies", ", ".join(dependencies["missing"]) or "(none)"),
            ("dependency cycles", ", ".join(dependencies["cycles"]) or "(none)"),
        ]
    )
    if modules["modules"]:
        print_table(
            [
                [
                    item["name"],
                    item["version"],
                    item["state"],
                    item["origin"],
                    ", ".join(item["commands"]) or "-",
                    ", ".join(item.get("depends_on", [])) or "-",
                ]
                for item in modules["modules"]
            ],
            headers=("name", "version", "state", "origin", "commands", "depends"),
        )
    collisions = list(dict(report.get("cli") or {}).get("collisions") or [])
    if collisions:
        write_line()
        write_status("cli-collision: module commands shadowed by another owner:", tag=TAG_ERROR)
        print_table(
            [[row["name"], row["owner"], row["module"]] for row in collisions],
            headers=("command", "owner", "claimed by"),
        )
    if report["discovery"]["failures"]:
        write_line()
        write_status("module discovery problems:", tag=TAG_NOTE)
        for failure in report["discovery"]["failures"]:
            write_line(f"  {TAG_ERROR} {failure['name']} [{failure['stage']}] {failure['message']}")
    if modules["failed"] or diagnostics.get("errors") or collisions:
        return int(ExitCode.ERROR)
    return int(ExitCode.SUCCESS)


def _modules_list_command(request: CliRequest) -> int:
    """List discovered modules and name every module that was not loaded."""
    chassis = _chassis(request)
    health = chassis.registry.health()
    skipped = [{"name": name, "reason": reason} for name, reason in chassis.discovery.skipped]
    optional = [item.as_dict() for item in chassis.discovery.optional]
    failures = [
        {
            "name": failure.name,
            "stage": failure.stage,
            "message": failure.message,
            "origin": failure.origin,
        }
        for failure in chassis.discovery.failures
    ]
    if request.flag("as_json"):
        dump_json(
            {
                **health,
                "skipped": skipped,
                "optional": optional,
                "failures": failures,
            }
        )
        return int(ExitCode.ERROR) if health["failed"] else int(ExitCode.SUCCESS)
    if not health["modules"]:
        write_status("no modules discovered yet", tag=TAG_NOTE)
        return int(ExitCode.SUCCESS)
    print_table(
        [
            [
                item["name"],
                item["version"],
                # 'new' is the registry's word for a module this one-shot command never
                # started; the operator reads it as 'found, not running here'.
                "discovered" if item["state"] == "new" else item["state"],
                item["origin"],
                ", ".join(item.get("depends_on", [])) or "-",
                item["description"],
            ]
            for item in health["modules"]
        ],
        headers=("name", "version", "state", "origin", "depends", "description"),
    )
    write_line()
    write_line(f"{TAG_OK} {chassis.discovery.summary()}")
    if any(item["state"] == "new" for item in health["modules"]):
        write_line(f"{TAG_NOTE} the live state of a running daemon: 'kdx status' or 'kdx ui'")
    for failure in chassis.discovery.failures:
        write_line(f"  {TAG_ERROR} {failure.describe()}")
    for item in skipped:
        write_line(tr("  [skipped] {name}: {reason}", name=item['name'], reason=item['reason']))
    for item in optional:
        write_line(
            tr(
                "  [optional] {name}: needs {module} - {hint}",
                name=item['name'],
                module=item['module'],
                hint=item['hint'],
            )
        )
    return int(ExitCode.ERROR) if health["failed"] else int(ExitCode.SUCCESS)


def _logs_command(request: CliRequest) -> int:
    """Show, follow, truncate, or rotate the chassis log file."""
    import signal
    import time

    from ..logging.engine import (
        DEFAULT_TAIL_LINES,
        LogTailer,
        rotate_log,
        tail_lines,
        truncate_log,
    )
    from .ux import UserExperience

    chassis = _chassis(request)
    ux = UserExperience(chassis.config)
    log_path = Path(chassis.paths.log_dir) / chassis.config.get_str("core.log.file", "kdx.log")
    raw = bool(request.flag("raw"))
    follow = bool(request.flag("follow"))
    lines_value = request.option("lines")
    count = int(str(lines_value)) if lines_value is not None else DEFAULT_TAIL_LINES
    if count < 1:
        write_line(f"{TAG_ERROR} " + tr("--lines must be at least 1, got {count}", count=count))
        write_line(f"{TAG_NOTE} kdx logs -n 200")
        return int(ExitCode.ERROR)
    timeout_value = request.option("timeout")
    timeout = float(str(timeout_value)) if timeout_value is not None else None

    def emit(line: str) -> None:
        write_line(line)

    def note(message: str) -> None:
        if not raw:
            write_line(f"{TAG_NOTE} {message}")

    if request.flag("rotate") and request.flag("clear"):
        write_line(f"{TAG_ERROR} --rotate and --clear are mutually exclusive")
        note("kdx logs --rotate archives the file; --clear empties it")
        return int(ExitCode.USAGE_ERROR)

    if not log_path.is_file():
        reason = (
            "file logging is disabled (core.log.file_enabled=false)"
            if not chassis.config.get_bool("core.log.file_enabled", True)
            else "the chassis has not written a log line yet"
        )
        write_line(f"{TAG_NOTE} " + tr("no log file found at {log_path}", log_path=log_path))
        note(reason)
        return int(ExitCode.SUCCESS)

    if request.flag("rotate"):
        proceed = True
        if ux.is_interactive():
            proceed = ux.confirm(f"Rotate log file {log_path}?", default=False)
        if not proceed:
            note("log file left alone")
            return int(ExitCode.SUCCESS)
        archive, archived = rotate_log(
            log_path, backup_count=chassis.config.get_int("core.log.backup_count", 5)
        )
        write_status(
            tr(
                "log file rotated to {name} ({archived} bytes archived)",
                name=archive.name,
                archived=archived,
            )
        )
        return int(ExitCode.SUCCESS)

    if request.flag("clear"):
        proceed = True
        if ux.is_interactive():
            proceed = ux.confirm(f"Truncate log file {log_path}?", default=False)
        if not proceed:
            note("log file left alone")
            return int(ExitCode.SUCCESS)
        removed = truncate_log(log_path)
        write_status(tr("log file truncated ({removed} bytes freed)", removed=removed))
        return int(ExitCode.SUCCESS)

    if follow:
        poll = chassis.config.get_float("core.logs.poll_interval", 0.5)
        tailer = LogTailer(log_path, poll_interval=poll)
        for line in tail_lines(log_path, min(count, 10)):
            emit(line)
        tailer.seek_end()
        deadline = time.monotonic() + timeout if timeout is not None and timeout > 0 else None
        note(f"following {log_path} (Ctrl-C to stop)")
        interrupted = False
        try:
            while not chassis.signals.shutdown_requested:
                for line in tailer.read_new():
                    emit(line)
                if deadline is not None and time.monotonic() >= deadline:
                    break
                tailer.wait()
        except KeyboardInterrupt:
            interrupted = True
        # The chassis owns the signal handlers, so Ctrl-C arrives as a shutdown
        # request rather than a KeyboardInterrupt; honour the recorded signal.
        signalled = chassis.signals.shutdown_requested
        stop_signal = chassis.signals.signal_number if signalled else None
        if interrupted or signalled:
            note(f"stopped following {log_path.name}")
            return int(ExitCode.from_signal(stop_signal or signal.SIGINT))
        return int(ExitCode.SUCCESS)

    if not raw:
        note(f"{log_path} ({log_path.stat().st_size} bytes)")
    for line in tail_lines(log_path, count):
        emit(line)
    return int(ExitCode.SUCCESS)


def _init_command(request: CliRequest) -> int:
    """Scaffold a brand-new chassis project from a template."""
    from .init_project import (
        CHASSIS_SOURCES,
        ECOSYSTEMS,
        ProjectError,
        init_project,
        prompt_project,
    )
    from .ux import UserExperience

    chassis = _chassis(request)
    ux = UserExperience(chassis.config)
    try:
        project = prompt_project(
            ux,
            name=str(request.option("name") or "").strip(),
            author=str(request.option("author") or "").strip(),
            ecosystem=str(request.option("ecosystem") or "").strip(),
            minimal=bool(request.flag("minimal")),
            chassis_source=str(request.option("chassis_source") or "").strip(),
            ui=str(request.option("ui") or "").strip(),
            pm=str(request.option("pm") or "").strip(),
            container=bool(request.flag("container")),
        )
        target_text = str(request.option("path") or "").strip()
        target = Path(target_text).expanduser() if target_text else Path.cwd() / project.package
        result = init_project(target, project, force=bool(request.flag("force")))
    except ProjectError as exc:
        write_line(f"{TAG_ERROR} {exc}")
        if exc.hint:
            write_line(f"{TAG_NOTE} {exc.hint}")
        return int(ExitCode.ERROR)
    write_status(tr("project {name} created", name=project.name))
    print_key_values(
        [
            ("root", str(result.root)),
            ("package", project.package),
            ("ecosystem", ECOSYSTEMS[project.ecosystem]),
            ("ui", project.ui),
            ("container", "Dockerfile + compose.yaml" if project.container else "no"),
            ("modules", "(bare chassis)" if project.minimal else "app"),
            ("files written", len(result.files)),
            ("chassis", f"korodexios-core {project.core_version}"),
            ("chassis dependency", CHASSIS_SOURCES[project.chassis_source]),
        ]
    )
    write_line(
        f"{TAG_NOTE} "
        + tr(
            "next: cd {root} && uv sync --extra dev && uv run {script} run",
            root=result.root,
            script=project.script,
        )
    )
    write_line(
        f"{TAG_NOTE} "
        + tr("without uv: pip install -e '.[dev]' && {script} run", script=project.script)
    )
    return int(ExitCode.SUCCESS)


def _status_command(request: CliRequest) -> int:
    """Query a running chassis through its control plane, locally or over the LAN."""
    chassis = _chassis(request)
    timeout = float(chassis.config.get_float("core.ipc.timeout", 2.0))
    default_port = int(chassis.config.get_int("core.ipc.port", 18123)) or DEFAULT_PORT
    discovery_path = Path(chassis.paths.state_dir) / DISCOVERY_NAME
    target = str(request.option("target") or "").strip()
    target_host = str(request.option("target_host") or "").strip()
    target_port = request.option("target_port")
    remote = bool(target or target_host)
    token = target_token(request, chassis, target or target_host)
    try:
        discovery: IpcDiscovery | None
        if target:
            discovery = IpcDiscovery.remote(
                target, default_port=default_port, token=token, cafile=_tls_ca(chassis)
            )
        elif target_host:
            address = target_host
            if target_port is not None:
                address = f"{target_host}:{int(target_port)}"
            discovery = IpcDiscovery.remote(
                address, default_port=default_port, token=token, cafile=_tls_ca(chassis)
            )
        else:
            discovery = IpcDiscovery.load(discovery_path)
    except IpcError as exc:
        write_line(f"{TAG_ERROR} {exc}")
        if exc.hint:
            write_line(f"{TAG_NOTE} {exc.hint}")
        return int(ExitCode.ERROR)
    if discovery is None or (not remote and not discovery.is_process_alive()):
        if request.flag("as_json"):
            dump_json({"running": False, "discovery_file": str(discovery_path)})
        else:
            write_line(f"{TAG_NOTE} chassis daemon is not running")
            write_line(
                f"{TAG_NOTE} "
                + tr("looked for {discovery_path}", discovery_path=discovery_path)
            )
        return int(ExitCode.SUCCESS)
    endpoint = discovery.base_url()
    try:
        code, payload = discovery.request(STATUS_PATH, timeout=timeout)
    except OSError as exc:
        write_line(
            f"{TAG_ERROR} "
            + tr("no control plane answering at {endpoint}", endpoint=endpoint)
        )
        write_line(f"{TAG_NOTE} {exc}")
        return int(ExitCode.ERROR)
    if code in (401, 403):
        write_line(
            f"{TAG_ERROR} "
            + tr("the daemon rejected this client ({endpoint} requires a token)", endpoint=endpoint)
        )
        if remote:
            write_line(
                f"{TAG_NOTE} pass --token TOKEN, or set core.ipc.token / KDX_CORE__IPC__TOKEN"
            )
        else:
            write_line(
                f"{TAG_NOTE} "
                + tr(
                    "start 'kdx run' again so {discovery_path} is refreshed",
                    discovery_path=discovery_path,
                )
            )
        return int(ExitCode.ERROR)
    if code != 200 or not isinstance(payload, dict):
        write_line(f"{TAG_ERROR} " + tr("the daemon answered HTTP {code}", code=code))
        return int(ExitCode.ERROR)
    if request.flag("as_json"):
        dump_json(payload)
        return int(ExitCode.SUCCESS)
    app = dict(payload.get("app") or {})
    modules = dict(payload.get("modules") or {})
    bus = dict(payload.get("bus") or {})
    memory = dict(payload.get("memory") or {})
    ipc = dict(payload.get("ipc") or {})
    print_section("Chassis")
    print_key_values(
        [
            ("state", app.get("state", "-")),
            ("uptime", f"{float(app.get('uptime_seconds', 0.0) or 0.0):.1f} s"),
            ("pid", ipc.get("pid", "-")),
            ("endpoint", endpoint),
            ("auth", "token required" if ipc.get("auth") else "anonymous reads"),
            (
                "shutdown",
                "anonymous"
                if ipc.get("allow_anonymous_shutdown") and not ipc.get("auth")
                else "token required",
            ),
            (
                "source",
                f"target {endpoint}" if remote else f"discovery file {discovery_path}",
            ),
        ]
    )
    print_section("Modules")
    rows = [
        [item.get("name"), item.get("state"), ", ".join(item.get("commands") or []) or "-"]
        for item in list(modules.get("modules") or [])
    ]
    print_key_values(
        [("total", modules.get("total", 0)), ("failed", modules.get("failed") or "(none)")]
    )
    if rows:
        print_table(rows, headers=("name", "state", "commands"))
    print_section("Event bus")
    print_key_values(
        [
            ("published", bus.get("published", 0)),
            ("delivered", bus.get("delivered", 0)),
            ("handler errors", bus.get("errors", 0)),
            ("slow handlers", bus.get("slow_handlers", 0)),
            ("subscriptions", bus.get("subscriptions", 0)),
        ]
    )
    print_section("Memory")
    print_key_values([("resident", memory.get("rss", "-")), ("source", memory.get("source", "-"))])
    return int(ExitCode.SUCCESS)


def _default_module_root(chassis: Chassis) -> Path:
    """Return where a new module goes: the project's own ``src/<pkg>/modules``, else the chassis."""
    src = Path(chassis.paths.root) / "src"
    found = sorted(src.glob("*/modules")) if src.is_dir() else []
    found = [item for item in found if item.is_dir() and item.parent.name != "korodexios_core"]
    return found[0] if len(found) == 1 else Path(chassis.paths.builtin_modules_dir)


def _modules_create_command(request: CliRequest) -> int:
    """Scaffold a new module package and report what landed on disk."""
    from .scaffold import ScaffoldError, scaffold_module, validate_module_name
    from .ux import UserExperience

    chassis = _chassis(request)
    ux = UserExperience(chassis.config)
    raw_name = str(request.option("name") or "").strip()
    if not raw_name:
        raw_name = ux.ask_text("Module name", default="", required=True, example="backup_engine")
    try:
        name = validate_module_name(raw_name)
    except ScaffoldError as exc:
        write_line(f"{TAG_ERROR} {exc}")
        if exc.hint:
            write_line(f"{TAG_NOTE} {exc.hint}")
        return int(ExitCode.ERROR)
    raw_path = str(request.option("path") or "").strip()
    parent = Path(raw_path).expanduser() if raw_path else _default_module_root(chassis)
    if not parent.is_absolute():
        parent = Path.cwd() / parent
    title = str(request.option("title") or "").strip()
    force = bool(request.flag("force"))
    directory = parent / name
    exists = directory.exists() and any(directory.iterdir())
    if exists and not force:
        if not ux.interactive:
            write_line(
                f"{TAG_ERROR} "
                + tr("{directory} already holds a module", directory=directory)
            )
            write_line(f"{TAG_NOTE} pass --force to replace its files")
            return int(ExitCode.ERROR)
        if not ux.confirm(f"Replace existing module {name}?", default=False):
            write_line(f"{TAG_NOTE} nothing written")
            return int(ExitCode.SUCCESS)
    try:
        result = scaffold_module(
            parent, name, title=title, force=force, project_root=chassis.paths.root
        )
    except ScaffoldError as exc:
        write_line(f"{TAG_ERROR} {exc}")
        if exc.hint:
            write_line(f"{TAG_NOTE} {exc.hint}")
        return int(ExitCode.ERROR)
    rows: list[tuple[str, Any]] = [
        ("directory", str(result.directory)),
        ("files", ", ".join(item.name for item in result.created)),
    ]
    if result.test_file is not None:
        rows.append(("test", str(result.test_file)))
    if request.flag("as_json"):
        dump_json({"module": name, **result.as_dict()})
        return int(ExitCode.SUCCESS)
    write_status(tr("module {name} created", name=name))
    print_key_values(rows)
    builtin = Path(chassis.paths.builtin_modules_dir)
    if result.directory.parent.resolve() != builtin.resolve():
        write_line(
            f"{TAG_NOTE} "
            + tr(
                "discovery needs {parent} in core.modules.extra_paths or passed as --modules-dir",
                parent=result.directory.parent,
            )
        )
    write_line(f"{TAG_NOTE} next: kdx modules list")
    return int(ExitCode.SUCCESS)


def _modules_show_command(request: CliRequest) -> int:
    """Show one (or all) modules in detail, initializing on demand."""
    chassis = _chassis(request)
    chassis.ensure_initialized()
    name = request.option("name")
    records = [chassis.registry.require(str(name))] if name else list(chassis.registry.ordered())
    payload: list[dict[str, Any]] = []
    for record in records:
        detail = record.as_dict()
        health_hook = getattr(record.module, "health", None)
        if callable(health_hook):
            try:
                detail["health"] = health_hook()
            except Exception as exc:
                detail["health_error"] = f"{type(exc).__name__}: {exc}"
        detail["spec"] = record.spec.as_dict()
        payload.append(detail)
    if request.flag("as_json"):
        dump_json(payload)
        return int(ExitCode.SUCCESS)
    if not payload:
        write_status("no modules registered", tag=TAG_NOTE)
        return int(ExitCode.SUCCESS)
    for detail in payload:
        print_section(str(detail["name"]))
        print_key_values(
            [
                ("version", detail["version"]),
                ("description", detail["description"]),
                ("state", detail["state"]),
                ("origin", detail["origin"]),
                ("priority", detail["priority"]),
                ("source", detail["spec"]["source"]),
                ("entrypoint", detail["spec"]["entrypoint"]),
                ("requires core", detail["spec"]["requires_core"]),
                ("commands", detail["commands"] or "(none)"),
                ("timings", detail["timings_ms"]),
                ("error", detail["error"]),
                ("health", detail.get("health", "(not reported)")),
            ]
        )
    return int(ExitCode.SUCCESS)


def _contracts_check_command(request: CliRequest) -> int:
    """Verify the declared file set; the chassis reads those files and rewrites none."""
    chassis = _chassis(request)
    chassis.ensure_initialized()
    report = chassis.contract_report()
    owners: dict[str, list[dict[str, str]]] = {}
    for name, row in chassis.declared_contracts():
        owners.setdefault(name, []).append({"path": row.path, "sha256": row.sha256})
    if request.flag("as_json"):
        dump_json(
            {
                "project": chassis.project.as_dict() if chassis.project is not None else {},
                "declared_by": owners,
                **report.as_dict(),
            }
        )
    else:
        identity = chassis.project
        print_section("Project identity")
        print_key_values(
            [
                ("name", identity.name if identity else "(unknown)"),
                ("version", identity.version if identity else "unknown"),
                ("contract directory", identity.contract_dir if identity else "-"),
            ]
        )
        if not report.rows:
            write_status("no project or module declared a contract file set", tag=TAG_NOTE)
            return int(ExitCode.SUCCESS)
        drifted = [dict(item) for item in report.as_dict()["drifted"]]
        missing = [str(item) for item in report.missing]
        print_section("Contracts")
        print_key_values(
            [
                ("declared", len(report.rows)),
                ("match", len(report.verified)),
                ("drifted", len(drifted)),
                ("missing", len(missing)),
            ]
        )
        named = drifted[:CONTRACT_NAMED_ROWS]
        rest = missing[:CONTRACT_NAMED_ROWS]
        if named or rest:
            print_table(
                [["drift", str(entry.get("path", ""))] for entry in named]
                + [["missing", path] for path in rest],
                headers=("state", "path"),
            )
        for entry in named:
            write_line(
                f"{TAG_ERROR} "
                + tr(
                    "{path}: declared {declared}..., read {actual}...",
                    path=entry.get("path"),
                    declared=str(entry.get("declared", ""))[:12],
                    actual=str(entry.get("actual", ""))[:12],
                )
            )
        for path in rest:
            write_line(f"{TAG_ERROR} " + tr("{path}: declared but not found", path=path))
        hidden = (len(drifted) + len(missing)) - CONTRACT_NAMED_ROWS * 2
        if hidden > 0:
            write_line(
                f"{TAG_NOTE} "
                + tr("+{hidden} more findings; pass --json for all of them", hidden=hidden)
            )
        write_status(
            tr("contracts: {summary}", summary=report.summary()),
            tag=TAG_OK if report.ok else TAG_ERROR,
        )
        write_line(f"{TAG_NOTE} the chassis reads these files and never rewrites one")
    if not report.ok:
        if report.drifted:
            first = report.drifted[0]
            detail = (
                f"{first.path} declares {first.declared[:12]}..., the file hashes to "
                f"{first.actual[:12]}..."
            )
        else:
            detail = f"{report.missing[0]} is declared and missing"
        raise ConfigurationError(
            f"contracts drifted: {report.summary()}",
            hint=detail + " - fix the file or the declaration; the chassis never rewrites one",
        )
    return int(ExitCode.SUCCESS)

def _config_show_command(request: CliRequest) -> int:
    """Print resolved configuration keys with provenance, secrets masked."""
    chassis = _chassis(request)
    prefix = str(request.option("key") or "").strip()
    reveal = bool(request.flag("reveal"))
    rows = [
        row
        for row in chassis.config.describe_rows(redact=not reveal)
        if not prefix or row[0].startswith(prefix)
    ]
    if request.flag("as_json"):
        dump_json({row[0]: {"value": row[1], "source": row[2]} for row in rows})
        return int(ExitCode.SUCCESS)
    if not rows:
        write_status(
            tr("no configuration keys start with {prefix}", prefix=repr(prefix)),
            tag=TAG_NOTE,
        )
        return int(ExitCode.SUCCESS)
    print_table(rows, headers=("key", "value", "source"))
    write_line()
    write_line(f"{TAG_NOTE} precedence: CLI > environment > config file > built-in defaults")
    if not reveal:
        write_line(
            f"{TAG_NOTE} sensitive values are masked; pass --reveal to print them in clear"
        )
    sentinels = chassis.config.sentinel_tokens()
    if sentinels:
        write_line(
            f"{TAG_NOTE} sentinel values fall through to the layer below: "
            + ", ".join(sentinels)
        )
    return int(ExitCode.SUCCESS)


def _config_file_command(request: CliRequest) -> int:
    """Print the discovered config and env file paths."""
    chassis = _chassis(request)
    print_key_values(
        [
            ("root", str(chassis.paths.root)),
            (
                "config file",
                str(chassis.paths.config_file) if chassis.paths.config_file else "(none)",
            ),
            ("env file", str(chassis.paths.env_file) if chassis.paths.env_file else "(none)"),
            ("env prefix", chassis.config.prefix),
        ]
    )
    return int(ExitCode.SUCCESS)


def _config_check_command(request: CliRequest) -> int:
    """Report keys the chassis does not read and module schema violations."""
    chassis = _chassis(request)
    unknown = chassis.config.unknown_keys(chassis.registry.names())
    project_keys = list(chassis.config.project_keys())
    issues = chassis.registry.validate_schemas(chassis.config)
    report = {
        "config_file": (
            str(chassis.config.config_file) if chassis.config.config_file else None
        ),
        "checked": len(chassis.config.keys()),
        "unknown": [
            {
                "key": key,
                "origin": chassis.config.origin_of(key),
                "suggestion": hint,
                "advice": _advice_for(hint),
            }
            for key, hint in unknown
        ],
        "schemas": {
            "modules": len(chassis.registry.schemas()),
            "issues": [issue.as_dict() for issue in issues],
        },
        "project_keys": project_keys,
    }
    if request.flag("as_json"):
        dump_json(report)
        if unknown or issues:
            return int(ExitCode.CONFIG_ERROR)
        return int(ExitCode.SUCCESS)
    print_key_values(
        [
            ("config file", report["config_file"] or "(none: defaults only)"),
            ("keys checked", str(report["checked"])),
            ("unknown keys", str(len(unknown))),
            ("project keys", ", ".join(project_keys) or "(none)"),
        ]
    )
    declared = chassis.registry.schemas()
    write_line()
    print_section("Module configuration schemas")
    if not declared:
        write_status("no module declares a configuration schema", tag=TAG_NOTE)
    for name, schema in sorted(declared.items()):
        secrets = ", ".join(schema.secrets()) or "-"
        write_line(
            tr(
                "{name}: {count} keys, required={required} secrets={secrets}",
                name=name,
                count=len(schema.fields),
                required=", ".join(schema.required_keys()) or "-",
                secrets=secrets,
            )
        )
    for key, hint in unknown:
        write_status(
            f"unknown configuration key {key} [{chassis.config.origin_of(key)}]"
            + _advice_for(hint),
            tag=TAG_ERROR,
        )
    for issue in issues:
        origin = issue.source or chassis.config.origin_of(issue.key)
        if not origin or origin == "unknown":
            origin = "unset"
        write_status(f"{issue.key}: {issue.message} [{origin}]", tag=TAG_ERROR)
    if unknown or issues:
        write_status(
            tr("{count} configuration findings need attention", count=len(unknown) + len(issues)),
            tag=TAG_NOTE,
        )
        return int(ExitCode.CONFIG_ERROR)
    write_status("configuration is understood", tag=TAG_OK)
    return int(ExitCode.SUCCESS)


def target_token(request: CliRequest, chassis: Chassis, target: str) -> str:
    """Return the token a client command presents to *target*.

    '--token' always wins. Without it the project's own core.ipc.token is presented only
    to this machine: it is the built-in admin, and a typo in '--target' or a peer on the
    LAN must never receive it - least of all over plain http.
    """
    explicit = str(request.option("target_token") or "").strip()
    if explicit:
        return explicit
    local = str(chassis.config.get_str("core.ipc.token", "") or "").strip()
    if not target or not local:
        return local
    text = target if "://" in target else "http://" + target
    host = urllib.parse.urlsplit(text).hostname or ""
    if is_loopback_host(host):
        return local
    write_line(
        f"{TAG_NOTE} "
        + tr("core.ipc.token is not sent to {host}; pass --token for a remote daemon", host=host)
    )
    return ""


def _client_discovery(request: CliRequest, chassis: Chassis) -> tuple[IpcDiscovery | None, Path]:
    """Resolve the daemon endpoint a client-side command should talk to."""
    default_port = int(chassis.config.get_int("core.ipc.port", DEFAULT_PORT)) or DEFAULT_PORT
    target = str(request.option("target") or "").strip()
    token = target_token(request, chassis, target)
    discovery_path = Path(chassis.paths.state_dir) / DISCOVERY_NAME
    try:
        if target:
            remote_client = IpcDiscovery.remote(
                target, default_port=default_port, token=token, cafile=_tls_ca(chassis)
            )
            return remote_client, discovery_path
        return IpcDiscovery.load(discovery_path), discovery_path
    except IpcError as exc:
        write_line(f"{TAG_ERROR} {exc}")
        if exc.hint:
            write_line(f"{TAG_NOTE} {exc.hint}")
        return None, discovery_path

def _reload_command(request: CliRequest) -> int:
    """Ask a running daemon to re-read its configuration over the control plane."""
    chassis = _chassis(request)
    timeout = float(chassis.config.get_float("core.ipc.timeout", 2.0))
    discovery, discovery_path = _client_discovery(request, chassis)
    if discovery is None or not discovery.is_process_alive():
        write_line(f"{TAG_NOTE} chassis daemon is not running")
        write_line(
            f"{TAG_NOTE} "
            + tr("looked for {discovery_path}", discovery_path=discovery_path)
        )
        write_line(f"{TAG_NOTE} a SIGHUP to the daemon does the same thing")
        return int(ExitCode.SUCCESS)
    try:
        code, payload = discovery.request(RELOAD_PATH, method="POST", timeout=timeout)
    except OSError as exc:
        write_line(
            f"{TAG_ERROR} "
            + tr("no control plane answering at {base_url}", base_url=discovery.base_url())
        )
        write_line(f"{TAG_NOTE} {exc}")
        return int(ExitCode.ERROR)
    if code in (401, 403):
        write_line(f"{TAG_ERROR} the daemon rejected this client (token required)")
        return int(ExitCode.ERROR)
    if code >= 400:
        write_line(f"{TAG_ERROR} " + tr("reload failed: {payload}", payload=payload))
        return int(ExitCode.CONFIG_ERROR if code == 422 else ExitCode.ERROR)  # 422: bad config
    if request.flag("as_json"):
        dump_json(payload)
        return int(ExitCode.SUCCESS)
    changed = list(payload.get("changed") or [])
    print_key_values(
        [
            ("endpoint", discovery.base_url()),
            ("reloads", str(payload.get("reloads", 1))),
            ("keys changed", str(len(changed))),
            ("changed", ", ".join(changed[:8]) or "(none)"),
        ]
    )
    write_status("configuration reloaded; the daemon kept running", tag=TAG_OK)
    return int(ExitCode.SUCCESS)

def _rpc_command(request: CliRequest) -> int:
    """Delegate one module command to the live daemon over the control plane."""
    chassis = _chassis(request)
    timeout = float(chassis.config.get_float("core.ipc.timeout", 2.0))
    tokens = [str(item) for item in (request.option("rpc_command") or [])]
    if not tokens:
        write_line(f"{TAG_ERROR} kdx rpc needs a command to delegate")
        write_line(f"{TAG_NOTE} kdx rpc heartbeat status -- -n 5")
        return int(ExitCode.USAGE_ERROR)
    raw_args = request.option("args_json")
    args: dict[str, Any]
    if raw_args:
        try:
            decoded = jsonio.loads(str(raw_args))
        except ValueError as exc:
            write_line(f"{TAG_ERROR} " + tr("--args is not valid JSON: {exc}", exc=exc))
            return int(ExitCode.USAGE_ERROR)
        if not isinstance(decoded, dict):
            write_line(f"{TAG_ERROR} --args must be a JSON object")
            return int(ExitCode.USAGE_ERROR)
        args = {str(key): value for key, value in decoded.items()}
    else:
        args = {}
    discovery, discovery_path = _client_discovery(request, chassis)
    if discovery is None or not discovery.is_process_alive():
        write_line(f"{TAG_NOTE} no chassis daemon answering; run 'kdx run' first")
        write_line(
            f"{TAG_NOTE} "
            + tr("looked for {discovery_path}", discovery_path=discovery_path)
        )
        return int(ExitCode.ERROR)
    # The first token names the command; the rest travel as argv, untouched, so the
    # daemon walks subcommands from them and hands every other token to the handler as
    # typed - a path with dots, slashes, or spaces arrives whole.
    command = tokens[0]
    passed = args.get("argv")
    args["argv"] = [*tokens[1:], *(passed if isinstance(passed, list) else [])]
    if request.flag("background") or request.flag("follow"):
        return _rpc_as_job(request, discovery, command, args)
    try:
        code, payload = discovery.call(command, args, timeout=timeout)
    except OSError as exc:
        write_line(
            f"{TAG_ERROR} "
            + tr("no control plane answering at {base_url}", base_url=discovery.base_url())
        )
        write_line(f"{TAG_NOTE} {exc}")
        return int(ExitCode.ERROR)
    render_rpc_reply(payload, command, as_json=bool(request.flag("as_json")))
    if code in (401, 403):
        return int(ExitCode.ERROR)
    return int(ExitCode.SUCCESS) if code < 400 else int(ExitCode.ERROR)
def _rpc_as_job(
    request: CliRequest, discovery: IpcDiscovery, command: str, args: Mapping[str, Any]
) -> int:
    """Start the command as a background job; detach, or follow its output to the end."""
    from .jobs import follow_job, submit_background

    reply = submit_background(discovery, command, args)
    if reply is None:
        return int(ExitCode.ERROR)
    code, payload = reply
    if code != 202:
        render_rpc_reply(payload, command, as_json=bool(request.flag("as_json")))
        return int(ExitCode.ERROR)
    job_id = str(payload["job"]["id"])
    if request.flag("as_json") or not request.flag("follow"):
        if request.flag("as_json"):
            dump_json(payload)
        else:
            write_line(
                f"{TAG_OK} "
                + tr("started {job_id}: {command}", job_id=job_id, command=command)
            )
            write_line(
                f"{TAG_NOTE} "
                + tr("follow it: kdx jobs show {job_id} --follow", job_id=job_id)
            )
            write_line(f"{TAG_NOTE} " + tr("stop it:   kdx jobs cancel {job_id}", job_id=job_id))
        return int(ExitCode.SUCCESS)
    write_line(
        f"{TAG_NOTE} "
        + tr("{job_id} started; Ctrl+C stops watching, not the job", job_id=job_id)
    )
    return follow_job(discovery, job_id)


def render_rpc_reply(payload: Mapping[str, Any], command: str, *, as_json: bool) -> None:
    """Print a daemon RPC reply the same way whether 'kdx rpc' or '--remote' sent it."""
    if as_json:
        dump_json(payload)
        return
    ok = bool(payload.get("ok"))
    result = payload.get("result")
    if ok and isinstance(result, dict):
        output = str(result.get("output") or "")
        if output:
            write_line(output.rstrip("\n"))
        print_key_values(
            [
                ("command", str(result.get("path") or command)),
                ("exit code", str(result.get("exit_code", 0))),
                ("module", str(result.get("module", "-"))),
            ]
        )
        health = result.get("health")
        if isinstance(health, dict):
            print_key_values(
                [
                    ("state", str(health.get("state", "-"))),
                    ("version", str(health.get("version", "-"))),
                    ("error", str(health.get("error") or "(none)")),
                ]
            )
    elif not ok:
        write_status(str(payload.get("error") or "the daemon rejected the call"), tag=TAG_ERROR)
    else:
        write_status(f"{command}: {result}", tag=TAG_OK)
def _advice_for(hint: str) -> str:
    """Return the parenthetical hint for an unknown configuration key."""
    if not hint:
        return ""
    if hint.startswith("core."):
        return f" (did you mean {hint}?)"
    return f" (module {hint.partition('.')[2]} is discovered)"


def _run_command(request: CliRequest) -> int:
    """Boot the chassis, run the lifecycle, then stop cleanly."""
    chassis = _chassis(request)
    duration_value = request.option("duration")
    duration = float(duration_value) if duration_value is not None else None
    overrides: dict[str, Any] = {}
    host_value = request.option("ipc_host")
    if host_value:
        overrides["core.ipc.host"] = str(host_value)
    port_value = request.option("ipc_port")
    if port_value is not None:
        overrides["core.ipc.port"] = int(port_value)
    if overrides:
        chassis.config = chassis.config.with_cli_overrides(overrides)
    chassis.ensure_initialized()
    if chassis.state.value == "failed":
        for name in chassis.registry.health()["failed"]:
            write_line(f"{TAG_ERROR} module {name} failed to initialize", stream=sys.stderr)
    chassis.start(ipc=not request.flag("once"))
    try:
        if request.flag("once"):
            chassis.run_once()
        else:
            chassis.run_forever(duration=duration)
    except KeyboardInterrupt:  # pragma: no cover - handled by the signal path
        chassis.signals.request_shutdown()
    return int(chassis.shutdown())


def _tls_ca(chassis: Chassis) -> str:
    """Return the certificate a remote https call should trust (core.ipc.tls_ca)."""
    return str(chassis.config.get("core.ipc.tls_ca", "") or "").strip()


def _chassis(request: CliRequest) -> Chassis:
    chassis = getattr(request.context, "chassis", None) or request.options.get("chassis")
    if chassis is None:
        raise RuntimeError("core commands require a chassis bound to the request context")
    return cast("Chassis", chassis)


def _interactivity(chassis: Chassis) -> dict[str, Any]:
    def is_tty(name: str) -> bool:
        stream = getattr(sys, name, None)
        try:
            return bool(stream and stream.isatty())
        except (ValueError, OSError):
            return False

    return {
        "stdin_tty": is_tty("stdin"),
        "stdout_tty": is_tty("stdout"),
        "interactive": chassis.config.get_bool("core.cli.interactive", True),
        "color": chassis.config.get_bool("core.cli.color", True),
        "confirmations": chassis.config.get_bool("core.cli.confirmations", True),
    }

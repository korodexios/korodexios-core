"""'kdx service': start the daemon by itself when you log in, on every platform.

Each host has one native way to keep a user's program running, and the chassis uses it
rather than shipping a supervisor of its own:

- Windows: a Task Scheduler task that runs at logon, defined by XML (no 261-character
  command limit), started through pythonw.exe so no console window stays open, and
  restarted by Task Scheduler if it fails;
- Linux: a systemd user unit ('systemctl --user'), restarted on failure;
- macOS: a launchd LaunchAgent, restarted on failure.

All of them run '<python> -m korodexios_core --home <root> run' as the current user, with
no administrator rights. 'kdx service install --print' shows the definition and the
commands and changes nothing; 'uninstall' first stops the daemon cleanly over its
control plane, then removes the definition.
"""

from __future__ import annotations

import getpass
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast
from xml.sax.saxutils import escape

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.ipc import DISCOVERY_NAME, SHUTDOWN_PATH, IpcDiscovery
from .render import print_key_values, tr, write_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
TASK_FOLDER = "Korodexios"


def run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run one service-manager command and capture what it says."""
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=60, check=False)


@dataclass(frozen=True, slots=True)
class ServicePlan:
    """What installing the service means on this host: a file and the commands around it."""

    platform: str
    name: str
    definition_path: Path
    definition: str
    install: tuple[tuple[str, ...], ...]
    uninstall: tuple[tuple[str, ...], ...]
    status: tuple[str, ...]
    encoding: str = "utf-8"
    note: str = ""


def service_command() -> CliCommand:
    """Return the 'kdx service' group."""
    name_arg = CliArgument(
        ("--service",), dest="service_name", metavar="NAME",
        help="Service name [korodexios-<project>].",
    )
    return CliCommand(
        name="service",
        help="Start the daemon automatically at logon (Task Scheduler, systemd, launchd).",
        group="core",
        subcommands=(
            CliCommand(
                name="install",
                help="Register the daemon to start at logon, and start it now.",
                handler=_install,
                group="core",
                args=(
                    name_arg,
                    CliArgument(
                        ("--print",), dest="print_only", action="store_true",
                        help="Show the definition and commands; change nothing.",
                    ),
                ),
            ),
            CliCommand(
                name="uninstall",
                help="Stop the daemon cleanly and remove the logon registration.",
                handler=_uninstall,
                group="core",
                args=(name_arg,),
            ),
            CliCommand(
                name="status",
                help="Say whether the service is registered and the daemon answers.",
                handler=_status,
                group="core",
                args=(name_arg,),
            ),
        ),
    )


def service_name(chassis: Chassis, override: str | None) -> str:
    """Return a service name safe for every service manager: [a-z0-9-], 1-64 chars."""
    project = chassis.project.name if chassis.project else "core"
    default = project if str(project).lower().startswith("korodexios") else f"korodexios-{project}"
    raw = override or default
    clean = re.sub(r"[^a-z0-9-]+", "-", str(raw).lower()).strip("-")[:64]
    return clean or "korodexios"


def interpreter(platform: str) -> str:
    """Return the Python that runs the daemon; pythonw on Windows, so no console opens."""
    executable = Path(sys.executable)
    if platform == "windows":
        windowless = executable.with_name("pythonw.exe")
        if windowless.is_file():
            return str(windowless)
    return str(executable)


def daemon_arguments(root: Path) -> list[str]:
    return ["-m", "korodexios_core", "--home", str(root), "run"]


def build_plan(
    chassis: Chassis, name: str, *, platform: str | None = None, home: Path | None = None
) -> ServicePlan:
    """Describe the service for *platform* (default: this host) without touching anything."""
    system = platform or _platform()
    root = Path(chassis.paths.root)
    python = interpreter(system)
    arguments = daemon_arguments(root)
    user_home = home or Path.home()
    if system == "windows":
        task = f"\\{TASK_FOLDER}\\{name}"
        xml_path = Path(chassis.paths.state_dir) / "service" / f"{name}.xml"
        return ServicePlan(
            platform=system,
            name=task,
            definition_path=xml_path,
            definition=_task_xml(python, arguments, root, name),
            install=(
                ("schtasks", "/Create", "/TN", task, "/XML", str(xml_path), "/F"),
                ("schtasks", "/Run", "/TN", task),
            ),
            uninstall=(("schtasks", "/Delete", "/TN", task, "/F"),),
            status=("schtasks", "/Query", "/TN", task, "/FO", "LIST"),
            encoding="utf-16",
            note="starts at every logon of this user; no administrator rights needed",
        )
    if system == "macos":
        label = f"dev.korodexios.{name}"
        plist = user_home / "Library" / "LaunchAgents" / f"{label}.plist"
        domain = f"gui/{os.getuid()}" if hasattr(os, "getuid") else "gui/0"
        return ServicePlan(
            platform=system,
            name=label,
            definition_path=plist,
            definition=_launchd_plist(
                label, [python, *arguments], root, Path(chassis.paths.log_dir)
            ),
            install=(("launchctl", "bootstrap", domain, str(plist)),),
            uninstall=(("launchctl", "bootout", domain, str(plist)),),
            status=("launchctl", "print", f"{domain}/{label}"),
            note="a LaunchAgent: starts at login, restarted if it fails",
        )
    unit = f"{name}.service"
    unit_path = user_home / ".config" / "systemd" / "user" / unit
    return ServicePlan(
        platform="linux",
        name=unit,
        definition_path=unit_path,
        definition=_systemd_unit(name, [python, *arguments], root),
        install=(
            ("systemctl", "--user", "daemon-reload"),
            ("systemctl", "--user", "enable", "--now", unit),
        ),
        uninstall=(
            ("systemctl", "--user", "disable", "--now", unit),
            ("systemctl", "--user", "daemon-reload"),
        ),
        status=("systemctl", "--user", "is-active", unit),
        note="to start at boot without logging in: loginctl enable-linger " + _user(),
    )


def _platform() -> str:
    if os.name == "nt":
        return "windows"
    return "macos" if sys.platform == "darwin" else "linux"


def _user() -> str:
    try:
        return getpass.getuser()
    except (OSError, KeyError):  # pragma: no cover - a host with no user name
        return "USER"


def _windows_account() -> str:
    # The logon trigger needs DOMAIN\\user. The domain is a fact about the host, not
    # configuration, so it is read from the process environment like getpass reads it.
    domain = os.environ.get("USERDOMAIN", "").strip()
    user = _user()
    return f"{domain}\\{user}" if domain else user


def _task_xml(python: str, arguments: Sequence[str], root: Path, name: str) -> str:
    account = escape(_windows_account())
    # Task Scheduler splits the line the way CommandLineToArgvW does; list2cmdline is
    # its exact inverse, so a space, a quote, or a trailing backslash (a drive root) survives.
    args = subprocess.list2cmdline(list(arguments))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape(f"korodexios daemon '{name}' for {root}")}</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{account}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{account}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(python)}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(str(root))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _systemd_quote(part: str) -> str:
    # '%' starts a systemd specifier ('%h', '%u'); a literal one is written '%%'.
    escaped = part.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return '"' + escaped + '"'


def _systemd_value(text: str) -> str:
    """Return *text* safe for a plain systemd setting: specifiers escaped, one line."""
    return str(text).replace("%", "%%").replace("\n", " ")


def _systemd_unit(name: str, command: Sequence[str], root: Path) -> str:
    exec_start = " ".join(_systemd_quote(part) for part in command)
    return "\n".join(
        [
            "[Unit]",
            f"Description={_systemd_value(f'korodexios daemon {name} ({root})')}",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={_systemd_value(str(root))}",
            f"ExecStart={exec_start}",
            "Restart=on-failure",
            "RestartSec=5",
            "KillSignal=SIGTERM",
            "TimeoutStopSec=30",
            "SuccessExitStatus=130 143",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def _launchd_plist(
    label: str, command: Sequence[str], root: Path, log_dir: Path | None = None
) -> str:
    arguments = "\n".join(f"    <string>{escape(part)}</string>" for part in command)
    log = escape(str((log_dir or root / "var" / "log") / "launchd.log"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{escape(label)}</string>
  <key>ProgramArguments</key>
  <array>
{arguments}
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(str(root))}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>StandardOutPath</key>
  <string>{log}</string>
  <key>StandardErrorPath</key>
  <string>{log}</string>
</dict>
</plist>
"""


def _chassis(request: CliRequest) -> Chassis:
    return cast("Chassis", request.options.get("chassis") or request.context.chassis)


def _plan(request: CliRequest) -> ServicePlan:
    chassis = _chassis(request)
    return build_plan(chassis, service_name(chassis, request.option("service_name")))


def _runner(request: CliRequest) -> Runner:
    """Return the function that runs service-manager commands (tests replace run_command)."""
    return run_command


def _shown(command: Sequence[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def _install(request: CliRequest) -> int:
    plan = _plan(request)
    if request.flag("print_only"):
        print_key_values([("platform", plan.platform), ("service", plan.name),
                          ("definition", plan.definition_path)])
        write_line(plan.definition.rstrip("\n"))
        for command in plan.install:
            write_line(f"$ {_shown(command)}")
        write_line(f"{TAG_NOTE} --print changed nothing")
        return int(ExitCode.SUCCESS)
    plan.definition_path.parent.mkdir(parents=True, exist_ok=True)
    plan.definition_path.write_text(plan.definition, encoding=plan.encoding)
    run = _runner(request)
    for command in plan.install:
        try:
            completed = run(command)
        except (OSError, subprocess.SubprocessError) as exc:
            write_line(
                f"{TAG_ERROR} "
                + tr("{program} could not run: {exc}", program=command[0], exc=exc)
            )
            return int(ExitCode.ERROR)
        if completed.returncode != 0:
            reason = (completed.stderr or completed.stdout or "").strip()
            write_line(
                f"{TAG_ERROR} "
                + tr(
                    "{shown} failed: {value}",
                    shown=_shown(command),
                    value=reason or completed.returncode,
                )
            )
            return int(ExitCode.ERROR)
    print_key_values([("service", plan.name), ("definition", plan.definition_path)])
    write_line(f"{TAG_OK} the daemon starts at logon and is starting now")
    write_line(f"{TAG_NOTE} {plan.note}")
    write_line(f"{TAG_NOTE} check it: kdx service status   remove it: kdx service uninstall")
    return int(ExitCode.SUCCESS)


def _stop_daemon(chassis: Chassis) -> bool:
    """Stop a running daemon over its control plane; True when one was asked to stop."""
    discovery = IpcDiscovery.load(Path(chassis.paths.state_dir) / DISCOVERY_NAME)
    if discovery is None or not discovery.is_process_alive():
        return False
    try:
        code, _ = discovery.request(SHUTDOWN_PATH, method="POST", timeout=5)
    except OSError:
        return False
    return code == 200


def _uninstall(request: CliRequest) -> int:
    plan = _plan(request)
    if _stop_daemon(_chassis(request)):
        write_line(f"{TAG_OK} asked the running daemon to stop cleanly")
    run = _runner(request)
    failures = 0
    for command in plan.uninstall:
        try:
            completed = run(command)
        except (OSError, subprocess.SubprocessError) as exc:
            write_line(
                f"{TAG_ERROR} "
                + tr("{program} could not run: {exc}", program=command[0], exc=exc)
            )
            return int(ExitCode.ERROR)
        if completed.returncode != 0:
            failures += 1
            reason = (completed.stderr or completed.stdout or "").strip()
            write_line(f"{TAG_NOTE} {_shown(command)}: {reason or completed.returncode}")
    if plan.definition_path.exists():
        plan.definition_path.unlink()
    if failures == len(plan.uninstall):
        write_line(
            f"{TAG_NOTE} "
            + tr("{name} was not registered; nothing to remove", name=plan.name)
        )
        return int(ExitCode.SUCCESS)
    write_line(
        f"{TAG_OK} "
        + tr("{name} removed; the daemon no longer starts at logon", name=plan.name)
    )
    return int(ExitCode.SUCCESS)


def _status(request: CliRequest) -> int:
    plan = _plan(request)
    chassis = _chassis(request)
    try:
        completed = _runner(request)(plan.status)
        registered = completed.returncode == 0
        detail = (completed.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        registered, detail = False, str(exc)
    discovery = IpcDiscovery.load(Path(chassis.paths.state_dir) / DISCOVERY_NAME)
    alive = discovery is not None and discovery.is_process_alive()
    state = _state_line(plan.platform, detail) if registered else "not registered"
    daemon = f"answering at {discovery.base_url()}" if alive and discovery else "not running"
    print_key_values(
        [
            ("service", plan.name),
            ("registered", "yes" if registered else "no"),
            ("manager says", state),
            ("daemon", daemon),
        ]
    )
    if not registered:
        write_line(f"{TAG_NOTE} register it with: kdx service install")
    return int(ExitCode.SUCCESS)


def _state_line(platform: str, detail: str) -> str:
    if platform == "windows":
        for line in detail.splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() == "status":
                return value.strip() or "registered"
        return "registered"
    first = detail.splitlines()[0].strip() if detail else ""
    return first or "registered"

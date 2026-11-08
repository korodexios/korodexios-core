"""Terminal clients for a running daemon: jobs, the live event feed, and stop.

Everything the web console does against a daemon has a command here, so the terminal
never falls behind the GUI. Each command finds the daemon the way 'kdx status' does -
the local discovery file, or '--target URL --token T' for another machine.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..events.bus import matches
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.ipc import JOBS_PATH, SHUTDOWN_PATH, IpcDiscovery
from .render import dump_json, print_table, tr, write_line
from .rpc_exit import json_no_daemon

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

EVENTS_PATH = "/api/v1/ui/events"
FOLLOW_POLL_SECONDS = 0.5
ROUTINE_EVENTS = re.compile(r"^(core\.tick|core\.heartbeat|core\.task\.)")
FINISHED_STATES = ("done", "failed", "cancelled")

TARGET_ARGS = (
    CliArgument(
        ("--target",), dest="target", metavar="URL",
        help="talk to http(s)://host:port instead of the local daemon",
    ),
    CliArgument(
        ("--token",), dest="target_token", metavar="TOKEN",
        help="your token for that daemon (core.ipc.token or one from 'kdx users add')",
    ),
)
JSON_ARG = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
JOB_ID = CliArgument(("job_id",), metavar="JOB", help="Job id, for example job-3.")


def jobs_command() -> CliCommand:
    """Return the 'kdx jobs' group."""
    return CliCommand(
        name="jobs",
        help="List, follow, or cancel commands running in the background on the daemon.",
        group="core",
        subcommands=(
            CliCommand(
                name="list", help="List background jobs, newest first.",
                handler=_list, group="core", args=(JSON_ARG, *TARGET_ARGS),
            ),
            CliCommand(
                name="show", help="Show one job's state and output.",
                handler=_show, group="core",
                args=(
                    JOB_ID,
                    CliArgument(
                        ("-f", "--follow"), dest="follow", action="store_true",
                        help="Print new output as it arrives, until the job ends.",
                    ),
                    JSON_ARG,
                    *TARGET_ARGS,
                ),
            ),
            CliCommand(
                name="cancel", help="Ask a job to stop (its command decides when it can).",
                handler=_cancel, group="core", args=(JOB_ID, JSON_ARG, *TARGET_ARGS),
            ),
        ),
    )


def events_command() -> CliCommand:
    """Return 'kdx events': the daemon's bus events, live."""
    return CliCommand(
        name="events",
        help="Follow the daemon's live events (Ctrl+C to stop).",
        handler=_events,
        group="core",
        args=(
            CliArgument(
                ("pattern",), nargs="?", default="*", metavar="PATTERN",
                help="Event pattern: 'backup.*', 'core.job.**', '*' for all [*].",
            ),
            CliArgument(
                ("--all",), dest="show_all", action="store_true",
                help="Include routine ticks, heartbeats, and scheduler runs.",
            ),
            CliArgument(
                ("-n", "--count"), dest="count", type="int",
                help="Stop after this many events (scripts, tests).",
            ),
            CliArgument(("--json",), dest="as_json", action="store_true",
                        help="One JSON object per line."),
            *TARGET_ARGS,
        ),
    )


def stop_command() -> CliCommand:
    """Return 'kdx stop': the operator stop, exit 0 on the daemon's side."""
    return CliCommand(
        name="stop",
        help="Stop the running daemon cleanly (needs the admin role).",
        handler=_stop,
        group="core",
        args=TARGET_ARGS,
    )


def connect(request: CliRequest) -> IpcDiscovery | None:
    """Return a client for the daemon this command addresses, or say why there is none."""
    from .commands import _client_discovery

    chassis = cast("Chassis", request.options.get("chassis") or request.context.chassis)
    discovery, path = _client_discovery(request, chassis)
    if discovery is None or (not request.option("target") and not discovery.is_process_alive()):
        if request.flag("as_json"):
            json_no_daemon(path)
            return None
        write_line(f"{TAG_NOTE} no daemon is answering; start one with 'kdx run'")
        write_line(f"{TAG_NOTE} " + tr("looked for {path}", path=path))
        return None
    return discovery


def _call(
    discovery: IpcDiscovery, route: str, *, method: str = "GET", payload: Any = None
) -> tuple[int, Any] | None:
    try:
        return discovery.request(route, method=method, payload=payload, timeout=10)
    except OSError as exc:
        write_line(
            f"{TAG_ERROR} "
            + tr(
                "no control plane answering at {base_url}: {exc}",
                base_url=discovery.base_url(),
                exc=exc,
            )
        )
        return None


def _failed(code: int, payload: Any) -> int:
    message = payload.get("error") if isinstance(payload, Mapping) else None
    write_line(f"{TAG_ERROR} {message or f'HTTP {code}'}")
    hint = payload.get("hint") if isinstance(payload, Mapping) else None
    if hint:
        write_line(f"{TAG_NOTE} {hint}")
    return int(ExitCode.ERROR)


def _list(request: CliRequest) -> int:
    discovery = connect(request)
    reply = None if discovery is None else _call(discovery, JOBS_PATH)
    if reply is None:
        return int(ExitCode.ERROR)
    code, payload = reply
    if code != 200:
        return _failed(code, payload)
    if request.flag("as_json"):
        dump_json(payload)
        return 0
    jobs = payload.get("jobs", [])
    if not jobs:
        write_line(f"{TAG_NOTE} no background jobs yet; start one with: kdx rpc --follow COMMAND")
        return 0
    print_table(
        [
            [job["id"], job["state"], job.get("user") or "-", job["command"],
             "-" if job.get("exit_code") is None else str(job["exit_code"]), f"{job['seconds']}s"]
            for job in jobs
        ],
        headers=("id", "state", "user", "command", "exit", "time"),
    )
    return 0


def follow_job(
    discovery: IpcDiscovery,
    job_id: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Print a job's output as it grows; return its exit code once it ends."""
    shown = 0
    while True:
        reply = _call(discovery, f"{JOBS_PATH}?id={job_id}")
        if reply is None:
            return int(ExitCode.ERROR)
        code, job = reply
        if code != 200:
            return _failed(code, job)
        output = str(job.get("output") or "")
        if len(output) > shown:
            write_line(output[shown:].rstrip("\n"))
            shown = len(output)
        if job["state"] in FINISHED_STATES:
            if job.get("error"):
                write_line(f"{TAG_ERROR} {job['error']}")
            summary = f"{job_id} {job['state']} after {job['seconds']}s"
            write_line(f"{TAG_OK if job['state'] == 'done' else TAG_NOTE} {summary}")
            if job["state"] == "cancelled":
                return int(ExitCode.ABORTED)
            exit_code = job.get("exit_code")
            return int(exit_code) if exit_code is not None else int(ExitCode.ERROR)
        sleep(FOLLOW_POLL_SECONDS)


def _show(request: CliRequest) -> int:
    discovery = connect(request)
    if discovery is None:
        return int(ExitCode.ERROR)
    job_id = str(request.option("job_id"))
    if request.flag("follow") and not request.flag("as_json"):
        return follow_job(discovery, job_id)
    reply = _call(discovery, f"{JOBS_PATH}?id={job_id}")
    if reply is None:
        return int(ExitCode.ERROR)
    code, job = reply
    if code != 200:
        return _failed(code, job)
    if request.flag("as_json"):
        dump_json(job)
        return 0
    write_line(
        tr(
            "{id}  {state}  {command}  (by {value}, {seconds}s)",
            id=job['id'],
            state=job['state'],
            command=job['command'],
            value=job.get('user') or 'the daemon',
            seconds=job['seconds'],
        )
    )
    output = str(job.get("output") or "").rstrip("\n")
    if output:
        write_line(output)
    if job["state"] in ("running", "cancelling"):
        write_line(
            f"{TAG_NOTE} "
            + tr("still running; follow it with: kdx jobs show {job_id} --follow", job_id=job_id)
        )
    return 0


def _cancel(request: CliRequest) -> int:
    discovery = connect(request)
    job_id = str(request.option("job_id"))
    reply = None if discovery is None else _call(
        discovery, JOBS_PATH, method="POST", payload={"cancel": job_id}
    )
    if reply is None:
        return int(ExitCode.ERROR)
    code, job = reply
    if code != 200:
        return _failed(code, job)
    if request.flag("as_json"):
        dump_json(job)
        return 0
    if job["state"] == "cancelling":
        write_line(
            f"{TAG_OK} "
            + tr("asked {job_id} to stop; it ends at its next safe point", job_id=job_id)
        )
        write_line(
            f"{TAG_NOTE} "
            + tr("watch it with: kdx jobs show {job_id} --follow", job_id=job_id)
        )
    else:
        write_line(
            f"{TAG_NOTE} "
            + tr("{job_id} is already {state}; nothing to stop", job_id=job_id, state=job['state'])
        )
    return 0


def _events(request: CliRequest) -> int:
    discovery = connect(request)
    if discovery is None:
        return int(ExitCode.ERROR)
    pattern = str(request.option("pattern") or "*")
    show_all = bool(request.flag("show_all")) or pattern != "*"
    count = request.option("count")
    remaining = int(count) if count is not None else None
    as_json = bool(request.flag("as_json"))
    if not as_json:
        write_line(
            f"{TAG_NOTE} "
            + tr("following events at {base_url} (Ctrl+C to stop)", base_url=discovery.base_url())
        )
    try:
        for event in discovery.events(EVENTS_PATH):
            name = str(event.get("name", ""))
            if not matches(pattern, name) or (not show_all and ROUTINE_EVENTS.match(name)):
                continue
            if as_json:
                dump_json(event)
            else:
                stamp = time.strftime("%H:%M:%S", time.localtime(float(event.get("at", 0))))
                write_line(f"{stamp}  {name:<28} {event.get('source') or '-':<14} "
                           f"{_compact(event.get('payload'))}")
            if remaining is not None:
                remaining -= 1
                if remaining <= 0:
                    return 0
    except OSError as exc:
        code = getattr(exc, "code", None)
        if code in (401, 403):
            write_line(
                f"{TAG_ERROR} "
                + tr("the daemon refused the event feed (HTTP {code})", code=code)
            )
        else:
            write_line(f"{TAG_ERROR} " + tr("the event feed ended: {exc}", exc=exc))
        return int(ExitCode.ERROR)
    write_line(f"{TAG_NOTE} the daemon closed the feed (it is stopping)")
    return 0


def _compact(payload: Any) -> str:
    import json

    text = json.dumps(payload, default=str, separators=(",", ":"))
    return text if len(text) <= 120 else text[:117] + "..."


def _stop(request: CliRequest) -> int:
    discovery = connect(request)
    reply = None if discovery is None else _call(discovery, SHUTDOWN_PATH, method="POST")
    if reply is None:
        return int(ExitCode.ERROR)
    code, payload = reply
    if code != 200:
        return _failed(code, payload)
    assert discovery is not None
    write_line(
        f"{TAG_OK} "
        + tr("the daemon at {base_url} is stopping", base_url=discovery.base_url())
    )
    return 0


def submit_background(
    discovery: IpcDiscovery, command: str, args: Mapping[str, Any]
) -> tuple[int, Any] | None:
    """Start *command* as a job; return the raw (status, body) reply."""
    return _call(
        discovery, "/api/v1/rpc", method="POST",
        payload={"command": command, "args": dict(args), "background": True},
    )

"""More terminal clients of a running daemon: who am I, the API contract, publish an event.

Each one has a web-console or HTTP twin; these make the terminal complete. They find the
daemon like 'kdx status' does (the local discovery file, or '--target URL --token T') and share
the job commands' connection helpers.
"""

from __future__ import annotations

import sys
from typing import Any

import yaml

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..jsonio import loads
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.ipc import SCHEMA_PATH, WHOAMI_PATH
from .jobs import (
    JSON_ARG,
    TARGET_ARGS,
    _call,
    _failed,
    connect,
    events_command,
    jobs_command,
    stop_command,
)
from .render import dump_json, print_key_values, print_table, tr, write_line

INBOUND_PATH = "/api/v1/webhooks/inbound"
LOGS_PATH = "/api/v1/ui/logs"
CONFIG_PATH = "/api/v1/ui/config"
CONFIG_EDIT_PATH = "/api/v1/ui/config/edit"
AUDIT_PATH = "/api/v1/ui/audit"


def _fetch(request: CliRequest, route: str) -> Any | None:
    """Return the decoded JSON of GET *route* on the addressed daemon; None after saying why."""
    discovery = connect(request)
    reply = None if discovery is None else _call(discovery, route)
    if reply is None:
        return None
    code, payload = reply
    if code != 200:
        _failed(code, payload)
        return None
    return payload


def _whoami(request: CliRequest) -> int:
    payload = _fetch(request, WHOAMI_PATH)
    if payload is None:
        return int(ExitCode.ERROR)
    if request.flag("as_json"):
        dump_json(payload)
        return int(ExitCode.SUCCESS)
    print_key_values([
        ("name", str(payload.get("name") or "-")),
        ("role", str(payload.get("role") or "-")),
        ("source", str(payload.get("source") or "-")),
        ("can", ", ".join(str(item) for item in payload.get("can") or []) or "-"),
    ])
    return int(ExitCode.SUCCESS)


def _schema(request: CliRequest) -> int:
    payload = _fetch(request, SCHEMA_PATH)
    if payload is None:
        return int(ExitCode.ERROR)
    if request.flag("as_json"):
        dump_json(payload)
        return int(ExitCode.SUCCESS)
    routes = [*payload.get("routes", []), *payload.get("module_routes", [])]
    print_table(
        [
            [item.get("method", "GET"), item.get("path", ""), item.get("role") or "-",
             item.get("module") or "core", item.get("summary", "")]
            for item in routes
        ],
        headers=("method", "route", "role", "module", "summary"),
    )
    return int(ExitCode.SUCCESS)


def _publish(request: CliRequest) -> int:
    name = str(request.option("name") or "").strip()
    raw = request.option("payload")
    try:
        body = loads(str(raw)) if raw else {}
    except ValueError as exc:
        write_line(f"{TAG_ERROR} " + tr("--payload is not valid JSON: {exc}", exc=exc))
        return int(ExitCode.USAGE_ERROR)
    discovery = connect(request)
    reply = None if discovery is None else _call(
        discovery, f"{INBOUND_PATH}?event={name}", method="POST", payload=body
    )
    if reply is None:
        return int(ExitCode.ERROR)
    code, answer = reply
    if code >= 400:
        return _failed(code, answer)
    if request.flag("as_json"):
        dump_json(answer)
    else:
        write_line(f"{TAG_OK} " + tr("published {event}", event=answer.get("event", name)))
    return int(ExitCode.SUCCESS)


def _daemon_logs(request: CliRequest) -> int:
    lines = int(request.option("lines") or 50)
    payload = _fetch(request, f"{LOGS_PATH}?lines={max(1, lines)}")
    if payload is None:
        return int(ExitCode.ERROR)
    if request.flag("as_json"):
        dump_json(payload)
        return int(ExitCode.SUCCESS)
    if payload.get("note"):
        write_line(f"{TAG_NOTE} {payload['note']}", stream=sys.stderr)
    for line in payload.get("lines", []):
        write_line(str(line), raw=True)
    return int(ExitCode.SUCCESS)


def _daemon_config_show(request: CliRequest) -> int:
    payload = _fetch(request, CONFIG_PATH)
    if payload is None:
        return int(ExitCode.ERROR)
    prefix = str(request.option("key") or "").strip()
    entries = [e for e in payload.get("entries", []) if str(e.get("key", "")).startswith(prefix)]
    if request.flag("as_json"):
        dump_json({e["key"]: {"value": e["value"], "source": e["source"]} for e in entries})
        return int(ExitCode.SUCCESS)
    print_table(
        [[e["key"], _shown(e.get("value")), e.get("source", "")] for e in entries],
        headers=("key", "value", "source"),
    )
    return int(ExitCode.SUCCESS)


def _shown(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "(empty)"
    return "(unset)" if value is None else str(value)


def _edit(request: CliRequest, body: dict[str, Any], key: str) -> int:
    """POST one configuration edit to the daemon and report it."""
    discovery = connect(request)
    reply = None
    if discovery is not None:
        reply = _call(discovery, CONFIG_EDIT_PATH, method="POST", payload=body)
    if reply is None:
        return int(ExitCode.ERROR)
    code, answer = reply
    if code >= 400:
        return _failed(code, answer)
    if request.flag("as_json"):
        dump_json(answer)
    else:
        write_line(f"{TAG_OK} " + tr("{key} changed on the daemon", key=key))
    return int(ExitCode.SUCCESS)


def _daemon_config_set(request: CliRequest) -> int:
    key = str(request.option("key") or "").strip()
    value = request.option("value")
    if value is None:
        write_line(f"{TAG_ERROR} " + tr("give a value: kdx daemon config set KEY VALUE"))
        return int(ExitCode.USAGE_ERROR)
    return _edit(request, {"set": {key: yaml.safe_load(str(value))}}, key)


def _daemon_config_unset(request: CliRequest) -> int:
    key = str(request.option("key") or "").strip()
    return _edit(request, {"unset": [key]}, key)


def _daemon_audit(request: CliRequest) -> int:
    payload = _fetch(request, f"{AUDIT_PATH}?lines={max(1, int(request.option('lines') or 50))}")
    if payload is None:
        return int(ExitCode.ERROR)
    entries = payload.get("entries", [])
    if request.flag("as_json"):
        dump_json(payload)
        return int(ExitCode.SUCCESS)
    print_table(
        [[e.get("at", ""), e.get("who") or e.get("user") or "-", e.get("action", ""),
          e.get("outcome", ""), e.get("command") or ""] for e in entries],
        headers=("at", "who", "action", "outcome", "command"),
    )
    return int(ExitCode.SUCCESS)


def whoami_command() -> CliCommand:
    """Return 'kdx whoami': who the token belongs to and what it may do."""
    return CliCommand(
        name="whoami", help="Show who a daemon sees you as, and what that role may do.",
        handler=_whoami, group="core", args=(JSON_ARG, *TARGET_ARGS),
    )


def api_command() -> CliCommand:
    """Return 'kdx api': the contract of a daemon's control plane."""
    return CliCommand(
        name="api", help="Read a daemon's HTTP API contract.", group="core",
        subcommands=(
            CliCommand(
                name="schema", help="List every route the daemon serves, with its role.",
                handler=_schema, group="core", args=(JSON_ARG, *TARGET_ARGS),
            ),
        ),
    )


def publish_command() -> CliCommand:
    """Return 'kdx publish': drop an event on a daemon's bus as 'inbound.NAME'."""
    return CliCommand(
        name="publish", help="Publish an 'inbound.NAME' event on the daemon's bus.",
        handler=_publish, group="core",
        args=(
            CliArgument(("name",), metavar="NAME", help="Event name, for example order.paid."),
            CliArgument(("--payload",), dest="payload", metavar="JSON",
                        help="The event's payload as a JSON value [{}]."),
            JSON_ARG,
            *TARGET_ARGS,
        ),
    )


def daemon_command() -> CliCommand:
    """Return 'kdx daemon': logs, configuration and audit of a daemon, local or --target."""
    lines = CliArgument(("-n", "--lines"), dest="lines", type="int", help="How many [50].")
    key = CliArgument(("key",), metavar="KEY", help="Configuration key, for example core.locale.")
    return CliCommand(
        name="daemon", help="Read a running daemon's logs, configuration and audit log.",
        group="core",
        subcommands=(
            CliCommand(name="logs", help="Print the newest log lines of the daemon.",
                       handler=_daemon_logs, group="core", args=(lines, JSON_ARG, *TARGET_ARGS)),
            CliCommand(name="audit", help="Print the daemon's audit records (admin).",
                       handler=_daemon_audit, group="core", args=(lines, JSON_ARG, *TARGET_ARGS)),
            CliCommand(
                name="config", help="Read or change the daemon's configuration.", group="core",
                subcommands=(
                    CliCommand(
                        name="show", help="Print the daemon's resolved configuration.",
                        handler=_daemon_config_show, group="core",
                        args=(CliArgument(("key",), nargs="?", default="", metavar="PREFIX",
                                          help="Only keys starting with this."),
                              JSON_ARG, *TARGET_ARGS),
                    ),
                    CliCommand(
                        name="set", help="Change one setting on the daemon (admin).",
                        handler=_daemon_config_set, group="core",
                        args=(key, CliArgument(("value",), metavar="VALUE", help="The new value."),
                              JSON_ARG, *TARGET_ARGS),
                    ),
                    CliCommand(
                        name="unset", help="Remove one setting from the daemon's local layer.",
                        handler=_daemon_config_unset, group="core",
                        args=(key, JSON_ARG, *TARGET_ARGS),
                    ),
                ),
            ),
        ),
    )


def remote_commands() -> list[CliCommand]:
    """Return every terminal client of a running daemon, in the order 'kdx --help' lists."""
    return [
        jobs_command(), events_command(), stop_command(),
        whoami_command(), api_command(), publish_command(), daemon_command(),
    ]

"""Web console: the daemon serves a browser GUI that drives the same core the CLI does.

The GUI is a thin client like every other: it reads '/api/v1/status', runs module
commands through '/api/v1/rpc', and asks for reload or shutdown on the core's own
routes. This module only ships the page and the read-only helpers the page needs - the
command catalog (built from the declarative CLI contract, so a new module's commands
appear as forms with no GUI code), the log tail, the configuration, and a live feed of
bus events - plus 'kdx ui' to open it.

The page's files carry no secret and are served without a token; every call that
reads state or acts presents the shared token, which 'kdx ui' hands the browser in the
URL fragment (a fragment never reaches the server, a proxy, or a log line).
"""

from __future__ import annotations

import contextlib
import json
import queue
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ...cli.render import print_key_values, write_line
from ...config.layered import is_secret_key, redact_value
from ...config.schema import ConfigField, ConfigKind, ConfigSchema
from ...constants import TAG_ERROR, TAG_NOTE, TAG_OK
from ...events.bus import Event
from ...kernel.audit import read_audit
from ...kernel.cli_contract import CliArgument, CliCommand, CliRequest, CliSurface
from ...kernel.context import CoreContext
from ...kernel.contract import Module
from ...kernel.i18n import I18n, normalize_locale, parse_po
from ...kernel.ipc import (
    DISCOVERY_NAME,
    IpcDiscovery,
    IpcRequest,
    IpcRoute,
    IpcStaticMount,
    IpcStream,
)
from ...kernel.users import current_caller, role_rank
from ...logging.engine import tail_lines

UI_PREFIX = "/ui"
CATALOG_PATH = "/api/v1/ui/catalog"
LOGS_PATH = "/api/v1/ui/logs"
CONFIG_PATH = "/api/v1/ui/config"
EVENTS_PATH = "/api/v1/ui/events"
AUDIT_PATH = "/api/v1/ui/audit"
I18N_PATH = "/api/v1/ui/i18n"
WEB_DOMAIN = "korodexios-web"
WEB_LOCALES_DIR = Path(__file__).resolve().parent / "locales"
EVENT_QUEUE_SIZE = 500
KEEPALIVE_SECONDS = 15.0
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_LOG_LINES = 200
MAX_LOG_LINES = 5000
READY_WAIT_SECONDS = 30.0
READY_POLL_SECONDS = 0.1

BrowserOpener = Callable[[str], Any]


class WebConsoleModule(Module):
    """Publishes the browser console on the control plane and the 'kdx ui' launcher."""

    name = "web_console"
    description = "Browser GUI on the control plane: status, module commands, logs, config."
    version = "0.1.0"

    def __init__(self) -> None:
        super().__init__()
        self.opener: BrowserOpener | None = None
        self._closing = threading.Event()
        self._feeds: set[queue.Queue[Event | None]] = set()
        self._feeds_lock = threading.Lock()

    def init(self, context: CoreContext) -> None:
        """Register the page's own message domain with the language service."""
        super().init(context)
        context.i18n.register(WEB_DOMAIN, WEB_LOCALES_DIR)

    def start(self) -> None:
        """Open the event feeds for business."""
        self._closing.clear()
        super().start()

    def stop(self) -> None:
        """End every open event feed, so no browser holds a server thread past shutdown."""
        self._closing.set()
        with self._feeds_lock:
            feeds = list(self._feeds)
        for feed in feeds:
            with contextlib.suppress(queue.Full):
                feed.put_nowait(None)
        super().stop()

    def config_schema(self) -> ConfigSchema:
        """Declare the one scoped key this module reads."""
        return ConfigSchema(
            module=self.name,
            fields=(
                ConfigField(
                    "open_browser",
                    kind=ConfigKind.BOOL,
                    default=True,
                    description="Whether 'kdx ui' opens a browser or only prints the URL.",
                ),
            ),
        )

    def static_dirs(self) -> tuple[IpcStaticMount, ...]:
        """Serve the page's files; they hold no secret, so they need no token."""
        return (IpcStaticMount(route_prefix=UI_PREFIX, directory=STATIC_DIR, auth=False),)

    def ipc_routes(self) -> tuple[IpcRoute, ...]:
        """Answer the page's reads: the command catalog, the log tail, the config."""
        return (
            IpcRoute(
                path=CATALOG_PATH,
                handler=self._catalog_route,
                module=self.name,
                summary="every module command with its declared arguments",
            ),
            IpcRoute(
                path=LOGS_PATH,
                handler=self._logs_route,
                module=self.name,
                summary="the last lines of the chassis log file",
            ),
            IpcRoute(
                path=CONFIG_PATH,
                handler=self._config_route,
                module=self.name,
                summary="resolved configuration with provenance, secrets masked",
            ),
            IpcRoute(
                path=I18N_PATH,
                handler=self._i18n_route,
                module=self.name,
                summary="the page's messages in a language ('?locale=de'; default core.locale)",
            ),
            IpcRoute(
                path=AUDIT_PATH,
                handler=self._audit_route,
                module=self.name,
                summary="the newest audit records; admin only",
            ),
            IpcRoute(
                path=EVENTS_PATH,
                handler=self._events_route,
                module=self.name,
                summary="live bus events as server-sent events",
                content_type="text/event-stream",
            ),
        )

    def register_cli(self, surface: CliSurface) -> None:
        """Contribute 'kdx ui' to the root surface."""
        surface.add_command(
            CliCommand(
                name="ui",
                help="Open the browser console (starts the daemon when none is running).",
                module_name=self.name,
                handler=self._ui_command,
                show_in_wizard=False,
                args=(
                    CliArgument(
                        ("--no-browser",),
                        dest="no_browser",
                        action="store_true",
                        help="Print the console URL instead of opening a browser.",
                    ),
                ),
            )
        )

    def catalog(self) -> dict[str, Any]:
        """Return every runnable module command with the arguments it declares."""
        registry = self.context.registry if self.context is not None else None
        commands: list[dict[str, Any]] = []

        def visit(
            command: CliCommand, path: str, remote: bool, danger: bool, role: str, top: str
        ) -> None:
            if not command.show_in_wizard:
                return
            remote = remote and command.remote
            danger = danger or command.danger
            own = command.role or ("admin" if command.danger else "operator")
            role = max(role, own, key=role_rank)
            if command.handler is not None:
                commands.append(
                    {
                        "path": path,
                        "help": command.help,
                        "module": command.module_name or top,
                        "remote": remote,
                        "danger": danger,
                        "role": role,
                        "args": [describe_argument(argument) for argument in command.args],
                    }
                )
            for child in command.subcommands:
                visit(child, f"{path} {child.name}", remote, danger, role, top)

        for top in registry.commands() if registry is not None else []:
            visit(top, top.name, True, False, "operator", top.module_name or top.name)
        return {"commands": commands}

    def _catalog_route(self, request: IpcRequest) -> dict[str, Any]:
        return self.catalog()

    def i18n_payload(self, locale: str | None = None) -> dict[str, Any]:
        """Return the page's messages for *locale* (default: the chassis language)."""
        service = self.require_context().i18n
        chosen = normalize_locale(locale) if locale else service.locale
        view = I18n(chosen or service.locale)
        view.register(WEB_DOMAIN, WEB_LOCALES_DIR)
        template = WEB_LOCALES_DIR / f"{WEB_DOMAIN}.pot"
        sources = [e.msgid for e in parse_po(template.read_text(encoding="utf-8")) if e.msgid]
        return {
            "locale": view.locale,
            "locales": list(view.locales(WEB_DOMAIN)),
            "messages": {m: view.translate(m, domain=WEB_DOMAIN) for m in sources},
        }

    def _i18n_route(self, request: IpcRequest) -> dict[str, Any]:
        requested = request.query.get("locale", [""])[0]
        return self.i18n_payload(requested or None)

    def _logs_route(self, request: IpcRequest) -> dict[str, Any]:
        """Return the newest log lines; '?lines=N' picks how many."""
        try:
            count = int(request.query.get("lines", [DEFAULT_LOG_LINES])[0])
        except (TypeError, ValueError):
            count = DEFAULT_LOG_LINES
        count = max(1, min(MAX_LOG_LINES, count))
        log_file = self.context.logs.log_file if self.context is not None else None
        if log_file is None:
            return {"file": None, "lines": [], "note": "file logging is disabled"}
        return {"file": str(log_file), "lines": tail_lines(Path(log_file), count)}

    def _config_route(self, request: IpcRequest) -> dict[str, Any]:
        """Return every resolved key with its layer; a secret is never sent in clear."""
        config = self.context.config if self.context is not None else None
        entries = config.entries() if config is not None else []
        return {
            "entries": [
                {
                    "key": entry.key,
                    "value": redact_value(entry.value) if is_secret_key(entry.key) else entry.value,
                    "source": entry.source.label(),
                    "secret": is_secret_key(entry.key),
                }
                for entry in entries
            ]
        }

    def _audit_route(self, request: IpcRequest) -> Any:
        """Return the newest audit records to an admin; who did what is not for everyone."""
        caller = current_caller()
        if caller is not None and not caller.allows("admin"):
            return 403, {"error": f"the audit log needs the admin role; {caller.name} is "
                         f"{caller.role}"}
        try:
            limit = int(request.query.get("lines", [DEFAULT_LOG_LINES])[0])
        except (TypeError, ValueError):
            limit = DEFAULT_LOG_LINES
        chassis = self.context.chassis if self.context is not None else None
        audit = getattr(chassis, "audit", None)
        if audit is None:
            return {"entries": []}
        return {"entries": read_audit(audit.path, limit=max(1, min(MAX_LOG_LINES, limit)))}

    def _events_route(self, request: IpcRequest) -> IpcStream:
        """Stream every bus event to one browser until it leaves or the chassis stops."""
        return IpcStream(self.event_feed())

    def event_feed(self, keepalive: float = KEEPALIVE_SECONDS) -> Iterator[bytes]:
        """Yield server-sent event frames: one per bus event, a comment while idle.

        The bus calls the subscriber on the publishing thread, so it only drops the
        event into this feed's bounded queue; a browser that reads too slowly loses
        events instead of slowing the module that published them.
        """
        feed: queue.Queue[Event | None] = queue.Queue(maxsize=EVENT_QUEUE_SIZE)

        def deliver(event: Event) -> None:
            with contextlib.suppress(queue.Full):
                feed.put_nowait(event)

        subscription = self.bus.subscribe("*", deliver, name="web_console.feed")
        with self._feeds_lock:
            self._feeds.add(feed)
        try:
            yield b"retry: 3000\n: connected\n\n"
            while not self._closing.is_set():
                try:
                    event = feed.get(timeout=keepalive)
                except queue.Empty:
                    yield b": keepalive\n\n"
                    continue
                if event is None:
                    return
                frame = {
                    "name": event.name,
                    "source": event.source,
                    "at": round(event.at, 3),
                    "sequence": event.sequence,
                    "payload": event.payload,
                }
                yield f"data: {json.dumps(frame, default=str)}\n\n".encode()
        finally:
            subscription.unsubscribe(self.bus)
            with self._feeds_lock:
                self._feeds.discard(feed)

    def _ui_command(self, request: CliRequest) -> int:
        """Open the console of the live daemon, or boot one here and open it once ready."""
        context = request.context
        open_browser = self.config.get_bool("open_browser", True) and not request.flag(
            "no_browser"
        )
        discovery = IpcDiscovery.load(context.paths.state_dir / DISCOVERY_NAME)
        if discovery is not None and discovery.is_process_alive():
            self._announce(console_url(discovery), open_browser)
            return 0
        chassis = request.options.get("chassis") or context.chassis
        if chassis is None:
            write_line(f"{TAG_ERROR} no daemon is running and this command has no chassis")
            write_line(f"{TAG_NOTE} start one with 'kdx run', then run 'kdx ui' again")
            return 1
        if not context.config.get_bool("core.ipc.enabled", True):
            write_line(f"{TAG_ERROR} the control plane is disabled (core.ipc.enabled: false)")
            return 1
        watcher = threading.Thread(
            target=self._announce_when_ready,
            args=(chassis, open_browser),
            name="kdx-ui-announce",
            daemon=True,
        )
        watcher.start()
        chassis.ensure_initialized()
        chassis.start(ipc=True)
        try:
            chassis.run_forever()
        except KeyboardInterrupt:  # pragma: no cover - handled by the signal path
            chassis.signals.request_shutdown()
        return int(chassis.shutdown())

    def _announce_when_ready(self, chassis: Any, open_browser: bool) -> None:
        """Wait for the control plane this process is starting, then announce it."""
        deadline = time.monotonic() + READY_WAIT_SECONDS
        while time.monotonic() < deadline:
            server = getattr(chassis, "ipc", None)
            discovery = getattr(server, "discovery", None) if server is not None else None
            if discovery is not None and getattr(server, "running", False):
                self._announce(console_url(discovery), open_browser)
                return
            if getattr(getattr(chassis, "state", None), "value", "") == "stopped":
                return
            time.sleep(READY_POLL_SECONDS)
        self.logger.warning("web console never saw the control plane come up")

    def _announce(self, url: str, open_browser: bool) -> None:
        """Print where the console lives and, unless told not to, open it."""
        public = url.split("#", 1)[0]
        print_key_values([("console", public)])
        opener = self.opener or webbrowser.open
        if open_browser and opener(url):
            write_line(f"{TAG_OK} opened the web console in your browser")
        else:
            write_line(f"{TAG_NOTE} open this URL; it carries the session token:")
            write_line(url)
        sys.stdout.flush()


def console_url(discovery: IpcDiscovery) -> str:
    """Return the console URL with the token in its fragment, never in its query."""
    base = f"{discovery.base_url()}{UI_PREFIX}/"
    return f"{base}#token={discovery.token}" if discovery.token else base


def describe_argument(argument: CliArgument) -> dict[str, Any]:
    """Describe one declared argument the way the page renders a form field."""
    flag_like = argument.type == "flag" or argument.action in ("store_true", "store_false")
    default = argument.default
    if default is not None and not isinstance(default, str | int | float | bool | list):
        default = str(default)
    return {
        "dest": argument.destination(),
        "flags": list(argument.flags),
        "positional": argument.is_positional,
        "kind": "flag" if flag_like else argument.type,
        "action": argument.action,
        "nargs": argument.nargs,
        "choices": list(argument.choices),
        "default": default,
        "required": argument.required,
        "help": argument.help,
        "metavar": argument.metavar,
    }


MODULE = WebConsoleModule()

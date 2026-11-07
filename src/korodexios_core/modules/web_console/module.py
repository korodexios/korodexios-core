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

from ...cli.render import (
    dump_json,
    print_key_values,
    print_section,
    print_table,
    tr,
    write_line,
)
from ...cli.ui_test import run_gui_tests
from ...config.layered import is_secret_key, redact_for_display
from ...config.schema import ConfigField, ConfigKind, ConfigSchema
from ...constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
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
from ...kernel.ui_manifest import build_manifest
from ...kernel.ui_prefs import PrefsError, PrefsStore
from ...kernel.ui_tiers import guide, probe_environment
from ...kernel.users import current_caller, role_rank
from ...logging.engine import tail_lines

UI_PREFIX = "/ui"
CATALOG_PATH = "/api/v1/ui/catalog"
LOGS_PATH = "/api/v1/ui/logs"
CONFIG_PATH = "/api/v1/ui/config"
EVENTS_PATH = "/api/v1/ui/events"
AUDIT_PATH = "/api/v1/ui/audit"
I18N_PATH = "/api/v1/ui/i18n"
MANIFEST_PATH = "/api/v1/ui/manifest"
PREFS_PATH = "/api/v1/ui/prefs"
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

_PREFS_ARGS = (
    CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON."),
    CliArgument(
        ("--user",),
        dest="user",
        metavar="NAME",
        help="Whose preferences (default: the signed-in user, else 'default').",
    ),
)


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
                path=MANIFEST_PATH,
                handler=self._manifest_route,
                module=self.name,
                role="viewer",
                summary="the GUI every loaded module declares ('?locale=de')",
            ),
            IpcRoute(
                path=PREFS_PATH,
                handler=self._prefs_route,
                methods=("GET", "PUT", "PATCH", "DELETE"),
                module=self.name,
                role="viewer",
                summary="the caller's own GUI preferences: read, replace, merge, reset",
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
                subcommands=(
                    CliCommand(
                        name="manifest",
                        help="Print the GUI every loaded module declares (what the browser gets).",
                        module_name=self.name,
                        handler=self._manifest_command,
                        show_in_wizard=False,
                        args=(
                            CliArgument(("--json",), dest="as_json", action="store_true",
                                        help="Emit JSON."),
                        ),
                    ),
                    CliCommand(
                        name="doctor",
                        help="Report what this machine can run for a GUI: Node, Bun, "
                        "browsers, a display, headless testing.",
                        module_name=self.name,
                        handler=self._doctor_command,
                        show_in_wizard=False,
                        args=(
                            CliArgument(("--json",), dest="as_json", action="store_true",
                                        help="Emit JSON."),
                        ),
                    ),
                    CliCommand(
                        name="test",
                        help="Run the GUI tests this machine can run: manifest, then the web app's "
                        "type check, component tests and headless e2e (--headed adds a window).",
                        module_name=self.name,
                        handler=self._test_command,
                        show_in_wizard=False,
                        remote=False,
                        args=(
                            CliArgument(("--headed",), dest="headed", action="store_true",
                                        help="Also run the e2e tests with a visible browser "
                                        "window (needs a display)."),
                            CliArgument(("--install",), dest="install", action="store_true",
                                        help="Run the package manager's install if node_modules "
                                        "is missing."),
                            CliArgument(("--pm",), dest="pm", metavar="MANAGER",
                                        help="npm | bun | pnpm [from the lockfile, else npm]."),
                            CliArgument(("--path",), dest="path", metavar="DIR",
                                        help="Project root [the chassis root]."),
                            CliArgument(("--json",), dest="as_json", action="store_true",
                                        help="Emit JSON."),
                        ),
                    ),
                    CliCommand(
                        name="guide",
                        help="Explain the GUI tiers a project can choose and when to pick each.",
                        module_name=self.name,
                        handler=self._guide_command,
                        show_in_wizard=False,
                        args=(
                            CliArgument(("--json",), dest="as_json", action="store_true",
                                        help="Emit JSON."),
                        ),
                    ),
                    CliCommand(
                        name="prefs",
                        help="Show, change or reset the remembered GUI choices.",
                        module_name=self.name,
                        show_in_wizard=False,
                        subcommands=(
                            CliCommand(
                                name="show",
                                help="Show the stored GUI preferences.",
                                module_name=self.name,
                                handler=self._prefs_show,
                                show_in_wizard=False,
                                args=_PREFS_ARGS,
                            ),
                            CliCommand(
                                name="patch",
                                help="Merge a JSON object into the preferences "
                                "(a null value deletes that key).",
                                module_name=self.name,
                                handler=self._prefs_patch,
                                show_in_wizard=False,
                                args=(
                                    CliArgument(("changes",), metavar="JSON",
                                                help="e.g. '{\"defaults\": {\"job_queue.submit\":"
                                                "{\"kind\": \"demo\"}}}'."),
                                    *_PREFS_ARGS,
                                ),
                            ),
                            CliCommand(
                                name="reset",
                                help="Forget the remembered choices (defaults stay unless "
                                "named).",
                                module_name=self.name,
                                handler=self._prefs_reset,
                                show_in_wizard=False,
                                args=(
                                    CliArgument(("sections",), metavar="SECTION", nargs="*",
                                                help="nav, panels, buttons, values, defaults, "
                                                "settings; none means all but defaults."),
                                    *_PREFS_ARGS,
                                ),
                            ),
                        ),
                    ),
                ),
            )
        )

    def _manifest_command(self, request: CliRequest) -> int:
        manifest = self.manifest()  # the language is the chassis' own: kdx --locale de ...
        if request.flag("as_json"):
            dump_json(manifest)
            return int(ExitCode.SUCCESS)
        for entry in manifest["modules"]:
            print_section(f"{entry['title']} ({entry['module']})")
            print_table(
                [[p["id"], p["type"], p["title"], p.get("source") or p.get("command") or ""]
                 for p in entry["panels"]],
                headers=("panel", "type", "title", "source / command"),
            )
        for issue in manifest["issues"]:
            where = issue["module"] + (f".{issue['panel']}" if issue.get("panel") else "")
            write_line(f"{TAG_ERROR} {where}: {issue['message']}")
        if not manifest["modules"]:
            write_line(f"{TAG_NOTE} no loaded module declares a [ui] table")
        return int(ExitCode.SUCCESS)

    def _doctor_command(self, request: CliRequest) -> int:
        report = probe_environment()
        if request.flag("as_json"):
            dump_json(report)
            return int(ExitCode.SUCCESS)
        tools = report["tools"]
        print_key_values(
            [
                (name, tools[name]["version"] or ("found" if tools[name]["found"] else "missing"))
                for name in tools
            ]
            + [
                ("browsers", ", ".join(b["name"] for b in report["browsers"]) or "none found"),
                ("playwright browsers", "yes" if report["playwright_browsers"] else "no"),
                ("display", f"{'yes' if report['display']['available'] else 'no'} "
                 f"({report['display']['reason']})"),
            ]
        )
        testing = report["testing"]
        write_line(
            f"{TAG_OK if testing['headless_browser'] else TAG_NOTE} "
            + tr("headless browser tests: {state}",
                 state="yes" if testing["headless_browser"] else "no (API and HTML tests only)")
        )
        write_line(
            f"{TAG_OK if testing['headed_browser'] else TAG_NOTE} "
            + tr("headed browser tests: {state}",
                 state="yes" if testing["headed_browser"] else "no")
        )
        for setting, value in testing["recommended"].items():
            write_line(
                f"{TAG_NOTE} "
                + tr("for browser tests: {setting}={value}", setting=setting, value=value)
            )
        for name, tier in report["tiers"].items():
            write_line(
                f"{TAG_OK if tier['ready'] else TAG_ERROR} "
                + tr("tier {name}: {state}", name=name,
                     state="ready" if tier["ready"] else "missing " + ", ".join(tier["missing"]))
            )
        return int(ExitCode.SUCCESS)

    def _test_command(self, request: CliRequest) -> int:
        path_text = str(request.option("path") or "").strip()
        root = Path(path_text).expanduser() if path_text else Path(request.context.paths.root)
        report = run_gui_tests(
            root,
            manifest=self.manifest(),
            environment=probe_environment(),
            headed=request.flag("headed"),
            install=request.flag("install"),
            pm=str(request.option("pm") or "").strip().lower(),
        )
        if request.flag("as_json"):
            dump_json(report.as_dict())
        else:
            for step in report.steps:
                tag = {"passed": TAG_OK, "failed": TAG_ERROR}.get(step.status, TAG_NOTE)
                took = f" ({step.seconds:.1f}s)" if step.seconds else ""
                write_line(f"{tag} {step.name}: {step.status}{took} {step.detail}".rstrip())
                if step.output:
                    for line in step.output.splitlines():
                        write_line(f"    {line}")
        return int(ExitCode.SUCCESS if report.ok else ExitCode.ERROR)

    def _guide_command(self, request: CliRequest) -> int:
        data = guide()
        if request.flag("as_json"):
            dump_json(data)
            return int(ExitCode.SUCCESS)
        for name, tier in data["tiers"].items():
            print_section(f"{name}: {tier['summary']}")
            for line in tier["choose_when"]:
                write_line(f"  + {line}")
            for line in tier["avoid_when"]:
                write_line(f"  - {line}")
            write_line(f"  create: {tier['init']}")
        print_section("how to decide")
        for line in data["decide"]:
            write_line(f"  {line}")
        print_section("rules")
        for line in data["rules"]:
            write_line(f"  {line}")
        print_section("known limits")
        for note in data["notes"]:
            write_line(f"  [{note['applies_to']}] {note['text']}")
        return int(ExitCode.SUCCESS)

    def _prefs_user(self, request: CliRequest) -> str | None:
        named = request.option("user")
        if named:
            return str(named)
        return request.caller.name if request.caller is not None else None

    def _prefs_emit(self, request: CliRequest, prefs: dict[str, Any]) -> int:
        if request.flag("as_json"):
            dump_json(prefs)
            return int(ExitCode.SUCCESS)
        for section in ("nav", "panels", "buttons", "values", "defaults", "settings"):
            value = prefs[section]
            write_line(f"{section}: " + (json.dumps(value, ensure_ascii=False) if value else "-"))
        return int(ExitCode.SUCCESS)

    def _prefs_show(self, request: CliRequest) -> int:
        return self._prefs_emit(request, self.prefs.load(self._prefs_user(request)))

    def _prefs_patch(self, request: CliRequest) -> int:
        try:
            changes = json.loads(str(request.option("changes") or ""))
            prefs = self.prefs.patch(self._prefs_user(request), changes)
        except (ValueError, PrefsError) as exc:
            write_line(f"{TAG_ERROR} {exc}")
            return int(ExitCode.USAGE_ERROR)
        return self._prefs_emit(request, prefs)

    def _prefs_reset(self, request: CliRequest) -> int:
        sections = tuple(str(s) for s in (request.option("sections") or ()))
        try:
            prefs = self.prefs.reset(self._prefs_user(request), sections)
        except PrefsError as exc:
            write_line(f"{TAG_ERROR} {exc}")
            return int(ExitCode.USAGE_ERROR)
        return self._prefs_emit(request, prefs)

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

    # -- manifest and preferences ----------------------------------------------------

    def manifest(self, locale: str | None = None) -> dict[str, Any]:
        """Return the GUI every active module declares, validated and translated."""
        context = self.require_context()
        service = context.i18n
        chosen = normalize_locale(locale) if locale else service.locale
        view = I18n(chosen or service.locale)
        for domain, directory in service.domains().items():
            view.register(domain, directory)
        declarations: list[tuple[str, Any]] = []
        failures: list[dict[str, str]] = []
        registry = context.registry
        for record in registry.active() if registry is not None else []:
            declared = record.spec.metadata.get("ui")
            if declared is None:
                try:
                    declared = record.module.ui_spec()
                except Exception as exc:  # a module's bug must not take the GUI down
                    failures.append(
                        {"module": record.spec.name, "message": f"ui_spec() raised: {exc}"}
                    )
                    continue
            declarations.append((record.spec.name, declared))
        known = {entry["path"] for entry in self.catalog()["commands"]}
        manifest = build_manifest(
            declarations,
            known_commands=known,
            translate=lambda message, domain: view.translate(message, domain=domain),
            locale=view.locale,
        )
        manifest["issues"].extend(failures)
        return manifest

    def _manifest_route(self, request: IpcRequest) -> dict[str, Any]:
        requested = request.query.get("locale", [""])[0]
        return self.manifest(requested or None)

    @property
    def prefs(self) -> PrefsStore:
        """Return the preference store under ``data/ui/``."""
        return PrefsStore(self.require_context().paths.durable_dir / "ui")

    def _prefs_route(self, request: IpcRequest) -> Any:
        """Read, replace, merge or reset the caller's own preferences."""
        caller = request.caller or current_caller()
        user = caller.name if caller is not None else None
        store = self.prefs
        try:
            if request.method == "GET":
                return store.load(user)
            if request.method == "PUT":
                return store.replace(user, request.payload)
            if request.method == "PATCH":
                return store.patch(user, request.payload)
            sections = request.query.get("section", [])
            return store.reset(user, tuple(sections))
        except PrefsError as exc:
            return 400, {"error": str(exc)}

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
                    "value": redact_for_display(entry.key, entry.value),
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

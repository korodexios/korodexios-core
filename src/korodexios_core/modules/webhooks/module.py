"""Webhooks module: bus events out to automation tools, and their events in.

Outbound: every bus event whose name matches a target's pattern is POSTed as JSON to that
target - an n8n "Webhook" trigger, a Zapier or Make hook, Slack, Discord, ntfy, or any
HTTP endpoint. Deliveries leave from one background worker with a bounded queue and
retries, so a slow receiver never slows the module that published the event. With
``modules.webhooks.secret`` set, each delivery carries an HMAC-SHA256 signature over
``<timestamp>.<body>`` (``X-Kdx-Signature: sha256=...``), so the receiver can tell a real
delivery from a forged or replayed one.

Inbound: ``POST /api/v1/webhooks/inbound?event=NAME`` (role contributor) publishes
``inbound.NAME`` on the bus with the posted JSON as payload; a module subscribed to
``inbound.**`` reacts. Commands are not run this way - that is ``/api/v1/rpc`` - so a
contributor token handed to an automation can only drop events in.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...cli.render import dump_json, print_table, tr, write_line
from ...config.schema import ConfigField, ConfigKind, ConfigSchema
from ...constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ...events.bus import Event, Subscription, matches
from ...kernel.cli_contract import CliArgument, CliCommand, CliRequest, CliSurface
from ...kernel.contract import Module
from ...kernel.diagnostics import DiagnosticCheck, DiagnosticSeverity
from ...kernel.ipc import IpcRequest, IpcRoute, is_loopback_host
from ...version import __version__

INBOUND_PATH = "/api/v1/webhooks/inbound"
INBOUND_PREFIX = "inbound"
SIGNATURE_HEADER = "X-Kdx-Signature"
TIMESTAMP_HEADER = "X-Kdx-Timestamp"
EVENT_HEADER = "X-Kdx-Event"
DELIVERY_HEADER = "X-Kdx-Delivery"
DEFAULT_TIMEOUT = 10.0
DEFAULT_RETRIES = 3
DEFAULT_QUEUE = 1000
MAX_BACKOFF = 30.0
WORKER_NAME = "kdx-webhooks"
EVENT_NAME: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
OWN_EVENTS = ("webhooks", "webhooks.**")


@dataclass(frozen=True, slots=True)
class Target:
    """One outbound destination: which events, and where to."""

    pattern: str
    url: str

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.url).hostname or ""

    def masked(self) -> str:
        """Return the URL without its path or query, which often hold the secret."""
        parts = urllib.parse.urlsplit(self.url)
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.hostname or ''}{port}/..."


def parse_target(text: str) -> Target:
    """Read 'URL' or 'PATTERN URL' ('core.job.** https://n8n.example/webhook/x')."""
    parts = str(text).split()
    if not parts or len(parts) > 2:
        raise ValueError(f"a target is 'URL' or 'PATTERN URL', got {text!r}")
    pattern, url = ("*", parts[0]) if len(parts) == 1 else (parts[0], parts[1])
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in ("http", "https") or not urllib.parse.urlsplit(url).hostname:
        raise ValueError(f"target URL {url!r} must be http:// or https:// with a host")
    return Target(pattern=pattern.strip(".") or "*", url=url)


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """Return the signature header value for one delivery: 'sha256=<hex>'."""
    digest = hmac.new(
        str(secret).encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


def verify(secret: str, timestamp: str, body: bytes, signature: str, *,
           tolerance: float = 300.0, now: float | None = None) -> bool:
    """Return True when *signature* is valid for *body* and *timestamp* is fresh.

    The receiving side of a chassis-to-chassis hook uses this; the docs show the same
    check in JavaScript for an n8n Code node.
    """
    try:
        age = abs((time.time() if now is None else now) - float(timestamp))
        wanted = sign(secret, timestamp, body).encode("ascii")
    except ValueError:  # not a number, or not ASCII (UnicodeEncodeError is a ValueError)
        return False
    return age <= tolerance and hmac.compare_digest(wanted, str(signature).encode("utf-8"))


def event_body(event: Event, delivery: str) -> bytes:
    """Return the JSON a target receives for *event*."""
    return json.dumps(
        {
            "event": event.name,
            "source": event.source,
            "at": round(event.at, 3),
            "sequence": event.sequence,
            "delivery": delivery,
            "payload": event.payload,
        },
        default=str,
        sort_keys=True,
    ).encode("utf-8")


class WebhooksModule(Module):
    """Delivers matching bus events to HTTP targets and accepts inbound events."""

    name = "webhooks"
    description = "Signed outbound webhooks for bus events, and an inbound endpoint."
    version = "0.1.0"

    def __init__(self) -> None:
        super().__init__()
        self._queue: queue.Queue[tuple[Target, Event, str] | None] = queue.Queue(
            maxsize=DEFAULT_QUEUE
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._subscription: Subscription | None = None
        self._targets: tuple[Target, ...] = ()
        self._invalid: list[str] = []
        self._running = False
        self.delivered = 0
        self.failed = 0
        self.dropped = 0
        self.received = 0
        self.last_error = ""

    def config_schema(self) -> ConfigSchema:
        """Declare the keys this module reads; the targets are secret (URLs are keys)."""
        return ConfigSchema(
            module=self.name,
            fields=(
                ConfigField(
                    "targets",
                    kind=ConfigKind.LIST,
                    default=[],
                    secret=True,
                    description="Outbound targets: 'URL' or 'PATTERN URL' (pattern '*' = all).",
                ),
                ConfigField(
                    "secret",
                    kind=ConfigKind.STR,
                    default="",
                    description="HMAC-SHA256 key that signs every delivery (empty: unsigned).",
                ),
                ConfigField(
                    "timeout",
                    kind=ConfigKind.FLOAT,
                    default=DEFAULT_TIMEOUT,
                    description="Seconds one delivery attempt may take.",
                ),
                ConfigField(
                    "retries",
                    kind=ConfigKind.INT,
                    default=DEFAULT_RETRIES,
                    description="Attempts per delivery before it counts as failed.",
                ),
                ConfigField(
                    "inbound",
                    kind=ConfigKind.BOOL,
                    default=True,
                    description="Accept POST /api/v1/webhooks/inbound from contributors.",
                ),
            ),
        )

    # -- configuration ------------------------------------------------------------

    def targets(self) -> tuple[Target, ...]:
        """Return the valid targets the configuration names; invalid ones are reported."""
        found: list[Target] = []
        invalid: list[str] = []
        for item in self.config.get_list("targets", []) or []:
            try:
                found.append(parse_target(str(item)))
            except ValueError as exc:
                invalid.append(str(exc))
        self._invalid = invalid
        return tuple(found)

    def on_config_reload(self, config: Any) -> None:
        """Pick up changed targets without a restart (and start delivering if now needed)."""
        super().on_config_reload(config)
        self._targets = self.targets()
        if self._targets and self._running:
            self._ensure_worker()

    # -- lifecycle ----------------------------------------------------------------

    def start(self) -> None:
        """Start delivering when targets exist; with none the module costs no thread."""
        super().start()
        self._running = True
        self._targets = self.targets()
        if self._targets:
            self._ensure_worker()
        self.logger.info("webhooks ready", targets=len(self._targets))

    def _ensure_worker(self) -> None:
        """Subscribe and start the worker once, each start with its own queue and flag.

        The module object outlives a chassis (it is the module's singleton), so a fresh
        queue and stop flag per start keep a worker from an earlier run from taking this
        run's stop signal.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        work: queue.Queue[tuple[Target, Event, str] | None] = queue.Queue(maxsize=DEFAULT_QUEUE)
        stop = threading.Event()
        self._queue, self._stop = work, stop
        self._subscription = self.bus.subscribe("*", self._enqueue, name="webhooks.outbound")
        self._thread = threading.Thread(
            target=self._work, args=(work, stop), name=WORKER_NAME, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop taking events, let the worker finish the current delivery, then join."""
        self._running = False
        self._stop.set()
        if self._subscription is not None:
            self._subscription.unsubscribe(self.bus)
            self._subscription = None
        thread = self._thread
        self._thread = None
        if thread is not None:
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(None)
            thread.join(timeout=max(0.1, self.stop_timeout))
        super().stop()

    def workers(self) -> dict[str, threading.Thread]:
        """Report the delivery worker so supervision can watch it."""
        thread = self._thread
        return {WORKER_NAME: thread} if thread is not None and thread.is_alive() else {}

    def health(self) -> dict[str, Any]:
        """Report delivery counters beside the lifecycle block."""
        data = super().health()
        data.update(
            targets=len(self._targets),
            queued=self._queue.qsize(),
            delivered=self.delivered,
            failed=self.failed,
            dropped=self.dropped,
            received=self.received,
            last_error=self.last_error or None,
        )
        return data

    def diagnostics(self) -> tuple[DiagnosticCheck, ...]:
        """Report invalid targets and unencrypted or unsigned deliveries."""
        checks: list[DiagnosticCheck] = []
        targets = self.targets()
        for problem in self._invalid:
            checks.append(DiagnosticCheck(
                name="target", message=problem, severity=DiagnosticSeverity.ERROR,
                hint="write 'URL' or 'PATTERN URL'", module=self.name,
            ))
        for target in targets:
            if target.url.lower().startswith("http://") and not is_loopback_host(target.host):
                checks.append(DiagnosticCheck(
                    name="plain-http",
                    message=f"events to {target.masked()} travel unencrypted",
                    severity=DiagnosticSeverity.WARNING,
                    hint="use https:// for a target off this machine", module=self.name,
                ))
        if targets and not self.config.get_str("secret", ""):
            checks.append(DiagnosticCheck(
                name="unsigned",
                message="deliveries are not signed (modules.webhooks.secret is empty)",
                severity=DiagnosticSeverity.WARNING,
                hint="set a secret so receivers can verify X-Kdx-Signature", module=self.name,
            ))
        if not checks:
            checks.append(DiagnosticCheck(
                name="targets", message=f"{len(targets)} webhook target(s)", module=self.name,
            ))
        return tuple(checks)

    # -- delivery -----------------------------------------------------------------

    def _enqueue(self, event: Event) -> None:
        """Queue *event* for every target that wants it; never block the publisher."""
        if self._stop.is_set() or any(matches(own, event.name) for own in OWN_EVENTS):
            return
        for target in self._targets:
            if not matches(target.pattern, event.name):
                continue
            try:
                self._queue.put_nowait((target, event, uuid.uuid4().hex))
            except queue.Full:
                self.dropped += 1

    def _work(
        self, work: queue.Queue[tuple[Target, Event, str] | None], stop: threading.Event
    ) -> None:
        while not stop.is_set():
            item = work.get()
            if item is None:
                return
            target, event, delivery = item
            self.deliver(target, event, delivery, stop=stop)

    def deliver(
        self,
        target: Target,
        event: Event,
        delivery: str = "",
        *,
        stop: threading.Event | None = None,
    ) -> tuple[bool, int, str]:
        """POST *event* to *target* with retries; return (ok, last status, error text).

        *stop* cuts the backoff short at shutdown; a direct call ('kdx webhooks test')
        passes none and simply waits.
        """
        stop = stop if stop is not None else threading.Event()
        delivery = delivery or uuid.uuid4().hex
        body = event_body(event, delivery)
        attempts = max(1, self.config.get_int("retries", DEFAULT_RETRIES))
        timeout = max(0.5, self.config.get_float("timeout", DEFAULT_TIMEOUT))
        secret = self.config.get_str("secret", "")
        status, error = 0, ""
        for attempt in range(attempts):
            stamp = str(int(time.time()))
            headers = {
                "Content-Type": "application/json",
                "User-Agent": f"korodexios-webhooks/{__version__}",
                EVENT_HEADER: event.name,
                DELIVERY_HEADER: delivery,
                TIMESTAMP_HEADER: stamp,
            }
            if secret:
                headers[SIGNATURE_HEADER] = sign(secret, stamp, body)
            request = urllib.request.Request(target.url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    status = int(response.status)
            except urllib.error.HTTPError as exc:
                status, error = int(exc.code), f"HTTP {exc.code}"
            except (OSError, ValueError) as exc:
                status, error = 0, str(exc)
            if 200 <= status < 300:
                self.delivered += 1
                return (True, status, "")
            if 400 <= status < 500 and status not in (408, 429):
                break  # the receiver refused the request itself; retrying cannot help
            if attempt + 1 < attempts and stop.wait(min(MAX_BACKOFF, 2.0 ** attempt)):
                break
        self.failed += 1
        self.last_error = f"{target.masked()}: {error or status}"
        self.logger.warning("webhook delivery failed", target=target.masked(),
                            event=event.name, status=status, error=error)
        return (False, status, error or f"HTTP {status}")

    # -- control plane ------------------------------------------------------------

    def ipc_routes(self) -> tuple[IpcRoute, ...]:
        """Accept inbound events from contributors (an automation's drop-box token)."""
        if not self.config.get_bool("inbound", True):
            return ()
        return (
            IpcRoute(
                path=INBOUND_PATH,
                handler=self._inbound_route,
                methods=("POST",),
                module=self.name,
                role="contributor",
                summary="publish 'inbound.<event>' on the bus from an automation (n8n, ...)",
            ),
        )

    def _inbound_route(self, request: IpcRequest) -> Any:
        body = request.payload
        name = str(request.query.get("event", [""])[0] or "").strip().lower()
        payload: Any = body
        if not name and isinstance(body, Mapping) and "event" in body:
            name = str(body.get("event") or "").strip().lower()
            payload = body.get("payload")
        if not EVENT_NAME.match(name):
            return (400, {"error": "name the event: ?event=NAME or {\"event\": NAME, "
                                   "\"payload\": ...}; 1-64 characters a-z, 0-9, '.', '_', '-'"})
        full = f"{INBOUND_PREFIX}.{name}"
        caller = request.caller
        handlers = self.bus.publish(
            full, {"payload": payload, "by": caller.name if caller else None},
            source="webhooks",
        )
        self.received += 1
        return (202, {"accepted": True, "event": full, "handlers": handlers})

    # -- CLI ----------------------------------------------------------------------

    def register_cli(self, surface: CliSurface) -> None:
        """Contribute 'kdx webhooks list|test'."""
        surface.add_command(
            CliCommand(
                name="webhooks",
                help="Outbound webhooks for bus events (n8n, Zapier, Make, Slack, ...).",
                module_name=self.name,
                subcommands=(
                    CliCommand(
                        name="list",
                        help="List the configured targets (URLs masked).",
                        module_name=self.name,
                        handler=self._list_command,
                        args=(CliArgument(("--json",), dest="as_json", action="store_true",
                                          help="Emit JSON."),),
                    ),
                    CliCommand(
                        name="test",
                        help="Send one signed test event to every target and report the answer.",
                        module_name=self.name,
                        handler=self._test_command,
                        args=(
                            CliArgument(("--event",), default="webhooks.test",
                                        help="Event name to send [webhooks.test]."),
                            CliArgument(("--json",), dest="as_json", action="store_true",
                                        help="Emit JSON."),
                        ),
                    ),
                ),
            )
        )

    def _list_command(self, request: CliRequest) -> int:
        targets = self.targets()
        rows = [{"pattern": target.pattern, "url": target.masked()} for target in targets]
        verdict = int(ExitCode.SUCCESS if not self._invalid else ExitCode.CONFIG_ERROR)
        if request.flag("as_json"):
            dump_json({"targets": rows, "invalid": list(self._invalid),
                       "signed": bool(self.config.get_str("secret", ""))})
            return verdict
        if not rows:
            write_line(f"{TAG_NOTE} " + tr("no webhook targets; set modules.webhooks.targets"))
        else:
            print_table([[row["pattern"], row["url"]] for row in rows],
                        headers=("pattern", "url"))
        for problem in self._invalid:
            write_line(f"{TAG_ERROR} {problem}")
        return verdict

    def _test_command(self, request: CliRequest) -> int:
        targets = self.targets()
        event = Event(name=str(request.option("event") or "webhooks.test"),
                      payload={"test": True}, source="webhooks", at=time.time())
        results = []
        for target in targets:
            ok, status, error = self.deliver(target, event)
            results.append({"url": target.masked(), "ok": ok, "status": status,
                            "error": error or None})
        if request.flag("as_json"):
            dump_json({"event": event.name, "results": results})
        elif not results:
            write_line(f"{TAG_NOTE} " + tr("no webhook targets; set modules.webhooks.targets"))
        for row in results if not request.flag("as_json") else []:
            tag = TAG_OK if row["ok"] else TAG_ERROR
            write_line(f"{tag} {row['url']} -> {row['status'] or row['error']}")
        failed = [row for row in results if not row["ok"]]
        return int(ExitCode.ERROR if failed else ExitCode.SUCCESS)


MODULE = WebhooksModule()

"""Metrics module: serve what the chassis and its modules measure, in the formats tools read.

- ``GET /metrics`` - the Prometheus text format: Prometheus, Grafana Agent/Alloy,
  VictoriaMetrics, Netdata and Zabbix scrape it (with the token as a bearer token).
- ``GET /metrics?format=json`` - the same numbers as JSON, for a Home Assistant REST
  sensor, a script, or a dashboard.
- ``kdx metrics`` - the same in the terminal (from the running daemon when there is one).
- ``modules.metrics.snapshot_interval`` - publish ``core.metrics.snapshot`` on the bus every
  N seconds, so the webhooks module, the WebSocket, or a project's own MQTT bridge can push
  the numbers out. Off by default: nothing is sent anywhere unless asked for.

Modules record their own numbers through ``context.metrics`` (counter, gauge, summary,
collector); this module adds the chassis' own: version, uptime, module states, control
plane traffic, jobs, bus activity, and the process' memory and threads.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ...cli.render import dump_json, print_table, tr, write_line
from ...config.schema import ConfigField, ConfigKind, ConfigSchema
from ...constants import TAG_NOTE, ExitCode
from ...kernel.cli_contract import CliArgument, CliCommand, CliRequest, CliSurface
from ...kernel.contract import Module
from ...kernel.ipc import (
    DISCOVERY_NAME,
    IpcDiscovery,
    IpcRequest,
    IpcResponse,
    IpcRoute,
    process_memory,
)
from ...kernel.metrics import PROMETHEUS_CONTENT_TYPE, Sample
from ...kernel.scheduler import ScheduledTask
from ...version import CORE_API_VERSION, __version__

METRICS_PATH = "/metrics"
SNAPSHOT_EVENT = "core.metrics.snapshot"
COLLECTOR = "metrics.chassis"


class MetricsModule(Module):
    """Serves the metrics registry and contributes the chassis' own numbers."""

    name = "metrics"
    description = "Prometheus /metrics, JSON for Home Assistant, kdx metrics, snapshot events."
    version = "0.1.0"

    def __init__(self) -> None:
        super().__init__()
        self._task: ScheduledTask | None = None
        self._lock = threading.Lock()
        self.scrapes = 0
        self.snapshots = 0

    def config_schema(self) -> ConfigSchema:
        """Declare the keys this module reads."""
        return ConfigSchema(
            module=self.name,
            fields=(
                ConfigField(
                    "auth",
                    kind=ConfigKind.BOOL,
                    default=True,
                    description="Require a token on /metrics (Prometheus sends it as a bearer).",
                ),
                ConfigField(
                    "snapshot_interval",
                    kind=ConfigKind.FLOAT,
                    default=0.0,
                    description="Seconds between core.metrics.snapshot events (0: never).",
                ),
            ),
        )

    def ipc_routes(self) -> tuple[IpcRoute, ...]:
        """Serve /metrics (text) and /metrics?format=json."""
        return (
            IpcRoute(
                path=METRICS_PATH,
                handler=self._route,
                methods=("GET",),
                auth=self.config.get_bool("auth", True),
                module=self.name,
                role="viewer",
                summary="Prometheus text format; '?format=json' for JSON",
                content_type=PROMETHEUS_CONTENT_TYPE,
            ),
        )

    def start(self) -> None:
        """Contribute the chassis collector; schedule snapshots when asked for."""
        super().start()
        context = self.require_context()
        context.metrics.collector(COLLECTOR, self.chassis_samples)
        interval = self.config.get_float("snapshot_interval", 0.0)
        if interval > 0:
            self._task = context.schedule(
                max(1.0, interval), self.publish_snapshot, name="metrics-snapshot",
                module=self.name,
            )

    def stop(self) -> None:
        """Withdraw the collector and the snapshot task."""
        task, self._task = self._task, None
        scheduler = self.scheduler
        if task is not None and scheduler is not None:
            scheduler.unschedule(task.name)
        if self.context is not None:
            self.context.metrics.remove_collector(COLLECTOR)
        super().stop()

    def health(self) -> dict[str, Any]:
        """Report scrapes and snapshots beside the lifecycle block."""
        data = super().health()
        data.update(scrapes=self.scrapes, snapshots=self.snapshots,
                    snapshot_scheduled=self._task is not None)
        return data

    def chassis_samples(self) -> list[Sample]:
        """Return the chassis' own measurements, read at scrape time."""
        context = self.require_context()
        rows = [
            Sample("kdx_info", 1.0, {"version": __version__, "core_api": CORE_API_VERSION,
                                     "app": context.app_name}, "gauge", "Chassis build info."),
            Sample("kdx_uptime_seconds", round(context.uptime(), 3), {}, "gauge",
                   "Seconds since the chassis was created."),
            Sample("kdx_threads", float(threading.active_count()), {}, "gauge",
                   "Live threads in the process."),
        ]
        memory = process_memory().get("rss_bytes")
        if memory is not None:
            rows.append(Sample("kdx_process_resident_memory_bytes", float(memory), {}, "gauge",
                               "Resident memory of the chassis process."))
        registry = context.registry
        if registry is not None:
            states: dict[str, int] = {}
            for record in registry.ordered():
                states[record.state.value] = states.get(record.state.value, 0) + 1
            for state, count in sorted(states.items()):
                rows.append(Sample("kdx_modules", float(count), {"state": state}, "gauge",
                                   "Modules by lifecycle state."))
        bus = context.bus.stats()
        for key, help_text in (("published", "Events published on the bus."),
                               ("errors", "Event handlers that raised.")):
            rows.append(Sample(f"kdx_bus_events_{key}_total", float(bus.get(key, 0)), {},
                               "counter", help_text))
        chassis = context.chassis
        ipc = getattr(chassis, "ipc", None)
        if ipc is not None and getattr(ipc, "running", False):
            view = ipc.as_dict()
            rows.append(Sample("kdx_ipc_requests_total", float(view.get("requests", 0)), {},
                               "counter", "Control-plane requests served."))
            rows.append(Sample("kdx_ipc_errors_total", float(view.get("errors", 0)), {},
                               "counter", "Control-plane handlers that raised."))
            rows.append(Sample("kdx_ipc_open_streams", float(ipc.open_streams), {}, "gauge",
                               "Open event streams and WebSockets."))
        jobs = getattr(chassis, "jobs", None)
        if jobs is not None:
            rows.append(Sample("kdx_jobs_running", float(jobs.running()), {}, "gauge",
                               "Background jobs running now."))
        return rows

    def publish_snapshot(self) -> None:
        """Publish every sample as one bus event, for push consumers."""
        payload = self.require_context().metrics.as_dict()
        self.bus.publish(SNAPSHOT_EVENT, payload, source=self.name)
        with self._lock:
            self.snapshots += 1

    def _route(self, request: IpcRequest) -> Any:
        registry = self.require_context().metrics
        with self._lock:
            self.scrapes += 1
        if str(request.argument("format") or "").lower() == "json":
            return registry.as_dict()
        return IpcResponse.text(registry.render_prometheus(), content_type=PROMETHEUS_CONTENT_TYPE)

    # -- CLI ----------------------------------------------------------------------

    def register_cli(self, surface: CliSurface) -> None:
        """Contribute 'kdx metrics'."""
        surface.add_command(
            CliCommand(
                name="metrics",
                help="Show the measurements (from the running daemon when there is one).",
                module_name=self.name,
                handler=self._command,
                args=(
                    CliArgument(("--json",), dest="as_json", action="store_true",
                                help="Emit JSON."),
                    CliArgument(("--prometheus",), dest="prometheus", action="store_true",
                                help="Emit the Prometheus text format."),
                    CliArgument(("--filter",), dest="needle", metavar="TEXT",
                                help="Only metrics whose name contains TEXT."),
                ),
            )
        )

    def _read(self, request: CliRequest) -> tuple[dict[str, Any], str, str]:
        """Return (json view, prometheus text, where it came from)."""
        context = request.context
        if not request.options.get("rpc"):
            found = IpcDiscovery.load(Path(context.paths.state_dir) / DISCOVERY_NAME)
            if found is not None and found.is_process_alive():
                try:
                    status, body = found.request(f"{METRICS_PATH}?format=json", timeout=5.0)
                    _, text = found.request(METRICS_PATH, timeout=5.0)
                except OSError:
                    status, body, text = 0, None, ""
                if status == 200 and isinstance(body, dict):
                    return body, str(text or ""), "daemon"
        registry = context.metrics
        if COLLECTOR not in registry.collectors():
            registry.collector(COLLECTOR, self.chassis_samples)
        return registry.as_dict(), registry.render_prometheus(), "this process"

    def _command(self, request: CliRequest) -> int:
        view, text, origin = self._read(request)
        needle = str(request.option("needle") or "")
        samples = [row for row in view.get("samples", []) if needle in str(row.get("name"))]
        if request.flag("prometheus"):
            lines = [line for line in text.splitlines() if not needle or needle in line]
            for line in lines:
                write_line(line)
            return int(ExitCode.SUCCESS)
        if request.flag("as_json"):
            dump_json({"source": origin, "samples": samples})
            return int(ExitCode.SUCCESS)
        print_table(
            [
                [row["name"], json.dumps(row.get("labels") or {}, sort_keys=True)
                 if row.get("labels") else "", _short(float(row["value"]))]
                for row in samples
            ],
            headers=("metric", "labels", "value"),
        )
        source = tr("the running daemon") if origin == "daemon" else tr(
            "this process (no daemon is running; start one with 'kdx run')"
        )
        write_line(f"{TAG_NOTE} " + tr("{count} samples from {source}", count=len(samples),
                                       source=source))
        return int(ExitCode.SUCCESS)


def _short(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.3f}"



MODULE = MetricsModule()

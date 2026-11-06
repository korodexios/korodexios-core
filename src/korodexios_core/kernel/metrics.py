"""Measurements: a tiny metrics registry and the Prometheus text format, stdlib only.

A module that measures something - a queue length, a request time, a sensor, a disk -
records it here, and every consumer reads the same numbers: ``GET /metrics`` for
Prometheus, Grafana Agent, VictoriaMetrics or Netdata; ``?format=json`` for a Home
Assistant REST sensor or a script; ``kdx metrics`` in the terminal; a periodic
``core.metrics.snapshot`` event for webhooks, the WebSocket, or a project's MQTT bridge.

Three kinds cover what a chassis project measures: a **counter** only goes up (events,
bytes, errors), a **gauge** goes up and down (temperature, queue depth, free space), a
**summary** keeps a count and a sum of observations (durations, sizes) so the rate and
the mean follow. A **collector** is a callable asked at read time, for values that are
cheaper to read than to track (host memory, disk usage, open files).

Names follow Prometheus: ``[a-zA-Z_:][a-zA-Z0-9_:]*``, a unit suffix (``_seconds``,
``_bytes``), ``_total`` on counters; label values are free text.
"""

from __future__ import annotations

import contextlib
import math
import re
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, TypeVar

__all__ = [
    "PROMETHEUS_CONTENT_TYPE",
    "Counter",
    "Gauge",
    "MetricsError",
    "MetricsRegistry",
    "Sample",
    "Summary",
]

PROMETHEUS_CONTENT_TYPE: Final[str] = "text/plain; version=0.0.4; charset=utf-8"
METRIC_NAME: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
LABEL_NAME: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
MAX_SERIES: Final[int] = 1000
"""Most label combinations one metric keeps; a runaway label (a user id) cannot eat memory."""

Labels = tuple[tuple[str, str], ...]


class MetricsError(ValueError):
    """Raised for a bad metric or label name, or a kind clash on one name."""


@dataclass(frozen=True, slots=True)
class Sample:
    """One value of one series, as a collector or a metric reports it."""

    name: str
    value: float
    labels: Mapping[str, str] = field(default_factory=dict)
    kind: str = "gauge"
    help: str = ""


def _labels(values: Mapping[str, Any]) -> Labels:
    for key in values:
        if not LABEL_NAME.match(str(key)) or str(key).startswith("__"):
            raise MetricsError(f"label name {key!r} is not [a-zA-Z_][a-zA-Z0-9_]*")
    return tuple(sorted((str(key), str(value)) for key, value in values.items()))


def _check_name(name: str) -> str:
    text = str(name).strip()
    if not METRIC_NAME.match(text):
        raise MetricsError(f"metric name {name!r} is not [a-zA-Z_:][a-zA-Z0-9_:]*")
    return text


class _Metric:
    kind = "untyped"

    def __init__(self, name: str, help: str = "") -> None:
        self.name = _check_name(name)
        self.help = str(help)
        self._values: dict[Labels, float] = {}
        self._lock = threading.Lock()

    def _key(self, labels: Mapping[str, Any]) -> Labels:
        key = _labels(labels)
        if key not in self._values and len(self._values) >= MAX_SERIES:
            raise MetricsError(f"{self.name} has {MAX_SERIES} label sets; a label is unbounded")
        return key

    def samples(self) -> list[Sample]:
        with self._lock:
            items = list(self._values.items())
        return [Sample(self.name, value, dict(key), self.kind, self.help) for key, value in items]

    def value(self, **labels: Any) -> float:
        """Return the current value of one series (0 when it was never touched)."""
        with self._lock:
            return self._values.get(_labels(labels), 0.0)


class Counter(_Metric):
    """A value that only goes up: requests, bytes sent, errors."""

    kind = "counter"

    def inc(self, amount: float = 1.0, **labels: Any) -> None:
        if amount < 0:
            raise MetricsError(f"{self.name} is a counter; it cannot go down")
        with self._lock:
            key = self._key(labels)
            self._values[key] = self._values.get(key, 0.0) + float(amount)


class Gauge(_Metric):
    """A value that goes up and down: a temperature, a queue depth, free space."""

    kind = "gauge"

    def set(self, value: float, **labels: Any) -> None:
        with self._lock:
            self._values[self._key(labels)] = float(value)

    def inc(self, amount: float = 1.0, **labels: Any) -> None:
        with self._lock:
            key = self._key(labels)
            self._values[key] = self._values.get(key, 0.0) + float(amount)

    def dec(self, amount: float = 1.0, **labels: Any) -> None:
        self.inc(-float(amount), **labels)


class Summary(_Metric):
    """Count and sum of observations (durations, sizes): rate and mean follow from them."""

    kind = "summary"

    def __init__(self, name: str, help: str = "") -> None:
        super().__init__(name, help)
        self._sums: dict[Labels, float] = {}

    def observe(self, value: float, **labels: Any) -> None:
        with self._lock:
            key = self._key(labels)
            self._values[key] = self._values.get(key, 0.0) + 1.0
            self._sums[key] = self._sums.get(key, 0.0) + float(value)

    @contextlib.contextmanager
    def time(self, **labels: Any) -> Iterator[None]:
        """Observe how long the block took, in seconds."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.observe(time.perf_counter() - started, **labels)

    def samples(self) -> list[Sample]:
        with self._lock:
            items = [(key, count, self._sums.get(key, 0.0)) for key, count in self._values.items()]
        rows: list[Sample] = []
        for key, count, total in items:
            rows.append(Sample(f"{self.name}_count", count, dict(key), "summary", self.help))
            rows.append(Sample(f"{self.name}_sum", total, dict(key), "summary", self.help))
        return rows


Collector = Callable[[], Iterable[Sample]]
_M = TypeVar("_M", bound=_Metric)


class MetricsRegistry:
    """Every metric and collector of one chassis; safe across threads."""

    def __init__(self) -> None:
        self._metrics: dict[str, _Metric] = {}
        self._collectors: dict[str, Collector] = {}
        self._lock = threading.Lock()
        self.collector_errors = 0

    def _get(self, kind: type[_M], name: str, help: str) -> _M:
        clean = _check_name(name)
        with self._lock:
            found = self._metrics.get(clean)
            if found is None:
                created = kind(clean, help)
                self._metrics[clean] = created
                return created
            if type(found) is not kind or not isinstance(found, kind):
                raise MetricsError(f"{clean} is already a {found.kind}, not a {kind.kind}")
            return found

    def counter(self, name: str, help: str = "") -> Counter:
        """Return the counter *name*, created on first use."""
        return self._get(Counter, name, help)

    def gauge(self, name: str, help: str = "") -> Gauge:
        """Return the gauge *name*, created on first use."""
        return self._get(Gauge, name, help)

    def summary(self, name: str, help: str = "") -> Summary:
        """Return the summary *name*, created on first use."""
        return self._get(Summary, name, help)

    def collector(self, name: str, read: Collector) -> None:
        """Register (or replace) a collector asked for its samples at every read."""
        with self._lock:
            self._collectors[str(name)] = read

    def remove_collector(self, name: str) -> bool:
        """Withdraw a collector; return whether one was registered."""
        with self._lock:
            return self._collectors.pop(str(name), None) is not None

    def collectors(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._collectors))

    def samples(self) -> list[Sample]:
        """Return every sample now: the metrics' values, then each collector's reading.

        A collector that raises is skipped and counted (``kdx_metrics_collector_errors``);
        one broken sensor never blanks the whole page.
        """
        with self._lock:
            metrics = list(self._metrics.values())
            collectors = list(self._collectors.values())
        rows = [sample for metric in metrics for sample in metric.samples()]
        for read in collectors:
            try:
                rows.extend(read())
            except Exception:
                self.collector_errors += 1
        rows.append(Sample("kdx_metrics_collector_errors", float(self.collector_errors),
                           {}, "counter", "Collector reads that raised."))
        return rows

    def render_prometheus(self) -> str:
        """Return the Prometheus text exposition format (version 0.0.4)."""
        lines: list[str] = []
        described: set[str] = set()
        for sample in sorted(self.samples(), key=lambda item: item.name):
            family = _family(sample)
            if family not in described:
                described.add(family)
                if sample.help:
                    lines.append(f"# HELP {family} {_escape_help(sample.help)}")
                lines.append(f"# TYPE {family} {sample.kind}")
            lines.append(f"{sample.name}{_render_labels(sample.labels)} {_number(sample.value)}")
        return "\n".join(lines) + "\n"

    def as_dict(self) -> dict[str, Any]:
        """Return every sample as JSON: a flat list plus a name -> value map for simple use."""
        samples = self.samples()
        rows = [
            {"name": s.name, "value": s.value, "labels": dict(s.labels), "kind": s.kind}
            for s in samples
        ]
        flat: dict[str, float] = {}
        for sample in samples:
            labels = ",".join(f"{key}={value}" for key, value in sorted(sample.labels.items()))
            flat[f"{sample.name}{{{labels}}}" if labels else sample.name] = sample.value
        return {"at": round(time.time(), 3), "samples": rows, "values": flat}


def _family(sample: Sample) -> str:
    if sample.kind == "summary":
        for suffix in ("_count", "_sum"):
            if sample.name.endswith(suffix):
                return sample.name[: -len(suffix)]
    return sample.name


def _escape_help(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("\n", "\\n")


def _escape_value(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_labels(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{key}="{_escape_value(value)}"' for key, value in sorted(labels.items()))
    return "{" + body + "}"


def _number(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    return repr(float(value)) if not float(value).is_integer() else str(int(value))

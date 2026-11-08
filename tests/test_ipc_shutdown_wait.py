"""Stopping the control plane lets a request that is being answered finish (review R6)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from korodexios_core.kernel.ipc import IpcRequest, IpcRoute
from tests.test_ipc import http_call, make_server


def test_stop_waits_for_a_request_in_flight(tmp_path: Path) -> None:
    started = threading.Event()

    def slow(request: IpcRequest) -> dict[str, str]:
        started.set()
        time.sleep(0.6)
        return {"done": "yes"}

    route = IpcRoute(path="/api/v1/slow", handler=slow, module="test", role="viewer")
    server, _ = make_server(tmp_path, routes=(route,))
    base = server.discovery.base_url()  # type: ignore[union-attr]
    token = server.discovery_token
    answer: dict[str, object] = {}
    caller = threading.Thread(
        target=lambda: answer.update(reply=http_call(base, "/api/v1/slow", token=token))
    )
    caller.start()
    assert started.wait(10)
    begun = time.monotonic()
    server.stop(timeout=5.0)
    waited = time.monotonic() - begun
    caller.join(10)
    assert answer["reply"] == (200, {"done": "yes"}), "the request was cut off, not finished"
    assert 0.2 < waited < 3.0, f"stop waited {waited:.2f}s"


def test_stop_does_not_wait_longer_than_its_budget(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def stuck(request: IpcRequest) -> dict[str, str]:
        started.set()
        release.wait(20)
        return {"done": "late"}

    route = IpcRoute(path="/api/v1/stuck", handler=stuck, module="test", role="viewer")
    server, _ = make_server(tmp_path, routes=(route,))
    base = server.discovery.base_url()  # type: ignore[union-attr]
    token = server.discovery_token
    caller = threading.Thread(target=lambda: http_call(base, "/api/v1/stuck", token=token))
    caller.start()
    assert started.wait(10)
    begun = time.monotonic()
    server.stop(timeout=0.5)
    assert time.monotonic() - begun < 2.5, "a stuck handler must not hold the shutdown"
    release.set()
    caller.join(10)

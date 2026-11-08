"""A busy target and an unwritable discovery file are survived (review 2026-11-08, R4)."""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path

import pytest

from korodexios_core import fsio
from korodexios_core.fsio import atomic_write_text
from korodexios_core.kernel.ipc import IpcOptions, IpcServer


def _sharing_violation() -> PermissionError:
    error = PermissionError(13, "The process cannot access the file")
    error.winerror = 32  # type: ignore[attr-defined]
    return error


def test_a_sharing_violation_is_waited_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.replace
    attempts = {"count": 0}

    def flaky(source: str, target: str) -> None:
        attempts["count"] += 1
        if attempts["count"] < 4:
            raise _sharing_violation()
        real(source, target)

    monkeypatch.setattr(fsio.os, "replace", flaky)
    target = tmp_path / "state.json"
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert attempts["count"] == 4
    assert not list(tmp_path.glob("*.tmp.*")), "no sibling is left behind"


def test_a_permanent_denial_is_raised_after_the_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(source: str, target: str) -> None:
        raise _sharing_violation()

    monkeypatch.setattr(fsio.os, "replace", denied)
    monkeypatch.setattr(fsio, "REPLACE_RETRY_SECONDS", 0.1)
    started = time.monotonic()
    with pytest.raises(PermissionError):
        atomic_write_text(tmp_path / "state.json", "new")
    assert time.monotonic() - started < 2.0
    assert not list(tmp_path.glob("*.tmp.*"))


def test_another_permission_error_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"count": 0}

    def denied(source: str, target: str) -> None:
        calls["count"] += 1
        raise PermissionError(13, "read-only")

    monkeypatch.setattr(fsio.os, "replace", denied)
    with pytest.raises(PermissionError):
        atomic_write_text(tmp_path / "state.json", "new")
    assert calls["count"] == 1


def test_a_failed_discovery_write_leaves_no_listening_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "denied")

    monkeypatch.setattr("korodexios_core.kernel.ipc.atomic_write_text", refuse)
    server = IpcServer(
        status_fn=dict,
        shutdown_fn=lambda: None,
        state_fn=lambda: "running",
        options=IpcOptions(port=0, discovery_path=tmp_path / "var" / "chassis.ipc"),
    )
    with pytest.raises(PermissionError):
        server.start()
    assert server.discovery is not None, "the endpoint it had announced"
    port = server.discovery.port
    with socket.socket() as probe:
        probe.settimeout(0.5)
        assert probe.connect_ex(("127.0.0.1", port)) != 0, "the port is still listening"

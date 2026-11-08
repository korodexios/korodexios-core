"""A busy target and an unwritable discovery file are survived (review 2026-11-08, R4)."""

from __future__ import annotations

import os
import socket
import threading
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


# -- secrets are protected before they are written (review 2026-11-08, S4) --------------------


def test_the_sibling_is_made_private_before_the_secret_reaches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[bool, int]] = []

    def restrict(path: Path) -> bool:
        seen.append((Path(path).exists(), Path(path).stat().st_size))
        return True

    monkeypatch.setattr(fsio, "restrict_to_owner", restrict)
    atomic_write_text(tmp_path / "users.json", "secret", private=True)
    assert seen == [(True, 0)], "restricted while still empty"
    assert (tmp_path / "users.json").read_text(encoding="utf-8") == "secret"


@pytest.mark.posix
def test_a_private_write_that_cannot_be_owner_only_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "users.json"
    target.write_text("old", encoding="utf-8")
    monkeypatch.setattr(fsio, "restrict_to_owner", lambda path: False)
    with pytest.raises(PermissionError):
        atomic_write_text(target, "secret", private=True)
    assert target.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob("*.tmp.*"))


@pytest.mark.posix
def test_a_rewrite_keeps_the_mode_of_a_hand_locked_file(tmp_path: Path) -> None:
    target = tmp_path / "kdx.local.yml"
    target.write_text("a: 1\n", encoding="utf-8")
    target.chmod(0o600)
    atomic_write_text(target, "a: 2\n")
    assert (target.stat().st_mode & 0o777) == 0o600
    private = tmp_path / "secret.json"
    atomic_write_text(private, "x", private=True)
    assert (private.stat().st_mode & 0o777) == 0o600


def test_a_leftover_sibling_from_a_crash_does_not_block_the_write(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    leftover = tmp_path / f"state.json.tmp.{os.getpid()}.{threading.get_ident()}"
    leftover.write_text("torn", encoding="utf-8")
    atomic_write_text(target, "fresh", private=True)
    assert target.read_text(encoding="utf-8") == "fresh"
    assert not leftover.exists()


def test_the_first_audit_record_lands_in_a_file_that_is_already_restricted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from korodexios_core.kernel import audit as audit_module
    from korodexios_core.kernel.audit import AuditLog

    sizes: list[int] = []

    def restrict(path: Path) -> bool:
        sizes.append(Path(path).stat().st_size)
        return True

    monkeypatch.setattr(audit_module, "restrict_to_owner", restrict)
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("rpc", caller=None, outcome="ok")
    assert sizes == [0], "restricted before the first record was written"
    assert (tmp_path / "audit.jsonl").read_text(encoding="utf-8").count("\n") == 1


def test_a_byte_order_mark_does_not_break_a_manifest_or_a_config_file(tmp_path: Path) -> None:
    """Review 2026-11-08, V6: Notepad and some editors prepend U+FEFF to UTF-8 files."""
    from korodexios_core.config.loaders import read_text
    from korodexios_core.kernel.discovery import ModuleDiscovery
    from korodexios_core.paths import AppPaths
    from tests.test_discovery import MANIFEST, build_plugins

    bom = chr(0xFEFF)
    config = tmp_path / "kdx.yml"
    config.write_text(bom + "core:" + chr(10) + "  name: demo" + chr(10), encoding="utf-8")
    assert read_text(config).startswith("core:")

    plugins = build_plugins(tmp_path / "plugins")
    (plugins / "probe" / "kdx_module.toml").write_text(bom + MANIFEST, encoding="utf-8")
    root = tmp_path / "project"
    root.mkdir()
    paths = AppPaths.discover(root=str(root), environ={}, extra_module_dirs=[str(plugins)])
    report = ModuleDiscovery(paths, logger=None).scan()
    assert "probe" in report.names()
    assert not [f for f in report.failures if f.name == "probe"]

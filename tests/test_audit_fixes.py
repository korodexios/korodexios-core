"""Regression tests for the findings of the November 2026 project audit."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.cli import commands as core_commands
from korodexios_core.cli.service import _systemd_unit, _task_xml
from korodexios_core.config.layered import (
    declare_secret_keys,
    is_secret_key,
    mask_url_credentials,
    redact_for_display,
    redact_value,
)
from korodexios_core.config.schema import ConfigField, ConfigSchema
from korodexios_core.config.unloaded import declare_loaded_modules
from korodexios_core.kernel.cli_contract import CliRequest
from korodexios_core.kernel.ipc import (
    MAX_STREAMS,
    TOKEN_HEADER,
    IpcOptions,
    IpcServer,
    presented_token,
)
from korodexios_core.kernel.process import ProcessSupervisor, safe_argv
from korodexios_core.kernel.queue import JobQueue, worker_identity
from korodexios_core.kernel.registry import collect_schema
from korodexios_core.modules.backup_engine.engine import (
    BackupSettings,
    build_plan,
    execute_plan,
    parse_stamp,
)


class _Log:
    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    def _add(self, message: str, **fields: Any) -> None:
        self.lines.append((message, fields))

    debug = info = warning = error = _add


# -- run_process ----------------------------------------------------------------------


def test_a_child_that_ignores_stdin_still_times_out() -> None:
    """A full stdin pipe used to block the writer past the timeout and the stop."""
    started = time.monotonic()
    result = ProcessSupervisor().run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout=1.0,
        stdin="x" * 2_000_000,
    )
    assert result.timed_out
    assert time.monotonic() - started < 15.0


def test_stdin_still_reaches_a_child_that_reads_it() -> None:
    result = ProcessSupervisor().run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
        stdin="hello",
        timeout=30,
    )
    assert result.ok and result.stdout.strip() == "HELLO"


def test_argv_is_masked_in_the_log_and_the_event() -> None:
    log = _Log()
    result = ProcessSupervisor(logger=log).run(
        [sys.executable, "-c", "pass", "--token", "abc", "https://u:pw@host/x"], timeout=30
    )
    started = next(fields for message, fields in log.lines if "started" in message)
    assert "abc" not in str(started["command"]) and "pw@" not in str(started["command"])
    assert "abc" not in str(result.as_dict()["command"])
    assert safe_argv(["--password=x"]) == ["--password=***redacted***"]


# -- secrets ----------------------------------------------------------------------------


def test_schema_declared_secrets_are_masked_whatever_their_name() -> None:
    assert not is_secret_key("modules.audit_probe.targets")

    class Probe:
        def config_schema(self) -> ConfigSchema:
            return ConfigSchema(fields=(ConfigField("targets", kind="list", secret=True),))

    collect_schema(Probe(), "audit_probe", _Log())
    assert is_secret_key("modules.audit_probe.targets")
    assert redact_value(["https://a", "https://b"]) == ["***redacted***", "***redacted***"]


def test_url_passwords_are_masked_under_any_key() -> None:
    declare_loaded_modules(["x"])  # keys of a module that is not loaded are masked wholesale
    assert mask_url_credentials("postgres://app:hunter2@db/app") == (
        "postgres://app:***redacted***@db/app"
    )
    assert redact_for_display("modules.x.database_url", "redis://:pw@h") == (
        "redis://:***redacted***@h"
    )
    assert redact_for_display("modules.x.plain", "https://example.com/x") == (
        "https://example.com/x"
    )
    assert is_secret_key("modules.x.signing_key")
    assert is_secret_key("modules.x.session_cookie")
    declare_secret_keys([" "])  # blank names are ignored


# -- token presentation -----------------------------------------------------------------


def test_bearer_and_chassis_header_both_present_a_token() -> None:
    assert presented_token({TOKEN_HEADER: "a", "Authorization": "Bearer b"}) == "a"
    assert presented_token({"Authorization": "Bearer  b "}) == "b"
    assert presented_token({"Authorization": "Basic Zm9v"}) == ""
    assert presented_token({}) == ""


def test_the_local_token_is_never_sent_to_a_remote_target(capsys) -> None:
    class Config:
        def get_str(self, key: str, default: str = "") -> str:
            return "local-admin" if key == "core.ipc.token" else default

    class FakeChassis:
        config = Config()

    def request(**options: Any) -> CliRequest:
        return CliRequest(
            context=None,  # type: ignore[arg-type]
            command="status",
            namespace=argparse.Namespace(**options),
        )

    chassis: Any = FakeChassis()
    assert core_commands.target_token(request(target_token=None), chassis, "") == "local-admin"
    assert core_commands.target_token(
        request(target_token=None), chassis, "http://127.0.0.1:18123"
    ) == "local-admin"
    assert core_commands.target_token(request(target_token=None), chassis, "nas.lan:1") == ""
    assert "not sent to nas.lan" in capsys.readouterr().out
    assert core_commands.target_token(request(target_token="t"), chassis, "nas.lan") == "t"


# -- streams ----------------------------------------------------------------------------


def test_stream_slots_are_bounded(tmp_path: Path) -> None:
    server = IpcServer(
        status_fn=dict,
        shutdown_fn=lambda: None,
        state_fn=lambda: "running",
        options=IpcOptions(port=0, discovery_path=tmp_path / "chassis.ipc"),
    )
    taken = [server.open_stream() for _ in range(MAX_STREAMS)]
    assert all(taken) and server.open_streams == MAX_STREAMS
    assert server.open_stream() is False
    server.close_stream()
    assert server.open_stream() is True


# -- durable queue ----------------------------------------------------------------------


def test_the_queue_rebuilds_its_schema_when_the_file_vanishes(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "queue.sqlite3")
    queue.submit("a", 1)
    for item in tmp_path.iterdir():
        item.unlink()
    assert queue.submit("a", 2).kind == "a"


def test_recover_leaves_a_task_a_live_process_owns(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "queue.sqlite3")
    queue.handle("a", lambda task: None)
    mine = queue.submit("a")
    theirs = queue.submit("a")
    assert queue.claim() is not None and queue.claim() is not None
    host = worker_identity().rsplit(":", 1)[0]
    parent = os.getppid()  # alive for as long as this test runs
    with queue._connect() as db:
        db.execute("UPDATE tasks SET owner=? WHERE id=?", (f"{host}:{parent}", theirs.id))
        db.execute("UPDATE tasks SET owner=? WHERE id=?", ("elsewhere:1", mine.id))
    assert queue.recover() == 1
    assert queue.get(mine.id).state == "queued"  # type: ignore[union-attr]
    assert queue.get(theirs.id).state == "running"  # type: ignore[union-attr]
    assert queue.get(theirs.id).as_dict()["owner"] == f"{host}:{parent}"  # type: ignore[union-attr]


# -- backups ----------------------------------------------------------------------------


def test_two_runs_planned_for_one_folder_never_share_it(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    settings = BackupSettings(source=source, dest=tmp_path / "dest")
    first = build_plan(settings, stamp="2026-11-06T10-00-00")
    second = build_plan(settings, stamp="2026-11-06T10-00-00")
    assert first.target == second.target
    execute_plan(first)
    execute_plan(second)
    assert first.target != second.target
    assert second.target.name == "2026-11-06T10-00-00-1"
    assert parse_stamp(second.target.name) is not None
    third = build_plan(settings, stamp="2026-11-06T10-00-00")
    execute_plan(third)
    assert third.target.name == "2026-11-06T10-00-00-2"


def test_a_file_that_vanishes_before_a_dry_run_is_an_error_not_a_crash(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    doomed = source / "gone.txt"
    doomed.write_text("x", encoding="utf-8")
    plan = build_plan(BackupSettings(source=source, dest=tmp_path / "dest", dry_run=True))
    doomed.unlink()
    result = execute_plan(plan)
    assert result.copied == 0 and result.errors and "gone.txt" in result.errors[0]


# -- service definitions ----------------------------------------------------------------


def test_systemd_unit_escapes_specifiers(tmp_path: Path) -> None:
    root = tmp_path / "100%done"
    unit = _systemd_unit("demo", ["/usr/bin/python3", "--home", str(root)], root)
    assert "100%%done" in unit and "100%done" not in unit.replace("100%%done", "")


@pytest.mark.parametrize("root", ["C:\\", "C:\\My Projects\\app"])
def test_task_xml_quotes_arguments_like_the_scheduler_splits_them(root: str) -> None:
    xml = _task_xml("python.exe", ["-m", "korodexios_core", "--home", root, "run"], Path(root), "x")
    arguments = xml.split("<Arguments>", 1)[1].split("</Arguments>", 1)[0]
    if " " in root:
        assert f'"{root}"' in arguments
    else:
        assert root in arguments and '"' not in arguments


def test_locale_extract_writes_lf_catalogues(
    project_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A text-mode write on Windows turned every newline of a catalogue into CRLF.

    The writes are intercepted, so the package's own catalogues are never touched.
    """
    from korodexios_core.cli.entry import main

    seen: list[tuple[str, Any]] = []

    def spy(self: Path, data: str, encoding: Any = None, errors: Any = None,
            newline: Any = None) -> int:
        seen.append((self.name, newline))
        return len(data)

    source = tmp_path / "source"
    source.mkdir()
    (source / "m.py").write_text("write_line('a brand new text')\n", encoding="utf-8")
    monkeypatch.setattr(Path, "write_text", spy)
    args = ["--home", str(project_root), "--no-log-file", "--no-color"]
    assert main([*args, "locale", "extract", "--source", str(source)]) == 0
    catalogues = [item for item in seen if item[0].endswith((".pot", ".po"))]
    assert catalogues and all(newline == "\n" for _name, newline in catalogues)


def test_own_host_names_never_ask_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reverse lookup stalled the first foreign-Host request past a client timeout on macOS."""
    import socket as socket_module

    from korodexios_core.kernel import ipc as ipc_module

    def no_dns(*_args: object) -> str:
        raise AssertionError("the control plane must not ask DNS for its own names")

    monkeypatch.setattr(socket_module, "getfqdn", no_dns)
    monkeypatch.setattr(socket_module, "gethostbyaddr", no_dns)
    monkeypatch.setattr(socket_module, "gethostname", lambda: "Mac-mini.local")
    ipc_module._own_host_names.cache_clear()
    try:
        names = ipc_module._own_host_names()
        assert {"mac-mini.local", "mac-mini"} <= names
        started = time.monotonic()
        server = IpcServer(
            status_fn=dict, shutdown_fn=lambda: None, state_fn=lambda: "running",
            options=IpcOptions(host="0.0.0.0", port=0),
        )
        assert server.cross_origin_refusal("nas.lan:18123", None)
        assert server.cross_origin_refusal("mac-mini:18123", None) is None
        assert time.monotonic() - started < 1.0
    finally:
        ipc_module._own_host_names.cache_clear()

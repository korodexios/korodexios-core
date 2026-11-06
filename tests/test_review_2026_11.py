"""The November 2026 review: each finding, pinned by a test that failed before its fix."""

from __future__ import annotations

import http.client
import importlib.util
import io
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.cli.init_project import build_files
from korodexios_core.kernel import rpc_output
from korodexios_core.kernel.audit import AuditLog, read_audit, redact_detail
from korodexios_core.kernel.chassis import Chassis, ChassisOptions
from korodexios_core.kernel.cli_contract import CliArgument, CliCommand, CliRequest
from korodexios_core.kernel.ipc import (
    JOBS_PATH,
    MAX_IPC_PAYLOAD_BYTES,
    TOKEN_HEADER,
    IpcOptions,
    IpcResponse,
    IpcRoute,
    IpcServer,
)
from korodexios_core.kernel.reset import apply_reset, plan_reset
from korodexios_core.kernel.rpc_output import capture_output, captured_text
from korodexios_core.kernel.users import Caller, UserStore, acting_as
from korodexios_core.modules.backup_engine import BackupSettings
from korodexios_core.modules.backup_engine.engine import build_plan, default_stamp, parse_stamp
from korodexios_core.modules.backup_engine.module import BackupEngineModule
from korodexios_core.paths import AppPaths

from .test_backup_engine import sweep_chassis
from .test_docs import probe_project
from .test_ipc import ipc_chassis
from .test_jobs import wait_for
from .test_reset import populate

TOKEN = "t" * 32


# --- 3.1 retention sees same-second siblings -------------------------------------------


def test_a_suffixed_stamp_is_still_a_stamp() -> None:
    assert parse_stamp("2020-01-01T00-00-00") is not None
    assert parse_stamp("2020-01-01T00-00-00-1") == parse_stamp("2020-01-01T00-00-00")
    assert parse_stamp("2020-01-01T00-00-00-12") is not None
    for label in ("hand-made", "2020-01-01T00-00-00-x", "2020-01-01T00-00-00-0",
                  "2020-01-01T00-00-00-01", "2020-01-01"):
        assert parse_stamp(label) is None, label
    assert BackupEngineModule._stamp_time("2020-01-01T00-00-00-3") is not None


def test_auto_prune_removes_a_suffixed_copy(tmp_path: Path) -> None:
    chassis = sweep_chassis(tmp_path, **{"modules.backup_engine.schedule_interval": 0.0})
    try:
        module = chassis.registry.require("backup_engine").module
        dest = Path(module.settings().dest)
        old = dest / f"{default_stamp(time.time() - 5 * 86_400.0)}-1"
        old.mkdir(parents=True)
        assert module.retention_report().copies_standing == 1
        assert module._prune_dest(dest).removed == 1
        assert not old.exists()
    finally:
        chassis.shutdown()


# --- 3.8 the destination guard is a path, not a pattern ---------------------------------


def test_the_destination_guard_spares_a_namesake_folder(tmp_path: Path) -> None:
    source = tmp_path / "tree"
    (source / "src" / "backups").mkdir(parents=True)
    (source / "src" / "backups" / "keep.txt").write_text("keep\n", encoding="utf-8")
    (source / "backups" / "old").mkdir(parents=True)
    (source / "backups" / "old" / "copy.txt").write_text("copy\n", encoding="utf-8")
    plan = build_plan(BackupSettings(source=source, dest=source / "backups"))
    copied = sorted(path.relative_to(source).as_posix() for path in plan.files)
    assert copied == ["src/backups/keep.txt"]
    assert plan.auto_excluded == ["backups"]


def test_the_guard_holds_for_a_destination_not_yet_created(tmp_path: Path) -> None:
    source = tmp_path / "tree"
    source.mkdir()
    (source / "a.txt").write_text("a\n", encoding="utf-8")
    plan = build_plan(BackupSettings(source=source, dest=source / "var" / "lib" / "copies"))
    assert plan.auto_excluded == ["var/lib/copies"]


# --- 3.2 / 4.1 / 4.2 the control plane --------------------------------------------------


def _server(tmp_path: Path, **options: Any) -> tuple[IpcServer, dict[str, Any]]:
    seen: dict[str, Any] = {}

    def probe(request: Any) -> Any:
        seen["payload"] = request.payload
        return {"ok": True}

    server = IpcServer(
        status_fn=dict,
        shutdown_fn=lambda: None,
        state_fn=lambda: "running",
        options=IpcOptions(port=0, token=TOKEN, discovery_path=tmp_path / "chassis.ipc",
                           **options),
        routes=[
            IpcRoute(path="/api/v1/probe", handler=probe, methods=("GET", "POST")),
            IpcRoute(
                path="/api/v1/framed",
                handler=lambda request: IpcResponse.html(
                    "<p>x</p>", headers=(("Content-Security-Policy", "default-src 'self'"),)
                ),
            ),
        ],
        jobs_fn=lambda job_id: {"jobs": []},
        cancel_fn=lambda job_id: None,
    )
    return server, seen


def _oversized(port: int, path: str) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.putrequest("POST", path)
    connection.putheader(TOKEN_HEADER, TOKEN)
    connection.putheader("Content-Length", str(MAX_IPC_PAYLOAD_BYTES + 1))
    connection.endheaders()
    return connection.getresponse().status


def test_an_oversized_body_is_413_on_module_routes_and_jobs(tmp_path: Path) -> None:
    server, seen = _server(tmp_path)
    server.start()
    try:
        port = server.discovery.port  # type: ignore[union-attr]
        assert _oversized(port, "/api/v1/probe") == 413
        assert "payload" not in seen, "the handler never sees an oversized body"
        assert _oversized(port, JOBS_PATH) == 413
    finally:
        server.stop()


def test_every_response_carries_the_security_headers(tmp_path: Path) -> None:
    server, _ = _server(tmp_path)
    server.start()
    try:
        port = server.discovery.port  # type: ignore[union-attr]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/health")
        response = connection.getresponse()
        assert response.getheader("X-Frame-Options") == "DENY"
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert response.getheader("Content-Security-Policy") == "frame-ancestors 'none'"
        assert response.getheader("Referrer-Policy") == "no-referrer"
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/api/v1/framed", headers={TOKEN_HEADER: TOKEN})
        response = connection.getresponse()
        assert response.getheader("Content-Security-Policy") == "default-src 'self'", (
            "a module's own policy is kept, not doubled"
        )
        assert server.as_dict()["requests"] == 2, "every request is counted"
    finally:
        server.stop()


def test_allowed_hosts_close_dns_rebinding_on_a_lan_bind(tmp_path: Path) -> None:
    guarded, _ = _server(tmp_path, host="0.0.0.0", allowed_hosts=("nas.lan", "192.168.1.20"))
    assert guarded.cross_origin_refusal("evil.example:18123", "http://evil.example:18123")
    assert guarded.cross_origin_refusal("nas.lan:18123", "http://nas.lan:18123") is None
    assert guarded.cross_origin_refusal("192.168.1.20:18123", None) is None
    assert guarded.cross_origin_refusal("127.0.0.1:18123", None) is None
    open_plane, _ = _server(tmp_path, host="0.0.0.0")
    # No list: a rebinding name is refused, addresses and this machine's names still work.
    assert open_plane.cross_origin_refusal("evil.example:18123", "http://evil.example:18123")
    assert open_plane.cross_origin_refusal("192.168.1.20:18123", None) is None
    assert open_plane.cross_origin_refusal("[fe80::1]:18123", None) is None
    own = socket.gethostname().lower()
    assert open_plane.cross_origin_refusal(f"{own}:18123", f"http://{own}:18123") is None


def test_the_chassis_reads_allowed_hosts(project_root: Path) -> None:
    paths = AppPaths.discover(root=str(project_root), environ={})
    chassis = Chassis.create(
        ChassisOptions(
            home=paths.root,
            environ={},
            cli_overrides={
                "core.log.file_enabled": False,
                "core.ipc.enabled": True,
                "core.ipc.port": 0,
                "core.ipc.allowed_hosts": ["nas.lan"],
            },
        )
    )
    chassis.initialize()
    chassis.start()
    try:
        assert chassis.ipc is not None
        assert chassis.ipc._options.allowed_hosts == ("nas.lan",)
    finally:
        chassis.shutdown()


# --- 4.3 the audit masks secrets ---------------------------------------------------------


def test_the_audit_masks_secret_arguments(tmp_path: Path) -> None:
    detail = {
        "argv": ["--token", "abc", "--name", "x", "--api-key=zz"],
        "password": "hunter2",
        "source": "/data",
    }
    masked = redact_detail(detail)
    assert masked["argv"][1] != "abc"
    assert masked["argv"][3] == "x"
    assert masked["argv"][4].startswith("--api-key=") and "zz" not in masked["argv"][4]
    assert masked["password"] != "hunter2"
    assert masked["source"] == "/data"
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("rpc", caller=None, outcome="exit 0", detail=detail)
    text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "hunter2" not in text and "abc" not in text
    assert read_audit(tmp_path / "audit.jsonl")[0]["detail"]["source"] == "/data"


# --- 3.3 RPC arguments are validated like terminal tokens -------------------------------


def _probe_chassis(project_root: Path, handler: Any = None) -> tuple[Chassis, dict[str, Any]]:
    seen: dict[str, Any] = {}

    def remember(request: CliRequest) -> int:
        seen.update(vars(request.namespace))
        return 0

    chassis = ipc_chassis(project_root)
    chassis.initialize()
    record = chassis.registry.get("heartbeat")
    assert record is not None
    record.commands.append(
        CliCommand(
            name="probe",
            handler=handler or remember,
            args=(
                CliArgument(("--mode",), choices=("fast", "slow"), default="fast"),
                CliArgument(("--count",), type="int", default=1),
                CliArgument(("--loud",), action="store_true"),
                CliArgument(("--tag",), action="append"),
            ),
        )
    )
    return chassis, seen


def test_rpc_mapping_values_pass_the_declared_checks(project_root: Path) -> None:
    chassis, seen = _probe_chassis(project_root)
    try:
        result = chassis.dispatch_rpc(
            "probe", {"mode": "slow", "count": "5", "loud": True, "tag": "a"}
        )
        assert result["exit_code"] == 0
        assert seen["mode"] == "slow" and seen["count"] == 5 and seen["loud"] is True
        assert seen["tag"] == ["a"]
        for bad, message in (
            ({"mode": "warp"}, "invalid choice"),
            ({"count": "many"}, "invalid value"),
            ({"count": 2.5}, "integer"),
            ({"loud": "yes"}, "flag"),
            ({"mode": ["fast"]}, "single value"),
            ({"bogus": 1}, "declares no argument"),
        ):
            with pytest.raises(TypeError, match=message):
                chassis.dispatch_rpc("probe", bad)
    finally:
        chassis.shutdown()


# --- 3.4 / 4.5 jobs at shutdown and job privacy -----------------------------------------


def test_shutdown_cancels_running_jobs_before_the_modules_stop(project_root: Path) -> None:
    observed: dict[str, Any] = {}

    def patient(request: CliRequest) -> int:
        while not request.cancelled():
            time.sleep(0.01)
        observed["cancelled"] = True
        return 130

    chassis, _ = _probe_chassis(project_root, handler=patient)
    job = chassis.submit_job("probe")
    wait_for(lambda: chassis.jobs.get(job["id"]) is not None)
    chassis.shutdown()
    assert observed.get("cancelled") is True
    finished = chassis.jobs.get(job["id"])
    assert finished is not None and finished.state == "cancelled"


def test_a_job_that_ignores_cancel_is_audited_as_abandoned(project_root: Path) -> None:
    release = threading.Event()

    def stubborn(request: CliRequest) -> int:
        release.wait(5.0)
        return 0

    chassis, _ = _probe_chassis(project_root, handler=stubborn)
    chassis.config = chassis.config.with_cli_overrides({"core.lifecycle.stop_timeout": 0.1})
    try:
        chassis.submit_job("probe")
        chassis.shutdown()
        outcomes = [entry["outcome"] for entry in read_audit(chassis.audit.path)]  # type: ignore[union-attr]
        assert "abandoned at shutdown" in outcomes
    finally:
        release.set()


def test_job_output_is_for_its_owner_and_admins(project_root: Path) -> None:
    chassis, _ = _probe_chassis(project_root)
    try:
        with acting_as(Caller("alice", "operator", "user")):
            job = chassis.submit_job("probe")
        wait_for(lambda: chassis.jobs.get(job["id"]).state != "running")  # type: ignore[union-attr]
        with acting_as(Caller("bob", "viewer", "user")):
            assert chassis.job_report(job["id"]) is None
            assert chassis.job_report()["jobs"][0]["id"] == job["id"], "the list stays visible"
        with acting_as(Caller("alice", "operator", "user")):
            assert "output" in chassis.job_report(job["id"])
        with acting_as(Caller("carol", "admin", "user")):
            assert "output" in chassis.job_report(job["id"])
    finally:
        chassis.shutdown()


# --- 4.4 captured output is bounded while written ---------------------------------------


def test_capture_is_bounded_at_write_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rpc_output, "MAX_CAPTURED_CHARS", 10)
    with capture_output() as buffer:
        sys.stdout.write("x" * 25)
        sys.stdout.write("y" * 5)
    assert buffer.getvalue() == "x" * 10, "nothing past the cap is kept in memory"
    assert "20 more characters" in captured_text(buffer)
    assert captured_text(io.StringIO("short")) == "short"


# --- 3.5 / 4.6 users.json across processes ----------------------------------------------


def test_two_stores_writing_at_once_lose_nothing(tmp_path: Path) -> None:
    path = tmp_path / "users.json"
    stores = [UserStore(path), UserStore(path)]

    def add_many(index: int) -> None:
        for number in range(8):
            stores[index].add(f"user-{index}-{number}", "viewer")

    threads = [threading.Thread(target=add_many, args=(index,)) for index in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert len(UserStore(path).users()) == 16


def test_a_rotated_token_stops_working_in_another_store(tmp_path: Path) -> None:
    path = tmp_path / "users.json"
    writer, daemon = UserStore(path), UserStore(path)
    old = writer.add("dana", "operator")
    assert daemon.authenticate(old) is not None
    new = writer.rotate("dana")
    assert daemon.authenticate(old) is None
    assert daemon.authenticate(new) is not None


# --- 3.6 a reload reads the environment the chassis booted from --------------------------


def test_reload_reads_the_boot_environment(project_root: Path) -> None:
    paths = AppPaths.discover(root=str(project_root), environ={})
    chassis = Chassis.create(
        ChassisOptions(
            home=paths.root,
            environ={"KDX_CORE__CLI__WIDTH": "123"},
            cli_overrides={"core.log.file_enabled": False},
        )
    )
    try:
        assert chassis.config.get_int("core.cli.width") == 123
        chassis.reload_config()
        assert chassis.config.get_int("core.cli.width") == 123
    finally:
        chassis.shutdown()


# --- 3.7 a vanished file is not reported as freed ---------------------------------------


def test_reset_does_not_count_a_vanished_file(tmp_path: Path) -> None:
    populate(tmp_path)
    plan = plan_reset(tmp_path, ["var/lib"], ["data"])
    (tmp_path / "var" / "lib" / "index.db").unlink()
    result = apply_reset(tmp_path, plan)
    assert result.failures == []
    assert result.removed == len(plan.entries) - 1
    assert result.freed == 5


# --- 3.9 the seeded project follows core.locale ------------------------------------------


def test_the_seeded_i18n_follows_core_locale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = build_files(probe_project())
    package = tmp_path / "probe"
    (package / "locales" / "sk").mkdir(parents=True)
    (package / "i18n.py").write_text(files["src/probe/i18n.py"], encoding="utf-8")
    (package / "locales" / "sk" / "probe.po").write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "Hello"\nmsgstr "Ahoj"\n',
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("probe_i18n", package / "i18n.py")
    assert spec is not None and spec.loader is not None
    seeded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seeded)
    monkeypatch.setenv("KDX_LOCALE", "en")
    assert seeded.translate("Hello") == "Hello", "before the chassis speaks, the env decides"
    seeded.use_locale("sk")
    assert seeded.translate("Hello") == "Ahoj", "core.locale wins once configured"
    assert 'use_locale(context.config.get_str("core.locale", "auto"))' in (
        files["src/probe/modules/app/module.py"]
    )

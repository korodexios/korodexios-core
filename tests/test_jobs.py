"""Background jobs and command permissions over the control plane."""

from __future__ import annotations

import io
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.kernel.chassis import Chassis
from korodexios_core.kernel.cli_contract import CliCommand, CliRequest
from korodexios_core.kernel.ipc import JOBS_PATH, RPC_PATH, IpcOptions, IpcServer
from korodexios_core.kernel.jobs import Job, JobLimitError, JobRegistry

from .test_ipc import ipc_chassis
from .test_ipc_routes import http_call


def wait_for(predicate: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")


def test_a_job_runs_in_the_background_and_reports_its_output() -> None:
    registry = JobRegistry()
    gate = threading.Event()

    def prepare(buffer: io.StringIO) -> Any:
        def run() -> dict[str, Any]:
            buffer.write("first line\n")
            gate.wait(timeout=5)
            buffer.write("second line\n")
            return {"exit_code": 0}

        return run

    job = registry.submit("demo", prepare)
    assert job.id == "job-1"
    wait_for(lambda: "first line" in job.as_dict()["output"])
    assert job.state == "running"
    assert registry.running() == 1
    gate.set()
    wait_for(lambda: job.state == "done")
    report = job.as_dict()
    assert report["exit_code"] == 0
    assert report["output"] == "first line\nsecond line\n"
    assert report["finished_at"] is not None
    assert "output" not in job.as_dict(output=False)
    assert registry.get("job-1") is job
    assert registry.get("job-9") is None


def test_failures_and_exit_codes_are_recorded() -> None:
    registry = JobRegistry()

    def raises(buffer: io.StringIO) -> Any:
        def run() -> dict[str, Any]:
            raise ValueError("boom")

        return run

    def exits(buffer: io.StringIO) -> Any:
        return lambda: {"exit_code": 3}

    crashed = registry.submit("crash", raises)
    nonzero = registry.submit("exit", exits)
    wait_for(lambda: crashed.state != "running" and nonzero.state != "running")
    assert crashed.state == "failed"
    assert crashed.error == "ValueError: boom"
    assert nonzero.state == "failed"
    assert nonzero.as_dict()["exit_code"] == 3


def test_a_refusal_raises_before_any_job_exists() -> None:
    registry = JobRegistry()

    def refuse(buffer: io.StringIO) -> Any:
        raise LookupError("no such command")

    with pytest.raises(LookupError):
        registry.submit("nope", refuse)
    assert registry.list() == []


def test_the_running_limit_and_the_memory_bound() -> None:
    registry = JobRegistry(max_running=1, keep=2)
    gate = threading.Event()
    blocked = registry.submit("slow", lambda buffer: lambda: gate.wait(5) and {"exit_code": 0})
    with pytest.raises(JobLimitError, match="limit 1"):
        registry.submit("second", lambda buffer: lambda: {"exit_code": 0})
    gate.set()
    wait_for(lambda: blocked.state == "done")
    for index in range(4):
        job = registry.submit(f"quick {index}", lambda buffer: lambda: {"exit_code": 0})
        wait_for(lambda job=job: job.state == "done")  # type: ignore[misc]
    remembered = [job.command for job in registry.list()]
    assert len(remembered) <= 3
    assert "slow" not in remembered


def test_job_as_dict_rounds_and_measures() -> None:
    job = Job(id="job-7", command="x", started_at=100.0, finished_at=101.5, state="done")
    assert job.as_dict(output=False)["seconds"] == 1.5


def local_only_chassis(project_root: Path) -> Chassis:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    chassis.registry.commands()  # the built-ins are registered
    return chassis


def test_remote_false_is_refused_and_inherited(project_root) -> None:
    chassis = local_only_chassis(project_root)
    try:
        with pytest.raises(PermissionError, match="own terminal"):
            chassis.dispatch_rpc("backup configure")
        # A group's flag covers its children.
        group = CliCommand(
            name="vault",
            remote=False,
            subcommands=(CliCommand(name="open", handler=lambda request: 0),),
        )
        record = chassis.registry.get("heartbeat")
        assert record is not None
        record.commands.append(group)
        with pytest.raises(PermissionError):
            chassis.dispatch_rpc("vault open")
    finally:
        chassis.shutdown()


def test_jobs_over_the_control_plane(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    chassis.start()
    try:
        discovery = chassis.ipc.discovery  # type: ignore[union-attr]
        base, token = discovery.base_url(), discovery.token
        code, payload = http_call(
            base, RPC_PATH, method="POST", token=token,
            payload={"command": "heartbeat status", "background": True},
        )
        assert code == 202
        job_id = payload["job"]["id"]  # type: ignore[index]
        wait_for(
            lambda: http_call(base, f"{JOBS_PATH}?id={job_id}", token=token)[1]["state"]  # type: ignore[index]
            != "running"
        )
        code, job = http_call(base, f"{JOBS_PATH}?id={job_id}", token=token)
        assert code == 200
        assert job["state"] == "done"  # type: ignore[index]
        assert "heartbeat" in job["output"].lower()  # type: ignore[index]
        code, listing = http_call(base, JOBS_PATH, token=token)
        assert listing["jobs"][0]["id"] == job_id  # type: ignore[index]
        assert "output" not in listing["jobs"][0]  # type: ignore[index]
        assert http_call(base, f"{JOBS_PATH}?id=job-999", token=token)[0] == 404
        assert http_call(base, JOBS_PATH)[0] == 401
        assert http_call(base, JOBS_PATH, method="POST", token=token, payload={})[0] == 405
        code, refused = http_call(
            base, RPC_PATH, method="POST", token=token,
            payload={"command": "backup configure", "background": True},
        )
        assert code == 403
        assert "own terminal" in refused["error"]  # type: ignore[index]
        code, _ = http_call(
            base, RPC_PATH, method="POST", token=token, payload={"command": "backup configure"}
        )
        assert code == 403
    finally:
        chassis.shutdown()


def test_a_busy_daemon_answers_429(project_root, monkeypatch) -> None:
    chassis = ipc_chassis(project_root)
    monkeypatch.setattr(chassis, "jobs", JobRegistry(max_running=1))
    gate = threading.Event()

    def slow(request: CliRequest) -> int:
        gate.wait(5)
        return 0

    chassis.initialize()
    record = chassis.registry.get("heartbeat")
    assert record is not None
    record.commands.append(CliCommand(name="slow", handler=slow))
    chassis.start()
    try:
        discovery = chassis.ipc.discovery  # type: ignore[union-attr]
        base, token = discovery.base_url(), discovery.token
        body = {"command": "slow", "background": True}
        assert http_call(base, RPC_PATH, method="POST", token=token, payload=body)[0] == 202
        code, payload = http_call(base, RPC_PATH, method="POST", token=token, payload=body)
        assert code == 429
        assert "already running" in payload["error"]  # type: ignore[index]
    finally:
        gate.set()
        chassis.shutdown()


def test_a_plane_without_jobs_says_so(tmp_path: Path) -> None:
    server = IpcServer(
        status_fn=lambda: {},
        shutdown_fn=lambda: None,
        state_fn=lambda: "running",
        options=IpcOptions(port=0, discovery_path=tmp_path / "chassis.ipc", token="t"),
        rpc_fn=lambda command, args: {},
    )
    server.start()
    try:
        base = server.discovery.base_url()  # type: ignore[union-attr]
        body = {"command": "x", "background": True}
        assert http_call(base, RPC_PATH, method="POST", token="t", payload=body)[0] == 501
        assert http_call(base, JOBS_PATH, token="t")[0] == 501
    finally:
        server.stop()


def test_jobs_announce_themselves_on_the_bus(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    seen: list[tuple[str, Any]] = []
    chassis.bus.subscribe("core.job.*", lambda event: seen.append((event.name, event.payload)))
    try:
        job = chassis.submit_job("heartbeat status")
        wait_for(lambda: len(seen) == 2)
        assert seen[0] == ("core.job.started", {"id": job["id"], "command": "heartbeat status"})
        assert seen[1][0] == "core.job.finished"
        assert seen[1][1]["ok"] is True
        assert seen[1][1]["exit_code"] == 0
    finally:
        chassis.shutdown()

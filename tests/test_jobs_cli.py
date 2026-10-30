"""The terminal side of jobs: kdx rpc --background/--follow, kdx jobs, kdx events, kdx stop."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.constants import ExitCode
from korodexios_core.kernel.chassis import Chassis
from korodexios_core.kernel.cli_contract import CliArgument, CliCommand, CliRequest
from korodexios_core.kernel.ipc import JOBS_PATH
from korodexios_core.kernel.users import Caller, acting_as
from korodexios_core.modules.backup_engine.engine import BackupSettings, build_plan, execute_plan

from .test_ipc import base_args, ipc_chassis
from .test_ipc_routes import http_call


def wait_for(predicate: Any, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")


class Daemon:
    """A chassis with its control plane up, plus a slow, cancellable 'probe wait'."""

    def __init__(self, project_root: Path) -> None:
        self.gate = threading.Event()
        self.seen: list[Any] = []
        self.chassis: Chassis = ipc_chassis(project_root)
        self.chassis.initialize()
        record = self.chassis.registry.get("heartbeat")
        assert record is not None
        record.commands.append(
            CliCommand(
                name="probe",
                subcommands=(
                    CliCommand(name="wait", handler=self._wait),
                    CliCommand(
                        name="echo",
                        handler=self._echo,
                        args=(CliArgument(("value",)), CliArgument(("--name",))),
                    ),
                ),
            )
        )
        self.chassis.start()
        self.args = base_args(project_root)

    def _wait(self, request: CliRequest) -> int:
        print("waiting", flush=True)
        while not request.cancelled() and not self.gate.is_set():
            time.sleep(0.01)
        print("stopped early" if request.cancelled() else "released")
        return 0

    def _echo(self, request: CliRequest) -> int:
        self.seen.append((request.option("value"), request.option("name")))
        print(f"value={request.option('value')} name={request.option('name')}")
        return 0

    def close(self) -> None:
        self.gate.set()
        self.chassis.shutdown()


@pytest.fixture()
def daemon(project_root):
    running = Daemon(project_root)
    yield running
    running.close()


def test_rpc_passes_paths_with_dots_slashes_and_spaces_whole(daemon, capsys) -> None:
    value = "C:/backups/v1.2/my folder"
    code = main([*daemon.args, "rpc", "probe", "echo", value, "--", "--name", "a.b/c"])
    assert code == 0
    assert daemon.seen == [(value, "a.b/c")]
    assert f"value={value}" in capsys.readouterr().out
    # The dotted first-word form still names the path.
    assert main([*daemon.args, "rpc", "probe.echo", "x"]) == 0
    assert daemon.seen[-1] == ("x", None)
    assert main([*daemon.args, "rpc", "probe", "nope"]) == int(ExitCode.ERROR)
    assert "has no subcommand 'nope'" in capsys.readouterr().out


def test_background_list_show_cancel(daemon, capsys) -> None:
    assert main([*daemon.args, "rpc", "--background", "probe", "wait"]) == 0
    out = capsys.readouterr().out
    assert "started job-1" in out
    assert "kdx jobs cancel job-1" in out
    wait_for(lambda: "waiting" in (daemon.chassis.jobs.get("job-1").as_dict()["output"]))  # type: ignore[union-attr]
    assert main([*daemon.args, "jobs", "list"]) == 0
    listing = capsys.readouterr().out
    assert "job-1" in listing and "probe wait" in listing and "running" in listing
    assert main([*daemon.args, "jobs", "show", "job-1"]) == 0
    assert "still running" in capsys.readouterr().out
    assert main([*daemon.args, "jobs", "cancel", "job-1"]) == 0
    assert "asked job-1 to stop" in capsys.readouterr().out
    assert main([*daemon.args, "jobs", "show", "job-1", "--follow"]) == int(ExitCode.ABORTED)
    followed = capsys.readouterr().out
    assert "stopped early" in followed
    assert "job-1 cancelled" in followed
    assert main([*daemon.args, "jobs", "cancel", "job-1"]) == 0
    assert "already cancelled" in capsys.readouterr().out
    assert main([*daemon.args, "jobs", "show", "job-9"]) == int(ExitCode.ERROR)
    assert "no job" in capsys.readouterr().out
    assert main([*daemon.args, "jobs", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"][0]["state"] == "cancelled"


def test_follow_prints_live_output_and_the_exit_code(daemon, capsys) -> None:
    daemon.gate.set()
    assert main([*daemon.args, "rpc", "--follow", "probe", "wait"]) == 0
    out = capsys.readouterr().out
    assert "waiting" in out and "released" in out and "done after" in out
    assert main([*daemon.args, "rpc", "--background", "--json", "probe", "wait"]) == 0
    assert json.loads(capsys.readouterr().out)["job"]["id"] == "job-2"
    assert main([*daemon.args, "rpc", "--background", "nope"]) == int(ExitCode.ERROR)


def test_only_the_owner_or_an_admin_may_cancel(daemon) -> None:
    users = daemon.chassis.users
    jana, oto = users.add("jana", "operator"), users.add("oto", "operator")
    base = daemon.chassis.ipc.discovery.base_url()
    code, started = http_call(base, "/api/v1/rpc", method="POST", token=jana,
                              payload={"command": "probe wait", "background": True})
    assert code == 202
    job_id = started["job"]["id"]
    assert started["job"]["command"] == "probe wait"
    cancel = {"cancel": job_id}
    assert http_call(base, JOBS_PATH, method="POST", token=oto, payload=cancel)[0] == 403
    code, job = http_call(base, JOBS_PATH, method="POST", token=jana, payload=cancel)
    assert code == 200
    assert job["state"] == "cancelling"
    assert http_call(base, JOBS_PATH, method="POST", token=jana,
                     payload={"cancel": "job-99"})[0] == 404
    viewer = users.add("vera", "viewer")
    assert http_call(base, JOBS_PATH, method="POST", token=viewer, payload=cancel)[0] == 403
    with acting_as(Caller("ada", "admin")):
        assert daemon.chassis.cancel_job(job_id) is not None


def test_events_and_stop_in_the_terminal(daemon, capsys) -> None:
    printed: dict[str, int] = {}

    def follow() -> None:
        printed["code"] = main([*daemon.args, "events", "demo.*", "-n", "1"])

    reader = threading.Thread(target=follow)
    reader.start()
    wait_for(lambda: any(sub.name == "web_console.feed"
                         for sub in daemon.chassis.bus._subscriptions))
    daemon.chassis.bus.publish("demo.hello", {"n": 1}, source="test")
    reader.join(timeout=10)
    assert printed == {"code": 0}
    out = capsys.readouterr().out
    assert "demo.hello" in out
    assert '{"n":1}' in out
    assert main([*daemon.args, "stop"]) == 0
    assert "is stopping" in capsys.readouterr().out
    wait_for(lambda: daemon.chassis.signals.shutdown_requested)


def test_terminal_clients_say_when_no_daemon_runs(project_root, capsys) -> None:
    args = base_args(project_root)
    for command in (["jobs", "list"], ["events"], ["stop"], ["jobs", "cancel", "job-1"]):
        assert main([*args, *command]) == int(ExitCode.ERROR)
        assert "no daemon is answering" in capsys.readouterr().out


def test_a_backup_stops_between_files(tmp_path: Path) -> None:
    source = tmp_path / "src"
    for index in range(20):
        (source / f"f{index}.txt").parent.mkdir(parents=True, exist_ok=True)
        (source / f"f{index}.txt").write_text("x", encoding="utf-8")
    plan = build_plan(BackupSettings(source=source, dest=tmp_path / "dst"))
    asked = {"n": 0}

    def stop_after_five() -> bool:
        asked["n"] += 1
        return asked["n"] > 5

    progress: list[int] = []
    result = execute_plan(plan, should_stop=stop_after_five,
                          progress=lambda running, total: progress.append(total))
    assert result.cancelled is True
    assert result.copied == 5
    assert result.as_dict()["cancelled"] is True

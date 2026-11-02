"""The authenticated caller as public API: rpc, background jobs, routes, package root."""

from __future__ import annotations

from typing import Any

import pytest

import korodexios_core
from korodexios_core import Caller, CliRequest, IpcRequest, IpcRoute, current_caller
from korodexios_core.kernel.cli_contract import CliCommand
from korodexios_core.kernel.ipc import RPC_PATH

from .test_ipc_routes import http_call
from .test_jobs_cli import Daemon, wait_for


@pytest.fixture()
def daemon(project_root):
    running = Daemon(project_root)
    seen: list[IpcRequest] = []
    running.requests = seen  # type: ignore[attr-defined]

    def who(request: CliRequest) -> int:
        caller = current_caller()
        assert request.caller == caller
        print(f"caller={caller.name if caller else None}")
        return 0

    record = running.chassis.registry.get("heartbeat")
    assert record is not None
    record.commands.append(CliCommand(name="whoami", handler=who))

    def handler(request: IpcRequest) -> dict[str, Any]:
        seen.append(request)
        assert request.caller == current_caller()
        return {"caller": request.caller.name if request.caller else None}

    running.chassis.register_ipc_route(
        IpcRoute(
            path="/api/v1/who",
            handler=handler,
            methods=("GET", "POST"),
            role="contributor",
            module="probe",
        )
    )
    yield running
    running.close()


def _base(daemon: Daemon) -> str:
    return daemon.chassis.ipc.discovery.base_url()  # type: ignore[union-attr]


def _shared_token(daemon: Daemon) -> str:
    return daemon.chassis.ipc.discovery_token  # type: ignore[union-attr]


def _run(daemon: Daemon, token: str, *, background: bool = False) -> Any:
    payload: dict[str, Any] = {"command": "whoami"}
    if background:
        payload["background"] = True
    code, body = http_call(_base(daemon), RPC_PATH, method="POST", token=token, payload=payload)
    return code, body


def test_synchronous_rpc_sees_the_named_user_and_the_owner(daemon) -> None:
    alice = daemon.chassis.users.add("alice", "operator")
    code, body = _run(daemon, alice)
    assert code == 200 and "caller=alice" in body["result"]["output"]  # type: ignore[index]
    code, body = _run(daemon, _shared_token(daemon))
    assert code == 200 and "caller=owner" in body["result"]["output"]  # type: ignore[index]


def test_background_jobs_keep_their_own_caller(daemon) -> None:
    alice = daemon.chassis.users.add("alice", "operator")
    bob = daemon.chassis.users.add("bob", "operator")
    ids = []
    for token in (alice, bob, alice):
        code, body = _run(daemon, token, background=True)
        assert code == 202
        ids.append(body["job"]["id"])  # type: ignore[index]
    for job_id in ids:
        wait_for(lambda job_id=job_id: daemon.chassis.jobs.get(job_id).state  # type: ignore[union-attr]
                 not in ("running", "cancelling"))
    names = [daemon.chassis.jobs.get(i).as_dict() for i in ids]  # type: ignore[union-attr]
    assert [("caller=" + job["user"]) in job["output"] for job in names] == [True] * 3
    assert [job["user"] for job in names] == ["alice", "bob", "alice"]


def test_route_handler_sees_the_caller_and_a_viewer_never_reaches_it(daemon) -> None:
    contributor = daemon.chassis.users.add("carol", "contributor")
    viewer = daemon.chassis.users.add("vera", "viewer")
    base = _base(daemon)
    code, body = http_call(base, "/api/v1/who", method="POST", token=contributor, payload={})
    assert code == 200 and body == {"caller": "carol"}
    seen = daemon.requests  # type: ignore[attr-defined]
    assert len(seen) == 1 and seen[0].caller == Caller("carol", "contributor", "user")
    assert http_call(base, "/api/v1/who", method="POST", token=viewer, payload={})[0] == 403
    assert len(seen) == 1


def test_a_local_invocation_has_no_caller() -> None:
    request = CliRequest(context=None, command="x")  # type: ignore[arg-type]
    assert request.caller is None
    assert IpcRequest(method="GET", path="/", query={}).caller is None


def test_the_package_root_exports_the_caller_api() -> None:
    for name in ("Caller", "current_caller", "acting_as"):
        assert name in korodexios_core.__all__
    assert korodexios_core.current_caller() is None
    with korodexios_core.acting_as(Caller("ada", "admin")):
        assert korodexios_core.current_caller() == Caller("ada", "admin")

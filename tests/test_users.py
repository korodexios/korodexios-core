"""Named users, roles, and the audit log - in the store, over the control plane, in the CLI."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.constants import ExitCode
from korodexios_core.kernel.audit import AuditLog, read_audit
from korodexios_core.kernel.chassis import Chassis
from korodexios_core.kernel.cli_contract import CliCommand
from korodexios_core.kernel.ipc import RELOAD_PATH, RPC_PATH, SHUTDOWN_PATH, STATUS_PATH
from korodexios_core.kernel.users import (
    OWNER_NAME,
    Caller,
    UserError,
    UserStore,
    acting_as,
    current_caller,
    hash_token,
    role_allows,
)

from .test_ipc import base_args, ipc_chassis
from .test_ipc_routes import http_call


def test_roles_are_ordered() -> None:
    assert role_allows("admin", "viewer")
    assert role_allows("operator", "operator")
    assert not role_allows("viewer", "operator")
    assert not role_allows("nobody", "viewer")
    assert not role_allows("admin", "superuser")
    assert Caller("a", "operator").allows("viewer")


def test_store_keeps_only_hashes_and_refreshes_from_disk(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    assert store.users() == []
    token = store.add("vera", "viewer")
    raw = (tmp_path / "users.json").read_text(encoding="utf-8")
    assert token not in raw
    assert hash_token(token) in raw
    assert store.authenticate(token).name == "vera"  # type: ignore[union-attr]
    assert store.authenticate("wrong") is None
    assert store.authenticate("") is None
    # A second store (another process, 'kdx users add' in a terminal) sees the change.
    other = UserStore(tmp_path / "users.json")
    other.add("oto", "operator")
    time.sleep(0.01)
    assert [user.name for user in store.users()] == ["oto", "vera"]


def test_store_rotate_role_and_remove(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    first = store.add("jana", "operator")
    second = store.rotate("jana")
    assert first != second
    assert store.authenticate(first) is None
    assert store.authenticate(second).role == "operator"  # type: ignore[union-attr]
    assert store.set_role("jana", "admin").role == "admin"
    store.remove("jana")
    assert store.authenticate(second) is None
    for action in (lambda: store.remove("jana"), lambda: store.rotate("x"),
                   lambda: store.set_role("x", "viewer")):
        with pytest.raises(UserError, match="no user"):
            action()


@pytest.mark.parametrize("name", ["", "Jana", "-x", "a" * 33, "with space", OWNER_NAME])
def test_bad_names_are_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(UserError):
        UserStore(tmp_path / "users.json").add(name, "viewer")


def test_bad_roles_and_duplicates_are_refused(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    with pytest.raises(UserError, match="not one of"):
        store.add("jana", "root")
    store.add("jana", "viewer")
    with pytest.raises(UserError, match="already exists"):
        store.add("jana", "viewer")


def test_a_damaged_file_is_reported_and_bad_rows_skipped(tmp_path: Path) -> None:
    path = tmp_path / "users.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(UserError, match="cannot read"):
        UserStore(path).users()
    path.write_text(
        json.dumps({"users": [{"name": "ok", "role": "viewer", "token_sha256": "a"},
                              {"name": "BAD", "role": "viewer", "token_sha256": "b"},
                              {"name": "norole"}]}),
        encoding="utf-8",
    )
    assert [user.name for user in UserStore(path).users()] == ["ok"]


def test_acting_as_sets_and_restores_the_caller() -> None:
    assert current_caller() is None
    with acting_as(Caller("vera", "viewer")):
        assert current_caller() == Caller("vera", "viewer")
    assert current_caller() is None


def test_a_command_role_must_be_known() -> None:
    with pytest.raises(ValueError, match="role"):
        CliCommand(name="x", handler=lambda request: 0, role="root")


def test_audit_log_appends_rotates_and_filters(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl", max_bytes=1024)
    for index in range(30):
        log.record("rpc", caller=Caller("oto", "operator"), outcome="exit 0",
                   command=f"cmd {index}", address="10.0.0.2")
    log.record("reload", caller=None, outcome="unauthenticated")
    assert (tmp_path / "audit.jsonl.1").exists()  # rotated, one generation kept
    newest = read_audit(tmp_path / "audit.jsonl", limit=5)
    assert len(newest) == 5
    assert newest[-1]["action"] == "reload"
    assert newest[-1]["user"] is None
    only_oto = read_audit(tmp_path / "audit.jsonl", limit=500, user="oto")
    assert only_oto and all(entry["user"] == "oto" for entry in only_oto)
    assert read_audit(tmp_path / "audit.jsonl", action="reload")[0]["outcome"] == "unauthenticated"
    assert read_audit(tmp_path / "missing.jsonl") == []
    assert log.as_dict()["records"] == 31


def test_audit_failures_are_counted_not_raised(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    log = AuditLog(blocker / "audit.jsonl")  # a file where a directory should be
    log.record("rpc", caller=None, outcome="x")
    assert log.failures == 1


def started(project_root: Path) -> tuple[Chassis, str, dict[str, str]]:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    assert chassis.users is not None
    tokens = {
        role: chassis.users.add(f"{role}-user", role) for role in ("viewer", "operator", "admin")
    }
    chassis.start()
    return chassis, chassis.ipc.discovery.base_url(), tokens  # type: ignore[union-attr]


def rpc(base: str, token: str, command: str, **extra: Any) -> tuple[int, Any]:
    return http_call(base, RPC_PATH, method="POST", token=token,
                     payload={"command": command, **extra})


def test_roles_over_the_control_plane(project_root) -> None:
    chassis, base, tokens = started(project_root)
    viewer, operator, admin = tokens["viewer"], tokens["operator"], tokens["admin"]
    try:
        assert http_call(base, STATUS_PATH, token=viewer)[0] == 200
        assert rpc(base, viewer, "heartbeat status")[0] == 403
        assert rpc(base, operator, "heartbeat status")[0] == 200
        code, refused = rpc(base, operator, "backup run", args={"argv": ["--dry-run"]})
        assert code == 403
        assert "needs the admin role" in refused["error"]
        assert rpc(base, admin, "backup plan")[0] == 200
        assert http_call(base, RELOAD_PATH, method="POST", token=operator)[0] == 403
        assert http_call(base, RELOAD_PATH, method="POST", token=admin)[0] == 200
        assert http_call(base, "/api/v1/whoami", token=operator)[1]["role"] == "operator"
        assert http_call(base, "/api/v1/whoami")[1]["source"] == "anonymous"
        # A job remembers who started it.
        code, started_job = rpc(base, operator, "heartbeat status", background=True)
        assert code == 202
        assert started_job["job"]["user"] == "operator-user"
        assert http_call(base, SHUTDOWN_PATH, method="POST", token=operator)[0] == 403
        assert http_call(base, RPC_PATH, method="POST", token="bogus", payload={})[0] == 401
    finally:
        chassis.shutdown()
    entries = read_audit(chassis.audit.path, limit=100)  # type: ignore[union-attr]
    outcomes = {(entry["user"], entry["action"], entry["outcome"]) for entry in entries}
    assert ("viewer-user", "rpc", "denied: needs operator") in outcomes
    assert ("operator-user", "rpc", "exit 0") in outcomes
    assert ("admin-user", "reload", "ok") in outcomes
    assert (None, "rpc", "unauthenticated") in outcomes
    denied = [entry for entry in entries if entry["user"] == "viewer-user"]
    assert denied[0]["command"] == "heartbeat status"
    assert any(entry["action"] == "job" and entry["outcome"].startswith("finished")
               for entry in entries)


def test_a_command_can_ask_for_a_stronger_role(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    record = chassis.registry.get("heartbeat")
    assert record is not None
    record.commands.append(
        CliCommand(name="vault", role="admin",
                   subcommands=(CliCommand(name="open", handler=lambda request: 0),))
    )
    try:
        with acting_as(Caller("oto", "operator")), pytest.raises(PermissionError, match="admin"):
            chassis.dispatch_rpc("vault open")
        with acting_as(Caller("ada", "admin")):
            assert chassis.dispatch_rpc("vault open")["exit_code"] == 0
        assert chassis.dispatch_rpc("vault open")["exit_code"] == 0  # in-process: no caller
    finally:
        chassis.shutdown()


def test_web_console_audit_route_is_admin_only(project_root) -> None:
    chassis, base, tokens = started(project_root)
    try:
        assert http_call(base, "/api/v1/ui/audit", token=tokens["operator"])[0] == 403
        code, payload = http_call(base, "/api/v1/ui/audit", token=tokens["admin"])
        assert code == 200
        assert isinstance(payload["entries"], list)
        catalog = http_call(base, "/api/v1/ui/catalog", token=tokens["viewer"])[1]
        roles = {item["path"]: item["role"] for item in catalog["commands"]}
        assert roles["backup run"] == "admin"
        assert roles["heartbeat status"] == "operator"
    finally:
        chassis.shutdown()


def test_users_and_audit_in_the_terminal(project_root, capsys) -> None:
    args = base_args(project_root)
    assert main([*args, "users", "list"]) == 0
    assert "no named users yet" in capsys.readouterr().out
    assert main([*args, "users", "add", "jana", "--role", "viewer", "--json"]) == 0
    token = json.loads(capsys.readouterr().out)["token"]
    assert len(token) > 30
    assert main([*args, "users", "add", "jana"]) == int(ExitCode.ERROR)
    assert "already exists" in capsys.readouterr().out
    assert main([*args, "users", "role", "jana", "operator"]) == 0
    assert "jana is now operator" in capsys.readouterr().out
    assert main([*args, "users", "rotate", "jana"]) == 0
    rotated = capsys.readouterr().out
    assert "shown only now" in rotated
    assert token not in rotated
    assert main([*args, "users", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)["users"]
    assert listed[0]["name"] == "jana"
    assert "token" not in json.dumps(listed)
    assert main([*args, "users", "remove", "jana"]) == 0
    assert main([*args, "users", "remove", "jana"]) == int(ExitCode.ERROR)
    capsys.readouterr()
    assert main([*args, "audit"]) == 0
    assert "no audit records" in capsys.readouterr().out
    AuditLog(Path(project_root) / "data" / "audit.jsonl").record(
        "rpc", caller=Caller("jana", "operator"), outcome="exit 0", command="system info"
    )
    assert main([*args, "audit", "--user", "jana"]) == 0
    assert "system info" in capsys.readouterr().out
    assert main([*args, "audit", "--json", "--action", "reload"]) == 0
    assert json.loads(capsys.readouterr().out)["entries"] == []


@pytest.mark.skipif(os.name != "nt", reason="the ACL check is Windows-only")
def test_the_user_file_is_owner_only_on_windows(tmp_path: Path) -> None:
    import subprocess

    UserStore(tmp_path / "users.json").add("jana", "viewer")
    listing = subprocess.run(
        ["icacls", str(tmp_path / "users.json")], capture_output=True, text=True, check=True
    ).stdout
    assert "(I)" not in listing

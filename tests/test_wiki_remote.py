"""`wiki ingest` over the control plane stays inside the project (review 2026-11-08, B1)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.kernel.chassis import Chassis, ChassisOptions
from korodexios_core.kernel.users import UserStore
from korodexios_core.paths import AppPaths


@pytest.fixture
def daemon(tmp_path: Path) -> Iterator[tuple[Chassis, str, Path]]:
    root = tmp_path / "project"
    root.mkdir()
    (root / "kdx.yml").write_text("core:\n  name: wiki-remote\n", encoding="utf-8")
    paths = AppPaths.discover(root=str(root), environ={})
    chassis = Chassis.create(
        ChassisOptions(
            home=paths.root,
            environ={},
            cli_overrides={
                "core.log.file_enabled": False,
                "core.ipc.enabled": True,
                "core.ipc.port": 0,
            },
            install_signals=False,
        )
    )
    chassis.start()
    for _ in range(100):
        if chassis.ipc is not None and chassis.ipc.discovery is not None:
            break
        time.sleep(0.05)
    token = UserStore(chassis.paths.durable_dir / "users.json").add("bot", "operator")
    try:
        yield chassis, token, root
    finally:
        chassis.shutdown()


def rpc(chassis: Chassis, token: str, *argv: str) -> int:
    discovery = chassis.ipc.discovery  # type: ignore[union-attr]
    body = json.dumps({"command": "wiki", "args": {"argv": list(argv)}}).encode("utf-8")
    request = urllib.request.Request(
        discovery.base_url() + "/api/v1/rpc",
        data=body,
        method="POST",
        headers={"X-Kdx-Token": token, "Content-Type": "application/json"},
    )
    payload: dict[str, Any]
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    return int(payload["result"]["exit_code"])


def test_operator_cannot_copy_runtime_state_into_the_wiki(
    daemon: tuple[Chassis, str, Path],
) -> None:
    chassis, token, root = daemon
    assert rpc(chassis, token, "init") == 0
    ipc_file = chassis.paths.state_dir / "chassis.ipc"
    assert ipc_file.is_file()
    assert rpc(chassis, token, "ingest", str(ipc_file), "--slug", "leak") != 0
    assert not list((root / "docs" / "wiki" / "raw").glob("leak*"))


@pytest.mark.parametrize(
    "name", ["kdx.local.yml", ".env", "data/users.json", "data/audit.jsonl", ".ssh/id_rsa"]
)
def test_operator_cannot_ingest_local_secrets(
    daemon: tuple[Chassis, str, Path], name: str
) -> None:
    chassis, token, root = daemon
    assert rpc(chassis, token, "init") == 0
    secret = root / name
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("secret", encoding="utf-8")
    assert rpc(chassis, token, "ingest", name, "--slug", "leak") != 0
    assert rpc(chassis, token, "ingest", str(secret), "--slug", "leak") != 0
    assert not list((root / "docs" / "wiki" / "raw").glob("leak*"))


def test_operator_cannot_leave_the_project(
    daemon: tuple[Chassis, str, Path], tmp_path: Path
) -> None:
    chassis, token, root = daemon
    assert rpc(chassis, token, "init") == 0
    outside = tmp_path / "outside.md"
    outside.write_text("# outside", encoding="utf-8")
    assert rpc(chassis, token, "ingest", str(outside), "--slug", "out") != 0
    assert rpc(chassis, token, "ingest", "../outside.md", "--slug", "out") != 0
    assert not list((root / "docs" / "wiki" / "raw").glob("out*"))


def test_operator_can_ingest_a_project_file(daemon: tuple[Chassis, str, Path]) -> None:
    chassis, token, root = daemon
    assert rpc(chassis, token, "init") == 0
    (root / "notes.md").write_text("# Notes", encoding="utf-8")
    assert rpc(chassis, token, "ingest", "notes.md", "--slug", "notes") == 0
    copied = root / "docs" / "wiki" / "raw" / "notes.md"
    assert copied.read_text(encoding="utf-8") == "# Notes"

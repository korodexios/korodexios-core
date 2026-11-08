"""The `fields` /api/v1/schema declares are the keys the route returns (review 2026-11-08, K3)."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import PROJECT_CONFIG
from tests.test_ipc import ipc_chassis


def test_declared_fields_match_the_live_answers(tmp_path: Path) -> None:
    (tmp_path / "kdx.yml").write_text(PROJECT_CONFIG, encoding="utf-8")
    chassis = ipc_chassis(tmp_path)
    chassis.initialize()
    chassis.start()
    checked: list[str] = []
    try:
        discovery = chassis.ipc.discovery  # type: ignore[union-attr]
        _, schema = discovery.request("/api/v1/schema")
        for route in schema["routes"]:
            declared = route.get("fields")
            # Shutdown stops the daemon under the test; rpc needs a command to run.
            if not declared or route["path"] in ("/api/v1/shutdown", "/api/v1/rpc"):
                continue
            code, payload = discovery.request(route["path"], method=route["method"])
            assert code == 200, route["path"]
            assert set(declared) == set(payload), route["path"]
            checked.append(route["path"])
    finally:
        chassis.shutdown()
    assert {"/health", "/api/v1/schema", "/api/v1/status", "/api/v1/jobs",
            "/api/v1/whoami", "/api/v1/reload"} <= set(checked)

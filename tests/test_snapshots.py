"""Public shapes are pinned: the keys and value types of `--json` and core routes, exit codes.

A snapshot is a tree of keys and types, never values, so it fails when a key appears, vanishes or
changes type and stays quiet when a number moves. A change to a public shape is deliberate: refresh
the files with ``KDX_UPDATE_SNAPSHOTS=1 pytest tests/test_snapshots.py``, review the diff, and add a
line to CHANGELOG.md (review 2026-11-08, K4).
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any

import pytest

import korodexios_core
from korodexios_core.cli.entry import main
from korodexios_core.constants import ExitCode
from tests.conftest import PROJECT_CONFIG
from tests.test_ipc import ipc_chassis

SNAPSHOTS = Path(__file__).parent / "snapshots"
UPDATE = os.environ.get("KDX_UPDATE_SNAPSHOTS") == "1"

CLI_SNAPSHOTS = {
    "cli-doctor": ("doctor", "--json"),
    "cli-status": ("status", "--json"),
    "cli-modules-list": ("modules", "list", "--json"),
    "cli-config-check": ("config", "check", "--json"),
    "cli-docs-check": ("docs", "check", "--json"),
    "cli-locale-show": ("locale", "show", "--json"),
}
ROUTE_SNAPSHOTS = {
    "route-health": "/health",
    "route-schema": "/api/v1/schema",
    "route-status": "/api/v1/status",
    "route-whoami": "/api/v1/whoami",
    "route-jobs": "/api/v1/jobs",
}


# Reports whose inner sections differ per platform (system, container, git): only their top-level
# keys are pinned, which is what a client of the report relies on.
TOP_LEVEL_ONLY = {"cli-doctor", "route-status"}


def shape(value: Any, depth: int = 99) -> Any:
    """Return the tree of keys and value types of *value*; lists merge their items."""
    if isinstance(value, dict):
        if depth == 0:
            return "object"
        return {key: shape(value[key], depth - 1) for key in sorted(value)}
    if isinstance(value, list):
        merged: dict[str, Any] = {}
        for item in value:
            inner = shape(item, depth - 1)
            if isinstance(inner, dict):
                merged.update({k: v for k, v in inner.items() if k not in merged or v})
        return [merged] if merged else "list"  # a list of plain values or an empty one
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    return "null" if value is None else "str"


def check(name: str, actual: Any) -> None:
    path = SNAPSHOTS / f"{name}.json"
    text = json.dumps(actual, indent=2, sort_keys=True) + "\n"
    if UPDATE:
        path.write_text(text, encoding="utf-8", newline="\n")
    assert path.is_file(), f"{path.name} is missing; run with KDX_UPDATE_SNAPSHOTS=1"
    assert json.loads(path.read_text(encoding="utf-8")) == actual, (
        f"{path.name} differs: a public shape changed; refresh the snapshot, review it and "
        "note it in CHANGELOG.md"
    )


@pytest.mark.parametrize("name", sorted(CLI_SNAPSHOTS))
def test_cli_json_shapes(
    name: str, project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--home", str(project_root), "--no-log-file", "--no-color", *CLI_SNAPSHOTS[name]])
    depth = 1 if name in TOP_LEVEL_ONLY else 99
    check(name, shape(json.loads(capsys.readouterr().out), depth))


def test_core_route_shapes(tmp_path: Path) -> None:
    (tmp_path / "kdx.yml").write_text(PROJECT_CONFIG, encoding="utf-8")
    chassis = ipc_chassis(tmp_path)
    chassis.initialize()
    chassis.start()
    try:
        discovery = chassis.ipc.discovery  # type: ignore[union-attr]
        answers = {
            name: discovery.request(route)[1] for name, route in ROUTE_SNAPSHOTS.items()
        }
    finally:
        chassis.shutdown()
    for name, payload in answers.items():
        check(name, shape(payload, 1 if name in TOP_LEVEL_ONLY else 99))


def test_exit_codes_are_pinned() -> None:
    check("exit-codes", {item.name: int(item) for item in ExitCode})


def test_frozen_functions_keep_their_signatures() -> None:
    functions = {
        name: str(inspect.signature(getattr(korodexios_core, name)))
        for name in sorted(korodexios_core.__all__)
        if inspect.isfunction(getattr(korodexios_core, name))
    }
    assert functions, "the package root exports no function"
    check("api-signatures", functions)

"""'kdx reset': lists first, deletes only the disposable side, refuses anything durable."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.constants import ExitCode
from korodexios_core.kernel.ipc import DISCOVERY_NAME, IpcDiscovery
from korodexios_core.kernel.reset import ResetEntry, ResetError, apply_reset, plan_reset

from .test_ipc import base_args


def populate(root: Path) -> None:
    lib = root / "var" / "lib"
    (lib / "cache" / "deep").mkdir(parents=True)
    (lib / ".gitkeep").write_text("", encoding="utf-8")
    (lib / "index.db").write_bytes(b"x" * 10)
    (lib / "cache" / "a.tmp").write_bytes(b"yy")
    (lib / "cache" / "deep" / "b.tmp").write_bytes(b"zzz")
    (root / "data").mkdir(exist_ok=True)
    (root / "data" / "ledger.jsonl").write_text("keep me\n", encoding="utf-8")


def test_plan_lists_without_touching(tmp_path: Path) -> None:
    populate(tmp_path)
    plan = plan_reset(tmp_path, ["var/lib"], ["data"])
    paths = {entry.path for entry in plan.entries}
    assert {"var/lib/index.db", "var/lib/cache/a.tmp", "var/lib/cache/deep/b.tmp"} <= paths
    assert {"var/lib/cache", "var/lib/cache/deep"} <= paths
    assert "var/lib/.gitkeep" not in paths
    assert plan.kept == ("var/lib/.gitkeep",)
    assert plan.total_bytes == 15
    assert (tmp_path / "var" / "lib" / "index.db").exists()
    assert plan.as_dict()["total_bytes"] == 15


def test_apply_removes_disposable_and_keeps_markers_and_durable(tmp_path: Path) -> None:
    populate(tmp_path)
    plan = plan_reset(tmp_path, ["var/lib"], ["data"])
    result = apply_reset(tmp_path, plan)
    assert result.failures == []
    assert result.removed == len(plan.entries)
    assert result.freed == 15
    assert sorted(item.name for item in (tmp_path / "var" / "lib").iterdir()) == [".gitkeep"]
    assert (tmp_path / "data" / "ledger.jsonl").read_text(encoding="utf-8") == "keep me\n"
    assert plan_reset(tmp_path, ["var/lib"], ["data"]).empty
    assert result.as_dict()["removed"] == len(plan.entries)


def test_directory_holding_a_marker_survives(tmp_path: Path) -> None:
    layer = tmp_path / "var" / "lib" / "layer"
    layer.mkdir(parents=True)
    (layer / ".gitkeep").write_text("", encoding="utf-8")
    (layer / "x.bin").write_bytes(b"1")
    apply_reset(tmp_path, plan_reset(tmp_path, ["var/lib"], []))
    assert [item.name for item in layer.iterdir()] == [".gitkeep"]


@pytest.mark.parametrize(
    ("disposable", "message"),
    [
        (["."], "inside the project"),
        (["../elsewhere"], "inside the project"),
        (["data"], "durable"),
        (["data/sub"], "durable"),
        (["var"], "needs"),
    ],
)
def test_unsafe_roots_are_refused(tmp_path: Path, disposable: list[str], message: str) -> None:
    populate(tmp_path)
    (tmp_path / "data" / "sub").mkdir()
    protected = ["var/lib/cache"] if message == "needs" else []
    with pytest.raises(ResetError, match=message):
        plan_reset(tmp_path, disposable, ["data"], protected=protected)


def test_a_durable_root_inside_the_disposable_one_is_refused(tmp_path: Path) -> None:
    populate(tmp_path)
    with pytest.raises(ResetError, match="durable"):
        plan_reset(tmp_path, ["var"], ["var/lib/cache"])


@pytest.mark.parametrize("bad", ["", "a/b", "..", "a\\b"])
def test_bad_keep_names_are_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ResetError, match="keep entry"):
        plan_reset(tmp_path, ["var/lib"], [], keep=[bad])


def test_missing_root_is_reported_absent(tmp_path: Path) -> None:
    plan = plan_reset(tmp_path, ["var/lib"], [])
    assert plan.absent == ("var/lib",)
    assert plan.empty


def test_file_symlink_is_removed_as_a_link(tmp_path: Path) -> None:
    populate(tmp_path)
    target = tmp_path / "data" / "ledger.jsonl"
    link = tmp_path / "var" / "lib" / "ledger-link"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    apply_reset(tmp_path, plan_reset(tmp_path, ["var/lib"], ["data"]))
    assert not link.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep me\n"


def make_directory_link(link: Path, target: Path) -> None:
    """Create a directory symlink, or a junction on Windows without symlink rights."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("symlinks are not available here")
        done = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True
        )
        if done.returncode != 0:
            pytest.skip("neither symlinks nor junctions are available here")


def test_directory_symlink_target_is_never_followed(tmp_path: Path) -> None:
    populate(tmp_path)
    outside = tmp_path / "data" / "dir"
    outside.mkdir()
    (outside / "precious.txt").write_text("p", encoding="utf-8")
    link = tmp_path / "var" / "lib" / "dirlink"
    make_directory_link(link, outside)
    plan = plan_reset(tmp_path, ["var/lib"], ["data"])
    assert any(entry.kind == "link" and entry.path.endswith("dirlink") for entry in plan.entries)
    apply_reset(tmp_path, plan)
    assert (outside / "precious.txt").exists()
    assert not link.is_symlink()


def test_apply_refuses_a_plan_that_leaves_its_roots(tmp_path: Path) -> None:
    populate(tmp_path)
    plan = plan_reset(tmp_path, ["var/lib"], ["data"])
    forged = replace(plan, entries=(ResetEntry("data/ledger.jsonl", "file", 8),))
    result = apply_reset(tmp_path, forged)
    assert result.removed == 0
    assert "outside the planned roots" in result.failures[0]
    assert (tmp_path / "data" / "ledger.jsonl").exists()


def test_read_only_file_is_removed(tmp_path: Path) -> None:
    populate(tmp_path)
    locked = tmp_path / "var" / "lib" / "locked.db"
    locked.write_bytes(b"1")
    locked.chmod(0o444)
    result = apply_reset(tmp_path, plan_reset(tmp_path, ["var/lib"], ["data"]))
    assert result.failures == []
    assert not locked.exists()


def test_cli_lists_by_default(project_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    populate(project_root)
    assert main([*base_args(project_root), "reset"]) == int(ExitCode.SUCCESS)
    out = capsys.readouterr().out
    assert "index.db" in out
    assert "kdx reset --apply" in out
    assert (project_root / "var" / "lib" / "index.db").exists()


def test_cli_json_plan(project_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    populate(project_root)
    assert main([*base_args(project_root), "reset", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False
    assert payload["plan"]["total_bytes"] == 15


def test_cli_apply_deletes_disposable_only(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    populate(project_root)
    assert main([*base_args(project_root), "reset", "--apply"]) == int(ExitCode.SUCCESS)
    assert "removed" in capsys.readouterr().out
    assert not (project_root / "var" / "lib" / "index.db").exists()
    assert (project_root / "data" / "ledger.jsonl").exists()
    assert (project_root / "var" / "lib" / ".gitkeep").exists()


def test_cli_apply_json_with_and_without_work(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*base_args(project_root), "reset", "--apply", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is True
    populate(project_root)
    assert main([*base_args(project_root), "reset", "--apply", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["removed"] > 0


def test_cli_refuses_while_the_daemon_runs(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    populate(project_root)
    state = project_root / "var"
    state.mkdir(exist_ok=True)
    discovery = IpcDiscovery(pid=os.getpid(), host="127.0.0.1", port=1, token="", started_at=0.0)
    (state / DISCOVERY_NAME).write_text(json.dumps(discovery.as_dict()), encoding="utf-8")
    assert main([*base_args(project_root), "reset", "--apply"]) == int(ExitCode.ERROR)
    assert "daemon is running" in capsys.readouterr().out
    assert (project_root / "var" / "lib" / "index.db").exists()


def test_cli_refuses_an_unsafe_configured_root(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    populate(project_root)
    (project_root / "kdx.local.yml").write_text(
        "core:\n  reset:\n    disposable: [data]\n", encoding="utf-8"
    )
    assert main([*base_args(project_root), "reset", "--apply"]) == int(ExitCode.CONFIG_ERROR)
    assert "durable" in capsys.readouterr().out
    assert (project_root / "data" / "ledger.jsonl").exists()


def test_cli_honours_configured_roots_and_extra_durable(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    populate(project_root)
    (project_root / "scratch").mkdir()
    (project_root / "scratch" / "s.bin").write_bytes(b"1")
    (project_root / "kdx.local.yml").write_text(
        "core:\n  reset:\n    disposable: [scratch]\n    durable: [var/lib]\n", encoding="utf-8"
    )
    assert main([*base_args(project_root), "reset", "--apply"]) == 0
    assert not (project_root / "scratch" / "s.bin").exists()
    assert (project_root / "var" / "lib" / "index.db").exists()
    assert main([*base_args(project_root), "reset"]) == 0
    capsys.readouterr()

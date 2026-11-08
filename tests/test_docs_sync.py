"""'kdx docs' and '--check': seeded documents are measured against the installed core."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from korodexios_core.cli.conventions import conventions_text, spec_template
from korodexios_core.cli.entry import main
from korodexios_core.cli.init_project import build_files, project_from_inputs
from korodexios_core.cli.playbook import agent_playbook
from korodexios_core.constants import ExitCode

from .test_cli_v2 import base_args, write_root

EMPTY = "FUNCTIONS: dict[str, int] = {}"


def run(root: Path, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main([*base_args(root), *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def seed(root: Path) -> None:
    write_root(root)
    for relative, text in (
        ("docs/CONVENTIONS.md", conventions_text()),
        ("AGENT_PLAYBOOK.md", agent_playbook()),
        ("docs/specs/TEMPLATE.md", spec_template()),
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def test_current_documents_pass_the_check(capsys, project_root: Path) -> None:
    seed(project_root)
    code, out, _ = run(project_root, capsys, "docs", "check")
    assert code == 0
    assert out.count("[✓]") == 3
    for flag in ("conventions", "playbook"):
        assert run(project_root, capsys, flag, "--check")[0] == 0


def test_crlf_checkout_is_not_drift(capsys, project_root: Path) -> None:
    seed(project_root)
    path = project_root / "docs" / "CONVENTIONS.md"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert run(project_root, capsys, "docs", "check")[0] == 0


def test_a_stale_document_exits_one_and_prints_the_diff(capsys, project_root: Path) -> None:
    seed(project_root)
    path = project_root / "docs" / "CONVENTIONS.md"
    path.write_text("# Project conventions\n\nold text\n", encoding="utf-8")
    code, out, _ = run(project_root, capsys, "conventions", "--check")
    assert code == int(ExitCode.ERROR) == 1
    assert "(project)" in out and "(core)" in out and "-old text" in out
    code, out, _ = run(project_root, capsys, "docs", "check")
    assert code == 1
    assert "kdx docs update" in out


def test_a_missing_document_is_reported_as_json(capsys, project_root: Path) -> None:
    seed(project_root)
    (project_root / "AGENT_PLAYBOOK.md").unlink()
    code, out, _ = run(project_root, capsys, "docs", "check", "--json")
    assert code == 1
    states = {row["document"]: row["state"] for row in json.loads(out)["documents"]}
    assert states == {
        "conventions": "current",
        "playbook": "missing",
        "spec-template": "current",
    }
    assert run(project_root, capsys, "playbook", "--check")[0] == 1


def test_update_rewrites_only_what_differs(capsys, project_root: Path) -> None:
    seed(project_root)
    (project_root / "AGENT_PLAYBOOK.md").unlink()
    (project_root / "docs" / "specs" / "TEMPLATE.md").write_text("old\n", encoding="utf-8")
    code, out, _ = run(project_root, capsys, "docs", "update")
    assert code == 0
    assert out.count("written") == 2
    assert (project_root / "AGENT_PLAYBOOK.md").read_text(encoding="utf-8") == agent_playbook()
    assert run(project_root, capsys, "docs", "check")[0] == 0
    code, out, _ = run(project_root, capsys, "docs", "update", "--json")
    assert code == 0
    assert {row["state"] for row in json.loads(out)["documents"]} == {"current"}


def test_update_reports_an_unwritable_target(capsys, project_root: Path) -> None:
    seed(project_root)
    (project_root / "docs" / "CONVENTIONS.md").unlink()
    (project_root / "docs" / "CONVENTIONS.md").mkdir()
    code, _, err = run(project_root, capsys, "docs", "update")
    assert code == int(ExitCode.ERROR)
    assert "[!]" in err


def test_init_seeds_the_docs_check_and_the_size_ratchet() -> None:
    files = build_files(project_from_inputs("ratchet_check"))  # type: ignore[arg-type]
    assert "check-docs:" in files["Makefile"]
    assert "check: lint typecheck test check-docs" in files["Makefile"]
    assert "kdx docs check" in files[".github/workflows/ci.yml"]
    ratchet = files["tests/test_size_ratchet.py"]
    assert 'src" / "ratchet_check"' in ratchet
    assert "FILES: dict[str, int] = {}" in ratchet
    assert "__PACKAGE__" not in ratchet


def test_the_seeded_size_ratchet_runs_and_catches_growth(tmp_path: Path) -> None:
    """Start at zero is green; one oversized function is a recorded decision or a failure."""
    project = project_from_inputs("ratchet_run")  # type: ignore[arg-type]
    files = build_files(project)  # type: ignore[arg-type]
    for name in ("tests/test_size_ratchet.py", "src/ratchet_run/__init__.py"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(files[name], encoding="utf-8")
    long_function = "def big() -> None:\n" + "    pass\n" * 45
    (tmp_path / "src" / "ratchet_run" / "big.py").write_text(long_function, encoding="utf-8")

    def pytest_run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             str(tmp_path / "tests" / "test_size_ratchet.py")],
            cwd=tmp_path, capture_output=True, text=True, check=False,
        )

    failed = pytest_run()
    assert failed.returncode != 0
    assert "big.py::big: 46 is over the trigger and not recorded" in failed.stdout

    ratchet = tmp_path / "tests" / "test_size_ratchet.py"
    text = ratchet.read_text(encoding="utf-8")
    ratchet.write_text(
        text.replace(EMPTY, 'FUNCTIONS: dict[str, int] = {"big.py::big": 50}'),
        encoding="utf-8",
    )
    stale = pytest_run()
    assert stale.returncode != 0 and "recorded 50, now 46" in stale.stdout

    ratchet.write_text(
        text.replace(EMPTY, 'FUNCTIONS: dict[str, int] = {"big.py::big": 46}'),
        encoding="utf-8",
    )
    assert pytest_run().returncode == 0


def test_a_broken_config_is_still_exit_three_not_drift(capsys, project_root: Path) -> None:
    """Review 2026-11-08, K1: CI can tell a drifted document (1) from an unloadable config (3)."""
    seed(project_root)
    (project_root / "kdx.yml").write_text("core: [unclosed\n", encoding="utf-8")
    assert run(project_root, capsys, "docs", "check")[0] == int(ExitCode.CONFIG_ERROR)

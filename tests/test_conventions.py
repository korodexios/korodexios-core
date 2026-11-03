"""'kdx conventions': the shipped rules, the spec template, and what 'kdx init' seeds."""

from __future__ import annotations

from pathlib import Path

import pytest

from korodexios_core.cli import conventions as conventions_module
from korodexios_core.cli.conventions import conventions_text, spec_template
from korodexios_core.cli.entry import main
from korodexios_core.cli.init_project import build_files, project_from_inputs
from korodexios_core.constants import ExitCode

from .test_cli_v2 import base_args, write_root


def run(root: Path, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main([*base_args(root), *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_conventions_resource_ships_with_the_package() -> None:
    text = conventions_text()
    assert text.startswith("# Project conventions")
    assert spec_template().startswith("# NNN-short-name")
    assert "docs/specs/TEMPLATE.md" in text


def test_kdx_conventions_prints_the_shipped_text(capsys, project_root: Path) -> None:
    write_root(project_root)
    code, out, _ = run(project_root, capsys, "conventions")
    assert code == 0
    assert out.startswith("# Project conventions")


def test_kdx_conventions_write_creates_the_file(
    capsys, project_root: Path, tmp_path: Path
) -> None:
    write_root(project_root)
    target = tmp_path / "docs" / "CONVENTIONS.md"
    code, _, _ = run(project_root, capsys, "conventions", "--write", str(target))
    assert code == 0
    assert target.read_text(encoding="utf-8") == conventions_text()


def test_kdx_conventions_write_reports_an_unwritable_target(
    capsys, project_root: Path, tmp_path: Path
) -> None:
    write_root(project_root)
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    code, _, err = run(project_root, capsys, "conventions", "--write", str(blocker / "x.md"))
    assert code == int(ExitCode.ERROR)
    assert "[!]" in err


def test_a_missing_resource_is_a_diagnostic_not_a_traceback(
    capsys, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(conventions_module, "RESOURCES", tmp_path)
    with pytest.raises(SystemExit) as stop:
        conventions_text()
    assert stop.value.code == int(ExitCode.ERROR)
    assert "[!]" in capsys.readouterr().err


def test_init_scaffold_points_at_the_conventions_and_the_spec_template() -> None:
    files = build_files(project_from_inputs("conventions_check"))  # type: ignore[arg-type]
    assert files["docs/CONVENTIONS.md"] == conventions_text()
    assert files["docs/specs/TEMPLATE.md"] == spec_template()
    assert "docs/CONVENTIONS.md" in files["AGENTS.md"]
    assert "docs/CONVENTIONS.md" in files["README.md"]
    assert "`docs/CONVENTIONS.md`" in files["AGENT_PLAYBOOK.md"]

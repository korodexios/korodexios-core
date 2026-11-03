"""'kdx brief' and 'kdx proposal': the goal interview and core-change notes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from korodexios_core.cli.brief import (
    PENDING,
    brief_template,
    parse_brief,
    pending,
    render_brief,
)
from korodexios_core.cli.entry import main
from korodexios_core.cli.init_project import build_files, project_from_inputs
from korodexios_core.cli.proposal import next_number, proposal_template, slugify

from .test_cli_v2 import base_args, write_root

ANSWERS = [
    "--goal", "keep a ledger of backups",
    "--users", "one operator",
    "--features", "list; verify; alert",
    "--non-goals", "no cloud",
    "--done", "stale backup exits 1",
]  # fmt: skip


def run(root: Path, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main([*base_args(root), *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_empty_brief_names_every_missing_answer() -> None:
    template = brief_template()
    assert template.count(PENDING) == 7
    assert pending(parse_brief(template)) == [
        "goal", "users", "features", "non_goals", "done"
    ]  # fmt: skip


def test_a_brief_survives_a_render_and_parse_round_trip() -> None:
    answers = {"goal": "g", "users": "u", "features": "a; b", "non_goals": "n", "done": "d"}
    parsed = parse_brief(render_brief(answers))
    assert parsed == answers
    assert pending(parsed) == []


def test_kdx_brief_writes_the_file_from_flags_and_check_passes(
    capsys, project_root: Path
) -> None:
    write_root(project_root)
    code, _, _ = run(project_root, capsys, "brief", *ANSWERS)
    target = project_root / "docs" / "BRIEF.md"
    assert code == 0 and "- verify" in target.read_text(encoding="utf-8")
    code, out, _ = run(project_root, capsys, "brief", "--check", "--json")
    assert code == 0 and json.loads(out) == {
        "file": str(target), "complete": True, "pending": []
    }  # fmt: skip


def test_kdx_brief_without_answers_is_incomplete_and_exits_1(capsys, project_root: Path) -> None:
    write_root(project_root)
    code, _, err = run(project_root, capsys, "brief", "--goal", "only a goal")
    assert code == 1 and "incomplete" in err
    code, out, _ = run(project_root, capsys, "brief", "--check", "--json")
    payload = json.loads(out)
    assert code == 1 and "goal" not in payload["pending"] and "users" in payload["pending"]


def test_a_later_run_keeps_earlier_answers(capsys, project_root: Path) -> None:
    write_root(project_root)
    run(project_root, capsys, "brief", *ANSWERS)
    code, _, _ = run(project_root, capsys, "brief", "--goal", "a new goal")
    text = (project_root / "docs" / "BRIEF.md").read_text(encoding="utf-8")
    assert code == 0 and "a new goal" in text and "one operator" in text


def test_check_without_a_file_says_so(capsys, project_root: Path) -> None:
    write_root(project_root)
    code, _, err = run(project_root, capsys, "brief", "--check")
    assert code == 1 and "does not exist" in err


def test_proposals_are_numbered_listed_and_use_the_template(
    capsys, project_root: Path
) -> None:
    write_root(project_root)
    code, out, _ = run(project_root, capsys, "proposal", "new", "Queue ignores stop!", "--json")
    first = json.loads(out)
    assert code == 0 and first["number"] == "001"
    assert Path(first["file"]).name == "001-queue-ignores-stop.md"
    code, out, _ = run(project_root, capsys, "proposal", "new", "Second", "--json")
    assert json.loads(out)["number"] == "002"
    text = Path(first["file"]).read_text(encoding="utf-8")
    assert text.startswith("# 001 - Queue ignores stop!") and "core-proposal 001" in text
    code, out, _ = run(project_root, capsys, "proposal", "list", "--json")
    rows = json.loads(out)["proposals"]
    assert [row["number"] for row in rows] == ["001", "002"]
    assert rows[0]["status"].startswith("proposed")
    code, out, _ = run(project_root, capsys, "proposal", "list")
    assert code == 0 and "Second" in out


def test_proposal_list_is_empty_at_first(capsys, project_root: Path) -> None:
    write_root(project_root)
    code, out, _ = run(project_root, capsys, "proposal", "list")
    assert code == 0 and "no proposals" in out


def test_proposal_helpers(tmp_path: Path) -> None:
    assert slugify("  Hello, World! ") == "hello-world"
    assert slugify("!!!") == ""
    assert next_number(tmp_path / "missing") == "001"
    assert proposal_template().startswith("# NNN - Title")


def test_init_seeds_the_brief_and_points_agents_at_it() -> None:
    files = build_files(project_from_inputs("brief_check"))  # type: ignore[arg-type]
    assert files["docs/BRIEF.md"] == brief_template()
    assert "docs/BRIEF.md" in files["AGENTS.md"]
    assert "kdx brief --check" in files["AGENTS.md"]
    assert "kdx proposal new" in files["AGENT_PLAYBOOK.md"]
    assert "docs/core-proposals" in files["docs/CONVENTIONS.md"]

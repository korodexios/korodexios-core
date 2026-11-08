"""`--json` without a daemon prints JSON on stdout, never prose (review 2026-11-08, K2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from korodexios_core.cli.entry import main


@pytest.mark.parametrize(
    "argv",
    [("jobs", "list", "--json"), ("rpc", "heartbeat", "status", "--json")],
)
def test_a_json_command_without_a_daemon_answers_in_json(
    project_root: Path, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
) -> None:
    code = main(["--home", str(project_root), "--no-log-file", "--no-color", *argv])
    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.out)
    assert payload["ok"] is False and payload["error"] == "no daemon is answering"
    assert payload["discovery_file"].endswith("chassis.ipc")
    assert "no daemon" in captured.err


def test_a_json_answer_stays_english_under_another_locale(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--home", str(project_root), "--no-log-file", "--no-color", "--locale", "sk",
                 "jobs", "list", "--json"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["error"] == "no daemon is answering"


def test_brief_check_json_reports_a_missing_brief(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--home", str(project_root), "--no-log-file", "--no-color", "brief",
                 "--check", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1 and payload["complete"] is False and payload["exists"] is False

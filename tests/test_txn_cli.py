"""'kdx txn': the terminal view of the journal a module's transactions leave behind."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.cli.txn import JOURNAL_DIR_NAME
from korodexios_core.constants import ExitCode
from korodexios_core.kernel.txn import TransactionManager, TxnOp

from .test_ipc import base_args
from .test_txn import Crash, crash_at


def manager(root: Path, **kwargs) -> TransactionManager:
    return TransactionManager(root, root / "data" / JOURNAL_DIR_NAME, **kwargs)


def test_list_is_empty_at_first(project_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([*base_args(project_root), "txn", "list"]) == int(ExitCode.SUCCESS)
    assert "no transactions" in capsys.readouterr().out
    assert main([*base_args(project_root), "txn", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"transactions": []}


def test_list_show_and_undo(project_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project_root / "a.txt").write_text("one\n", encoding="utf-8", newline="\n")
    receipt = manager(project_root).run([TxnOp.write("a.txt", "two\n")], label="edit a")
    args = base_args(project_root)

    assert main([*args, "txn", "list"]) == 0
    out = capsys.readouterr().out
    assert receipt.txn_id in out and "edit a" in out and "finalized" in out

    assert main([*args, "txn", "show", receipt.txn_id]) == 0
    assert "a.txt" in capsys.readouterr().out
    assert main([*args, "txn", "show", receipt.txn_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["phase"] == "finalized"

    assert main([*args, "txn", "undo", receipt.txn_id]) == 0
    assert "undone" in capsys.readouterr().out
    assert (project_root / "a.txt").read_text(encoding="utf-8") == "one\n"
    assert main([*args, "txn", "undo", receipt.txn_id, "--json"]) == int(ExitCode.ERROR)
    assert "already-undone" in capsys.readouterr().out


def test_undo_json_and_refusal(project_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project_root / "a.txt").write_text("one\n", encoding="utf-8", newline="\n")
    receipt = manager(project_root).run([TxnOp.write("a.txt", "two\n")])
    (project_root / "a.txt").write_text("hand edit\n", encoding="utf-8")
    args = base_args(project_root)
    assert main([*args, "txn", "undo", receipt.txn_id]) == int(ExitCode.ERROR)
    assert "result-changed" in capsys.readouterr().out
    (project_root / "a.txt").write_text("two\n", encoding="utf-8", newline="\n")
    assert main([*args, "txn", "undo", receipt.txn_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["phase"] == "undone"


def test_recover_settles_a_crash(project_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project_root / "a.txt").write_text("one\n", encoding="utf-8", newline="\n")
    with pytest.raises(Crash):
        manager(project_root, checkpoint=crash_at("applied")).run([TxnOp.write("a.txt", "two\n")])
    args = base_args(project_root)
    assert main([*args, "txn", "list"]) == 0
    assert "1 unfinished" in capsys.readouterr().out
    assert main([*args, "txn", "recover"]) == 0
    assert "rolled_back" in capsys.readouterr().out
    assert (project_root / "a.txt").read_text(encoding="utf-8") == "one\n"
    assert main([*args, "txn", "recover"]) == 0
    assert "nothing to recover" in capsys.readouterr().out
    assert main([*args, "txn", "recover", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"outcomes": []}


def test_recover_reports_failure_with_a_nonzero_exit(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project_root / "a.txt").write_text("one\n", encoding="utf-8", newline="\n")
    with pytest.raises(Crash):
        manager(project_root, checkpoint=crash_at("applied")).run([TxnOp.write("a.txt", "two\n")])
    for backup in (project_root / "data" / JOURNAL_DIR_NAME).glob("*.backup/*"):
        backup.unlink()
    assert main([*base_args(project_root), "txn", "recover"]) == int(ExitCode.ERROR)
    assert "backup-missing" in capsys.readouterr().out


def test_show_unknown_id(project_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([*base_args(project_root), "txn", "show", "nope"]) == int(ExitCode.ERROR)
    assert "txn-unknown" in capsys.readouterr().out

"""Transactions: all-or-nothing runs, crash recovery at every phase, and guarded undo."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.kernel.txn import (
    TransactionManager,
    TxnError,
    TxnOp,
    default_reason,
)


class Crash(BaseException):
    """Stands in for the process dying: it is not an Exception, so nothing compensates."""


def crash_at(phase: str):
    def checkpoint(seen: str) -> None:
        if seen == phase:
            raise Crash(seen)

    return checkpoint


@pytest.fixture()
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.txt").write_text("alpha\n", encoding="utf-8", newline="\n")
    (root / "b.txt").write_text("beta\n", encoding="utf-8", newline="\n")
    return root


def manager(tree: Path, **kwargs: Any) -> TransactionManager:
    return TransactionManager(tree, tree.parent / "journal", **kwargs)


def snapshot(tree: Path) -> dict[str, bytes]:
    return {
        p.relative_to(tree).as_posix(): p.read_bytes()
        for p in sorted(tree.rglob("*"))
        if p.is_file()
    }


def change() -> list[TxnOp]:
    return [
        TxnOp.write("a.txt", "ALPHA\n"),
        TxnOp.write("new/c.txt", b"gamma\n"),
        TxnOp.delete("b.txt"),
    ]


def test_run_applies_every_op_and_finalizes(tree: Path) -> None:
    receipt = manager(tree).run(change(), label="demo")
    assert receipt.phase == "finalized"
    assert receipt.label == "demo"
    assert receipt.paths == ("a.txt", "new/c.txt", "b.txt")
    assert snapshot(tree) == {"a.txt": b"ALPHA\n", "new/c.txt": b"gamma\n"}
    assert receipt.as_dict()["ops"][0]["before"] != receipt.as_dict()["ops"][0]["after"]
    assert manager(tree).receipt(receipt.txn_id).phase == "finalized"
    assert [item.txn_id for item in manager(tree).receipts()] == [receipt.txn_id]
    assert manager(tree).pending() == []


@pytest.mark.parametrize(
    "ops",
    [
        [],
        [TxnOp.write("../escape.txt", "x")],
        [TxnOp.write("/abs.txt", "x")],
        [TxnOp.write("C:/x.txt", "x")],
        [TxnOp.write("a.txt:stream", "x")],
        [TxnOp.write("", "x")],
        [TxnOp.write(".", "x")],
        [TxnOp.write("a.txt", "1"), TxnOp.write("A.TXT", "2")],
        [TxnOp(path="a.txt", action="rename")],
    ],
)
def test_refused_plans_touch_nothing(tree: Path, ops: list[TxnOp]) -> None:
    before = snapshot(tree)
    with pytest.raises(TxnError):
        manager(tree).run(ops)
    assert snapshot(tree) == before
    assert manager(tree).receipts() == []


def test_a_directory_target_is_refused(tree: Path) -> None:
    (tree / "d").mkdir()
    with pytest.raises(TxnError) as caught:
        manager(tree).run([TxnOp.write("d", "x")])
    assert caught.value.code == "target-is-directory"


def test_allowlist_and_reason_callbacks(tree: Path) -> None:
    seen: list[str] = []

    def reason(code: str, fields: Mapping[str, Any]) -> str:
        seen.append(code)
        return f"nope {code} {fields.get('path')}"

    guarded = manager(tree, allow=lambda path: path.startswith("a"), reason=reason)
    with pytest.raises(TxnError, match=r"nope path-not-allowed b.txt") as caught:
        guarded.run([TxnOp.write("a.txt", "1"), TxnOp.write("b.txt", "2")])
    assert caught.value.code == "path-not-allowed"
    assert caught.value.fields == {"path": "b.txt"}
    assert seen == ["path-not-allowed"]
    assert snapshot(tree)["a.txt"] == b"alpha\n"


def test_the_journal_directory_cannot_be_a_target(tree: Path) -> None:
    inside = TransactionManager(tree, tree / "journal")
    with pytest.raises(TxnError) as caught:
        inside.run([TxnOp.write("journal/x.json", "{}")])
    assert caught.value.code == "path-not-allowed"


def test_default_reason_reads_plainly() -> None:
    assert default_reason("empty-transaction", {}) == "empty-transaction"
    assert default_reason("x", {"b": 2, "a": 1}) == "x: a=1, b=2"


def test_an_ordinary_failure_compensates_before_raising(tree: Path) -> None:
    before = snapshot(tree)

    def boom(phase: str) -> None:
        if phase == "op:1":
            raise OSError("disk went away")

    with pytest.raises(TxnError) as caught:
        manager(tree, checkpoint=boom).run(change())
    assert caught.value.code == "apply-failed"
    assert snapshot(tree) == before
    assert [item.phase for item in manager(tree).receipts()] == ["rolled_back"]


@pytest.mark.parametrize("phase", ["prepared", "backed_up", "op:0", "op:1", "op:2", "applied"])
def test_a_crash_before_commit_is_compensated_by_recover(tree: Path, phase: str) -> None:
    before = snapshot(tree)
    with pytest.raises(Crash):
        manager(tree, checkpoint=crash_at(phase)).run(change())
    assert len(manager(tree).pending()) == 1
    outcomes = manager(tree).recover()
    assert [(item.action) for item in outcomes] == ["rolled_back"]
    assert snapshot(tree) == before
    assert manager(tree).pending() == []
    assert manager(tree).recover() == []


def test_a_crash_after_commit_is_finished(tree: Path) -> None:
    with pytest.raises(Crash):
        manager(tree, checkpoint=crash_at("committed")).run(change())
    outcomes = manager(tree).recover()
    assert [item.action for item in outcomes] == ["finalized"]
    assert snapshot(tree) == {"a.txt": b"ALPHA\n", "new/c.txt": b"gamma\n"}
    assert manager(tree).receipts()[0].phase == "finalized"


def test_recover_is_safe_to_run_twice_after_a_partial_restore(tree: Path) -> None:
    before = snapshot(tree)
    with pytest.raises(Crash):
        manager(tree, checkpoint=crash_at("op:2")).run(change())
    (tree / "a.txt").write_bytes(b"alpha\n")
    assert [item.action for item in manager(tree).recover()] == ["rolled_back"]
    assert snapshot(tree) == before


def test_undo_restores_the_files(tree: Path) -> None:
    before = snapshot(tree)
    receipt = manager(tree).run(change())
    undone = manager(tree).undo(receipt.txn_id)
    assert undone.phase == "undone"
    assert snapshot(tree) == before
    with pytest.raises(TxnError) as caught:
        manager(tree).undo(receipt.txn_id)
    assert caught.value.code == "already-undone"


def test_undo_is_refused_when_the_result_moved(tree: Path) -> None:
    receipt = manager(tree).run(change())
    (tree / "a.txt").write_text("edited by hand\n", encoding="utf-8")
    with pytest.raises(TxnError) as caught:
        manager(tree).undo(receipt.txn_id)
    assert caught.value.code == "result-changed"
    assert caught.value.fields["path"] == "a.txt"
    assert (tree / "a.txt").read_text(encoding="utf-8") == "edited by hand\n"
    assert not (tree / "b.txt").exists()
    assert manager(tree).receipt(receipt.txn_id).phase == "finalized"


def test_undo_is_refused_when_a_deleted_file_came_back(tree: Path) -> None:
    receipt = manager(tree).run(change())
    (tree / "b.txt").write_text("new b\n", encoding="utf-8")
    with pytest.raises(TxnError, match="result-changed"):
        manager(tree).undo(receipt.txn_id)


def test_undo_of_an_unfinished_or_unknown_transaction(tree: Path) -> None:
    with pytest.raises(Crash):
        manager(tree, checkpoint=crash_at("applied")).run(change())
    pending = manager(tree).pending()[0]
    with pytest.raises(TxnError) as caught:
        manager(tree).undo(pending)
    assert caught.value.code == "not-undoable"
    for bad in ("nope", "../x", ""):
        with pytest.raises(TxnError) as unknown:
            manager(tree).undo(bad)
        assert unknown.value.code == "txn-unknown"


def test_a_missing_backup_blocks_the_rollback_loudly(tree: Path) -> None:
    with pytest.raises(Crash):
        manager(tree, checkpoint=crash_at("op:2")).run(change())
    journal = tree.parent / "journal"
    for backup in journal.glob("*.backup/*"):
        backup.unlink()
    outcomes = manager(tree).recover()
    assert outcomes[0].action == "failed"
    assert "backup-missing" in outcomes[0].detail


def test_a_corrupt_backup_is_not_restored(tree: Path) -> None:
    with pytest.raises(Crash):
        manager(tree, checkpoint=crash_at("op:2")).run(change())
    for backup in (tree.parent / "journal").glob("*.backup/*"):
        backup.write_bytes(b"tampered")
    outcomes = manager(tree).recover()
    assert outcomes[0].action == "failed"
    assert "backup-corrupt" in outcomes[0].detail
    assert not (tree / "b.txt").exists()


def test_unreadable_journals_are_reported_not_fatal(tree: Path) -> None:
    mgr = manager(tree)
    mgr.run([TxnOp.write("a.txt", "1")])
    journal = tree.parent / "journal"
    (journal / "junk.json").write_text("{not json", encoding="utf-8")
    (journal / "deep.json").write_text("[" * 100000, encoding="utf-8")
    (journal / "odd.json").write_text(json.dumps({"id": "other"}), encoding="utf-8")
    actions = {item.txn_id: item.action for item in mgr.recover()}
    assert actions == {"junk": "failed", "deep": "failed", "odd": "failed"}
    assert len(mgr.receipts()) == 1


def test_receipt_of_an_unknown_id(tree: Path) -> None:
    with pytest.raises(TxnError) as caught:
        manager(tree).receipt("missing")
    assert caught.value.code == "txn-unknown"
    assert manager(tree).receipts() == []
    assert manager(tree).recover() == []


def test_concurrent_transactions_are_serialised_and_both_undo(tree: Path) -> None:
    """Review 2026-11-08, B3: the plan is read under the lock, so a later undo still works."""
    inside = 0
    peak = 0
    guard = threading.Lock()

    def checkpoint(phase: str) -> None:
        nonlocal inside, peak
        if phase == "prepared":
            with guard:
                inside += 1
                peak = max(peak, inside)
            time.sleep(0.3)
        elif phase == "finalized":
            with guard:
                inside -= 1

    mgr = manager(tree, checkpoint=checkpoint)
    ids: dict[str, str] = {}

    def run(name: str, delay: float) -> None:
        time.sleep(delay)
        ids[name] = mgr.run([TxnOp.write("a.txt", name)], label=name).txn_id

    threads = [threading.Thread(target=run, args=("A", 0.0)),
               threading.Thread(target=run, args=("B", 0.1))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert peak == 1, "one transaction at a time"
    assert (tree / "a.txt").read_text(encoding="utf-8") == "B"
    mgr.undo(ids["B"])
    assert (tree / "a.txt").read_text(encoding="utf-8") == "A"

"""context.db(): an opt-in SQLite database with migrations, and 'kdx db' to look into it."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.constants import ExitCode
from korodexios_core.kernel.database import (
    Database,
    DatabaseError,
    databases_in,
    split_statements,
    validate_database_name,
)
from korodexios_core.kernel.ui_tiers import guide

from .test_ipc import base_args

NOTE = "CREATE TABLE note (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"


def make(tmp_path: Path) -> Database:
    return Database(tmp_path / "db" / "main.sqlite3")


def test_nothing_exists_until_a_statement_runs(tmp_path: Path) -> None:
    db = make(tmp_path)
    assert not db.exists() and db.schema_version() == 0
    assert db.describe()["tables"] == [] and db.describe()["exists"] is False
    assert not (tmp_path / "db").exists()
    with pytest.raises(DatabaseError, match="does not exist"):
        db.query("SELECT 1")


def test_migrations_run_once_in_order_and_a_failure_changes_nothing(tmp_path: Path) -> None:
    db = make(tmp_path)
    steps = [(1, NOTE), (2, "ALTER TABLE note ADD COLUMN tag TEXT; CREATE INDEX n ON note(tag);")]
    assert db.migrate(steps) == [1, 2]
    assert db.migrate(steps) == []  # already applied
    assert db.schema_version() == 2
    bad = [*steps, (3, "ALTER TABLE note ADD COLUMN ok TEXT; CREATE TABLE note (x)")]
    with pytest.raises(DatabaseError, match="migration 3"):
        db.migrate(bad)
    assert db.schema_version() == 2
    columns = [c["name"] for c in db.describe()["tables"][0]["columns"]]
    assert "ok" not in columns  # the whole failed step was rolled back
    for wrong in ([(2, NOTE), (1, NOTE)], [(0, NOTE)], [(1, NOTE), (1, NOTE)]):
        with pytest.raises(DatabaseError, match="ascending"):
            db.migrate(wrong)


def test_statements_queries_and_transactions(tmp_path: Path) -> None:
    db = make(tmp_path)
    db.migrate([(1, NOTE)])
    first = db.insert("INSERT INTO note (body) VALUES (?)", ("a",))
    assert db.execute("UPDATE note SET body = ? WHERE id = ?", ("b", first)) == 1
    assert db.one("SELECT body FROM note WHERE id = :id", {"id": first}) == {"body": "b"}
    assert db.one("SELECT * FROM note WHERE id = 99") is None
    with db.transaction() as tx:
        tx.insert("INSERT INTO note (body) VALUES (?)", ("c",))
        assert len(tx.query("SELECT * FROM note")) == 2  # sees its own change
        assert tx.execute("DELETE FROM note WHERE body = ?", ("b",)) == 1
    assert [r["body"] for r in db.query("SELECT body FROM note")] == ["c"]
    with pytest.raises(RuntimeError), db.transaction() as tx:
        tx.execute("DELETE FROM note")
        raise RuntimeError("boom")
    assert len(db.query("SELECT * FROM note")) == 1  # rolled back
    assert len(db.query("SELECT * FROM note", limit=1)) == 1
    with pytest.raises(sqlite3.Error):
        db.query("DELETE FROM note")  # query() opens the file read-only


def test_describe_backup_and_the_directory_listing(tmp_path: Path) -> None:
    db = make(tmp_path)
    db.migrate([(1, NOTE)])
    db.insert("INSERT INTO note (body) VALUES ('x')")
    view = db.describe()
    note = next(t for t in view["tables"] if t["name"] == "note")
    assert note["rows"] == 1 and note["system"] is False
    assert [c["name"] for c in note["columns"]] == ["id", "body"]
    assert next(t for t in view["tables"] if t["name"] == "schema_migrations")["system"] is True
    target = db.backup(tmp_path / "copies" / "main.sqlite3")
    assert Database(target).query("SELECT body FROM note") == [{"body": "x"}]
    with pytest.raises(DatabaseError, match="already exists"):
        db.backup(target)
    with pytest.raises(DatabaseError, match="does not exist"):
        Database(tmp_path / "db" / "none.sqlite3", "none").backup(tmp_path / "x.sqlite3")
    (tmp_path / "db" / "Bad Name.sqlite3").write_bytes(b"")
    assert [d.name for d in databases_in(tmp_path / "db")] == ["main"]
    assert databases_in(tmp_path / "missing") == []


def test_helpers_validate_names_and_split_sql() -> None:
    assert validate_database_name("orders-2") == "orders-2"
    for bad in ("", "Main", "../x", "1a", "a" * 60):
        with pytest.raises(DatabaseError, match="usable database name"):
            validate_database_name(bad)
    trigger = (
        "CREATE TRIGGER t AFTER INSERT ON a BEGIN INSERT INTO b VALUES (1); END; SELECT ';';"
    )
    assert split_statements("SELECT 1") == ["SELECT 1"]  # the last ';' may be left out
    assert split_statements(trigger) == [
        "CREATE TRIGGER t AFTER INSERT ON a BEGIN INSERT INTO b VALUES (1); END;", "SELECT ';';"]
    with pytest.raises(DatabaseError, match="incomplete"):
        split_statements("SELECT 1; SELECT 'open")


def test_the_context_hands_out_one_handle_per_name_and_creates_no_file(project_root) -> None:
    from .test_ipc import ipc_chassis

    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        context = chassis.context
        assert context.databases == {}
        main_db = context.db()
        assert main_db is context.db("main") and context.db("other") is not main_db
        assert main_db.path == chassis.paths.durable_dir / "db" / "main.sqlite3"
        assert not main_db.exists()
        assert context.as_dict()["databases"] == ["main", "other"]
        with pytest.raises(DatabaseError):
            context.db("Not Valid")
    finally:
        chassis.shutdown()


def test_the_terminal_inspects_databases_read_only(project_root, capsys) -> None:
    args = base_args(project_root)
    assert main([*args, "db", "list"]) == 0
    assert "no module has asked" in capsys.readouterr().out

    db = Database(project_root / "data" / "db" / "main.sqlite3")
    db.migrate([(1, NOTE)])
    db.insert("INSERT INTO note (body) VALUES ('hello')")

    assert main([*args, "db", "list", "--json"]) == 0
    out = capsys.readouterr().out
    rows = json.loads(out[out.index("{"):out.rindex("}") + 1])["databases"]
    assert rows[0]["name"] == "main" and rows[0]["schema_version"] == 1

    assert main([*args, "db", "show"]) == 0
    assert "note" in capsys.readouterr().out
    assert main([*args, "db", "show", "ghost", "--json"]) == 0
    assert '"exists": false' in capsys.readouterr().out
    assert main([*args, "db", "show", "ghost"]) == 0
    assert "does not exist yet" in capsys.readouterr().out

    assert main([*args, "db", "query", "main", "SELECT body FROM note", "--json"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index("{"):out.rindex("}") + 1])["rows"] == [{"body": "hello"}]
    assert main([*args, "db", "query", "main", "SELECT body FROM note"]) == 0
    assert "hello" in capsys.readouterr().out
    assert main([*args, "db", "query", "main", "SELECT * FROM note WHERE id = 99"]) == 0
    assert "no rows" in capsys.readouterr().out
    for sql in ("DELETE FROM note", "SELECT 1; SELECT 2", "SELEC nonsense"):
        assert main([*args, "db", "query", "main", sql]) == int(ExitCode.ERROR)
    assert db.query("SELECT COUNT(*) AS n FROM note") == [{"n": 1}]  # nothing was changed
    assert main([*args, "db", "query", "Bad Name", "SELECT 1"]) == int(ExitCode.ERROR)
    capsys.readouterr()

    copy = project_root / "backup.sqlite3"
    assert main([*args, "db", "backup", "main", "--to", str(copy), "--json"]) == 0
    assert copy.is_file()
    assert main([*args, "db", "backup", "main", "--to", str(copy)]) == int(ExitCode.ERROR)
    assert main([*args, "db", "backup", "main"]) == 0
    assert list((project_root / "data" / "db" / "backups").glob("main-*.sqlite3"))
    assert main([*args, "db", "backup", "ghost"]) == int(ExitCode.ERROR)


def test_the_guide_says_when_a_database_is_worth_it() -> None:
    storage = guide()["storage"]
    assert any("context.db" in item["use"] for item in storage)
    assert any("no database" in item["use"].lower() or "kdx.yml" in item["use"] for item in storage)

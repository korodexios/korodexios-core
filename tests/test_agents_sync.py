"""'kdx agents sync': every agent's rule file follows AGENTS.md, and the seed ships them."""

from __future__ import annotations

import json
from pathlib import Path

from korodexios_core.cli.agents import MARKER, TARGETS, sync_agents
from korodexios_core.cli.entry import main
from korodexios_core.cli.init_project import build_files, project_from_inputs

from .test_ipc import base_args


def test_sync_writes_checks_and_keeps_hand_written_files(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Rules\n\nBe careful.\n", encoding="utf-8")
    assert {row.state for row in sync_agents(tmp_path, check=True)} == {"missing"}
    written = sync_agents(tmp_path)
    assert {row.state for row in written} == {"written"}
    assert "@AGENTS.md" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    cursor = (tmp_path / ".cursor/rules/agents.mdc").read_text(encoding="utf-8")
    assert cursor.startswith("---\n") and "alwaysApply: true" in cursor and "Be careful." in cursor
    windsurf = (tmp_path / ".windsurf/rules/agents.md").read_text(encoding="utf-8")
    assert "trigger: always_on" in windsurf
    assert {row.state for row in sync_agents(tmp_path, check=True)} == {"current"}

    (tmp_path / "AGENTS.md").write_text("# Rules\n\nBe very careful.\n", encoding="utf-8")
    stale = {row.key: row.state for row in sync_agents(tmp_path, check=True)}
    assert stale["copilot"] == "stale" and stale["claude"] == "current"  # an import never ages

    (tmp_path / "GEMINI.md").write_text("my own notes\n", encoding="utf-8")
    rows = {row.key: row.state for row in sync_agents(tmp_path)}
    assert rows["gemini"] == "kept" and rows["copilot"] == "written"
    assert (tmp_path / "GEMINI.md").read_text(encoding="utf-8") == "my own notes\n"
    assert {row.key: row.state for row in sync_agents(tmp_path, check=True)}["gemini"] == "kept"
    forced = {row.key: row.state for row in sync_agents(tmp_path, force=True, only=("gemini",))}
    assert forced == {"gemini": "written"}
    assert MARKER in (tmp_path / "GEMINI.md").read_text(encoding="utf-8")


def test_cli_reports_and_gates(tmp_path: Path, capsys) -> None:
    args = base_args(tmp_path)
    assert main([*args, "agents", "sync"]) == 1
    assert "AGENTS.md" in capsys.readouterr().err
    (tmp_path / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
    assert main([*args, "agents", "sync", "--check"]) == 1
    assert main([*args, "agents", "sync", "--only", "nobody"]) == 2
    assert main([*args, "agents", "sync", "--only", "claude,cursor"]) == 0
    capsys.readouterr()
    assert main([*args, "agents", "sync", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {row["agent"] for row in payload["rows"]} == {target.key for target in TARGETS}
    assert "Codex" in payload["native"]
    assert main([*args, "agents", "sync", "--check"]) == 0
    (tmp_path / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
    assert main([*args, "agents", "sync"]) == 0
    assert "--force" in capsys.readouterr().out


def test_the_seed_ships_mcp_and_every_rule_file() -> None:
    files = build_files(project_from_inputs("agentic"))  # type: ignore[arg-type]
    server = json.loads(files[".mcp.json"])["mcpServers"]["agentic"]
    assert server == {"command": "uv", "args": ["run", "agentic", "mcp", "serve"]}
    for target in TARGETS:
        assert MARKER in files[target.path], target.path
    assert "kdx agents sync" in files["AGENTS.md"]
    assert "Be careful" not in files["CLAUDE.md"] and "@AGENTS.md" in files["CLAUDE.md"]
    assert "Agent tools" in files[".github/copilot-instructions.md"]

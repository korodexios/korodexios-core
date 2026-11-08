"""The wiki in the LLM-wiki layout: schema, raw sources, log, wikilinks, lint, and export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from korodexios_core.cli.entry import main
from korodexios_core.modules.wiki.export import (
    MANIFEST_NAME,
    apply_export,
    bundle,
    plan_export,
)
from korodexios_core.modules.wiki.pages import (
    INDEX_MARKER,
    append_log,
    check_pages,
    page_path,
    raw_files,
    read_log,
    render_page,
    scan,
    schema_text,
    wikilinks,
)

from .test_ipc import base_args


def run(root: Path, capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main([*base_args(root), *args])
    return code, capsys.readouterr().out


def header(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return dict(yaml.safe_load(text[4:text.find("\n---", 3)]))


# -- pages ------------------------------------------------------------------------------


def test_new_pages_carry_the_llm_wiki_header() -> None:
    text = render_page("MCP", page_type="concept", tags=["agents"], sources=["notes"],
                       today="2026-11-06")
    assert "tags: [agents]" in text and "sources: [notes]" in text
    assert "created: 2026-11-06" in text and "&id" not in text
    source = render_page("Notes", page_type="source", raw="raw/notes.md", today="2026-11-06")
    assert "sources:" not in source and "raw: raw/notes.md" in source
    assert "ingested: 2026-11-06" in source
    assert "[[" not in render_page("Blank")  # no example link other tools would trip over


def test_paths_follow_type_folders_and_language_suffixes(tmp_path: Path) -> None:
    assert page_path(tmp_path, "mcp", "en", "en", "concept") == tmp_path / "concepts/mcp.md"
    assert page_path(tmp_path, "mcp", "sk", "en", "concept") == tmp_path / "concepts/mcp.sk.md"
    assert page_path(tmp_path, "home", "en", "en", "page") == tmp_path / "home.md"
    assert page_path(tmp_path, "ops/run", "en", "en", "guide") == tmp_path / "ops/run.md"


def test_scan_skips_raw_graph_and_the_bookkeeping_files(tmp_path: Path) -> None:
    for name in ("raw/article.md", "graph/x.md", ".obsidian/y.md", "index.md", "log.md",
                 "SCHEMA.md", "concepts/mcp.md", "concepts/mcp.sk.md"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntitle: {name}\n---\nbody\n", encoding="utf-8")
    pages = scan(tmp_path, "public", "en")
    assert [(page.slug, page.name, page.language) for page in pages] == [
        ("concepts/mcp", "mcp", "en"), ("concepts/mcp", "mcp", "sk")
    ]
    assert raw_files(tmp_path) == ["raw/article.md"]
    assert raw_files(tmp_path / "nothing") == []


def test_wikilinks_ignore_code_and_keep_names() -> None:
    text = "See [[mcp]], [[deploy|how]] and [[ops#run]]. `[[not-a-link]]`\n```\n[[nor]]\n```"
    assert wikilinks(text) == ["mcp", "deploy", "ops"]


def test_log_is_append_only_and_parseable(tmp_path: Path) -> None:
    append_log(tmp_path, "ingest", "Notes  (raw/notes.md)", today="2026-11-05")
    append_log(tmp_path, "update", "linked\nmcp", today="2026-11-06")
    text = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert text.startswith("# Wiki Log") and "## [2026-11-06] update | linked mcp" in text
    assert read_log(tmp_path) == [
        {"date": "2026-11-05", "operation": "ingest", "description": "Notes (raw/notes.md)"},
        {"date": "2026-11-06", "operation": "update", "description": "linked mcp"},
    ]
    assert read_log(tmp_path, 1)[0]["operation"] == "update"
    assert read_log(tmp_path / "none") == []


def test_schema_names_the_layout_and_the_languages() -> None:
    text = schema_text("public", ["en", "sk"], "en")
    assert "`raw/`" in text and "`page.sk.md`" in text and "kdx wiki ingest" in text
    assert "none yet" in schema_text("public", ["en"], "en")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_lint_finds_what_an_llm_wiki_lint_finds(tmp_path: Path) -> None:
    write(tmp_path / "raw/notes.md", "source text")
    write(tmp_path / "raw/unread.pdf", "pdf")
    write(tmp_path / "sources/notes.md",
          "---\ntype: source\ntitle: Notes\ntags: [x]\nraw: raw/notes.md\n---\nAbout [[mcp]].\n")
    write(tmp_path / "sources/gone.md",
          "---\ntype: source\ntitle: Gone\ntags: [x]\nraw: raw/gone.md\n---\nSee [[mcp]].\n")
    write(tmp_path / "concepts/mcp.md",
          "---\ntype: concept\ntitle: MCP\ntags: [a]\nsources: [notes, missing]\n---\n"
          "Links [[nowhere]] and [notes](../sources/notes.md).\n" + "line\n" * 801)
    write(tmp_path / "concepts/lonely.md", "---\ntype: concept\ntitle: Lonely\n---\nalone\n")
    write(tmp_path / "ops/mcp.md", "---\ntype: guide\ntitle: Twin\ntags: [b]\n---\n[[lonely]]\n")
    write(tmp_path / "home.md", "---\ntype: page\ntitle: Home\ntags: [s]\n---\n[[mcp]]\n")
    issues = check_pages(scan(tmp_path, "public", "en"), ["en"], directory=tmp_path)
    found = {(issue.severity, issue.message) for issue in issues}
    assert ("error", "broken wikilink [[nowhere]]") in found
    assert ("error", "source 'missing' is not a page") in found
    assert ("error", "raw file raw/gone.md is missing") in found
    assert ("warning", "a concept page lists no sources") in found
    assert ("warning", "no tags") in found
    assert ("warning", "raw source not ingested: no page names it") in found
    assert any("split it" in message for _severity, message in found)
    assert any("two pages are named [[mcp]]" in message for _severity, message in found)
    orphans = {issue.path for issue in issues if issue.message.startswith("orphan")}
    assert "home" not in orphans and "sources/notes" not in orphans
    assert "sources/gone" in orphans


# -- the CLI ----------------------------------------------------------------------------


def configure(root: Path, languages: str = "[en, sk]") -> None:
    text = (root / "kdx.yml").read_text(encoding="utf-8")
    (root / "kdx.yml").write_text(text + f"  wiki:\n    languages: {languages}\n",
                                  encoding="utf-8")


def test_init_ingest_new_log_index_check(capsys, project_root: Path, tmp_path: Path) -> None:
    configure(project_root)
    code, out = run(project_root, capsys, "wiki", "init", "--json")
    assert code == 0
    wiki = project_root / "docs" / "wiki"
    assert (wiki / "SCHEMA.md").is_file() and (wiki / "raw").is_dir()
    assert (project_root / "docs-dev" / "wiki" / "SCHEMA.md").is_file()
    source = tmp_path / "Release Notes.md"
    source.write_text("# Notes\nMCP arrived.\n", encoding="utf-8")
    code, out = run(project_root, capsys, "wiki", "ingest", str(source), "--tags", "release",
                    "--json")
    made = json.loads(out)
    assert code == 0 and made["name"] == "release-notes"
    assert (wiki / "raw" / "release-notes.md").read_text(encoding="utf-8").startswith("# Notes")
    assert header(wiki / "sources" / "release-notes.md")["raw"] == "raw/release-notes.md"
    assert run(project_root, capsys, "wiki", "ingest", str(source))[0] == 1  # raw is immutable
    assert run(project_root, capsys, "wiki", "ingest", str(tmp_path / "none.md"))[0] == 1
    assert run(project_root, capsys, "wiki", "ingest", str(source), "--slug", "Bad")[0] == 2
    assert run(project_root, capsys, "wiki", "new", "mcp", "--type", "concept", "--tags",
               "agents", "--sources", "release-notes", "--body", "See [[home]].")[0] == 0
    assert header(wiki / "concepts" / "mcp.md")["sources"] == ["release-notes"]
    assert run(project_root, capsys, "wiki", "new", "home", "--tags", "start",
               "--body", "Start at [[mcp]].")[0] == 0
    assert header(wiki / "home.md")["type"] == "page"
    code, out = run(project_root, capsys, "wiki", "log", "update", "linked", "mcp")
    assert code == 0 and "update | linked mcp" in out
    assert run(project_root, capsys, "wiki", "log", "update")[0] == 2
    code, out = run(project_root, capsys, "wiki", "log", "--json")
    operations = [entry["operation"] for entry in json.loads(out)["entries"]]
    assert operations == ["schema", "ingest", "create", "create", "update"]
    assert "[" in run(project_root, capsys, "wiki", "log", "-n", "2")[1]
    assert run(project_root, capsys, "wiki", "log", "--internal")[0] == 0
    (wiki / "INDEX.md").write_text(INDEX_MARKER + "\nold\n", encoding="utf-8")
    assert run(project_root, capsys, "wiki", "index")[0] == 0
    names = {entry.name for entry in wiki.iterdir()}
    assert "index.md" in names and "INDEX.md" not in names
    index = (wiki / "index.md").read_text(encoding="utf-8")
    assert "## Sources" in index and "- [[mcp]]" in index and "## Pages" in index
    code, out = run(project_root, capsys, "wiki", "check", "--record", "--json")
    report = json.loads(out)
    assert code == 0 and report["errors"] == 0
    assert any("no sk version" in issue["message"] for issue in report["issues"])
    assert not any(issue["path"].endswith("release-notes") and "sk version" in issue["message"]
                   for issue in report["issues"])  # a source summary needs no translation
    assert "lint |" in (wiki / "log.md").read_text(encoding="utf-8")
    code, out = run(project_root, capsys, "wiki", "list", "--type", "concept")
    assert "mcp" in out and "home" not in out
    code, out = run(project_root, capsys, "wiki", "show", "mcp", "--json")
    assert json.loads(out)["slug"] == "concepts/mcp"  # found by its wikilink name


def test_mcp_resources_include_the_schema(monkeypatch, capsys, project_root: Path) -> None:
    from .test_mcp import rpc, serve

    assert main([*base_args(project_root), "wiki", "init"]) == 0
    capsys.readouterr()
    replies = serve(monkeypatch, capsys, project_root, [
        rpc("resources/read", {"uri": "kdx://wiki/public/schema"}, 1),
        rpc("tools/list", None, 2),
    ], "--in-process", "--role", "admin")
    assert "Wiki Schema" in replies[0]["result"]["contents"][0]["text"]
    tools = {tool["name"]: tool for tool in replies[1]["result"]["tools"]}
    assert "wiki_ingest" in tools and tools["wiki_export"]["annotations"]["destructiveHint"]


# -- export -----------------------------------------------------------------------------


def project_wiki(root: Path) -> Path:
    wiki = root / "docs" / "wiki"
    write(wiki / "raw/notes.md", "raw notes")
    write(wiki / "sources/notes.md",
          "---\ntype: source\ntitle: Notes\ntags: [r]\nraw: raw/notes.md\n---\nOn [[mcp]].\n")
    write(wiki / "concepts/mcp.md",
          "---\ntype: concept\ntitle: MCP\ntags: [a]\nsources: [notes]\n---\n"
          "See [[deploy|how]], [deploy](../deploy.md) and `[[deploy]]`.\n")
    write(wiki / "deploy.md", "---\ntype: page\ntitle: Deploy\ntags: [o]\n---\n[[mcp]]\n")
    return wiki


def llm_wiki(base: Path) -> Path:
    """A target laid out like an LLM-wiki: base/wiki with index and log, base/raw beside it."""
    (base / "raw").mkdir(parents=True)
    write(base / "wiki/index.md", "# Wiki Index\n\n## Sources\n")
    write(base / "wiki/log.md", "# Wiki Log\n\n---\n")
    write(base / "wiki/concepts/deploy.md", "---\ntype: concept\ntitle: Theirs\n---\nmine\n")
    return base / "wiki"


def plan(wiki: Path, target: Path, **options: Any) -> Any:
    pages = scan(wiki, "public", "en")
    return plan_export(pages, wiki, raw_files(wiki), target, namespace="myapp", **options)


def test_export_refuses_clashes_then_merges_with_a_prefix(tmp_path: Path) -> None:
    wiki = project_wiki(tmp_path / "project")
    target = llm_wiki(tmp_path / "base")
    clash = plan(wiki, target)
    assert clash.collisions == ["deploy"]
    with pytest.raises(ValueError, match="--prefix"):
        apply_export(clash, project="myapp")
    merged = plan(wiki, target, prefix="app")
    done = apply_export(merged, project="myapp")
    folder = target / "myapp"
    mcp = (folder / "concepts" / "app-mcp.md").read_text(encoding="utf-8")
    assert "[[app-deploy|how]]" in mcp and "](../app-deploy.md)" in mcp
    assert "`[[deploy]]`" in mcp  # code is never rewritten
    assert header(folder / "concepts" / "app-mcp.md")["sources"] == ["app-notes"]
    assert header(folder / "sources" / "app-notes.md")["raw"] == "raw/myapp/notes.md"
    assert (tmp_path / "base" / "raw" / "myapp" / "notes.md").read_text(encoding="utf-8") == (
        "raw notes"
    )
    catalog = folder / "app-index.md"
    assert header(catalog)["type"] == "synthesis" and "[[app-mcp]]" in catalog.read_text(
        encoding="utf-8")
    assert "- [[app-index]] - pages imported from myapp" in (target / "index.md").read_text(
        encoding="utf-8")
    assert "import | myapp" in (target / "log.md").read_text(encoding="utf-8")
    assert {"link index.md", "log log.md"} <= set(done)
    assert (target / "concepts" / "deploy.md").read_text(encoding="utf-8").endswith("mine\n")
    again = plan(wiki, target, prefix="app")
    assert not again.writes and not again.removes
    apply_export(again, project="myapp")
    assert (target / "index.md").read_text(encoding="utf-8").count("[[app-index]]") == 1
    assert (target / "log.md").read_text(encoding="utf-8").count("import | myapp") == 1


def test_export_updates_removes_and_keeps_edited_files(tmp_path: Path) -> None:
    wiki = project_wiki(tmp_path / "project")
    target = tmp_path / "vault"  # a plain folder: raw goes inside the namespace
    target.mkdir()
    apply_export(plan(wiki, target), project="myapp")
    folder = target / "myapp"
    assert (folder / "raw" / "notes.md").is_file()
    manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert "@raw/notes.md" in manifest["files"]
    (folder / "deploy.md").write_text("edited by the vault owner\n", encoding="utf-8")
    (wiki / "concepts" / "mcp.md").write_text(
        "---\ntype: concept\ntitle: MCP v2\ntags: [a]\nsources: [notes]\n---\nnew\n",
        encoding="utf-8")
    (wiki / "sources" / "notes.md").unlink()
    update = plan(wiki, target)
    assert "concepts/mcp.md" in update.writes
    assert "sources/notes.md" in update.removes
    assert "deploy.md" in update.kept
    apply_export(update, project="myapp")
    assert not (folder / "sources" / "notes.md").exists()
    assert (folder / "deploy.md").read_text(encoding="utf-8") == "edited by the vault owner\n"
    assert "MCP v2" in (folder / "concepts" / "mcp.md").read_text(encoding="utf-8")
    write(folder / "own.md", "the owner's own page")
    (wiki / "own.md").write_text("---\ntitle: Own\n---\nx\n", encoding="utf-8")
    assert "own.md" in plan(wiki, target).kept  # never replace a file we did not write
    with pytest.raises(ValueError):
        plan(wiki, target, prefix="Bad Prefix")
    with pytest.raises(ValueError):
        plan_export([], wiki, [], target, namespace="Bad")


def test_export_cli_dry_run_json_and_bundle(capsys, project_root: Path, tmp_path: Path) -> None:
    project_wiki(project_root)
    target = llm_wiki(tmp_path / "base")
    code, out = run(project_root, capsys, "wiki", "export", str(target), "--namespace", "myapp",
                    "--dry-run", "--json")
    assert code == 1 and json.loads(out)["collisions"] == ["deploy"]
    code, out = run(project_root, capsys, "wiki", "export", str(target), "--namespace", "myapp")
    assert code == 1 and "--prefix" in out
    code, out = run(project_root, capsys, "wiki", "export", str(target), "--namespace", "myapp",
                    "--prefix", "app", "--dry-run")
    assert code == 0 and "dry run" in out and not (target / "myapp").exists()
    code, out = run(project_root, capsys, "wiki", "export", str(target), "--namespace", "myapp",
                    "--prefix", "app", "--json")
    assert code == 0 and json.loads(out)["done"]
    assert run(project_root, capsys, "wiki", "export")[0] == 2
    assert run(project_root, capsys, "wiki", "export", str(target), "--namespace", "Bad")[0] == 2
    code, out = run(project_root, capsys, "wiki", "export", "--format", "json")
    data = json.loads(out)
    assert data["format"] == "kdx-wiki/1" and data["raw"] == ["raw/notes.md"]
    assert {page["name"] for page in data["pages"]} == {"notes", "mcp", "deploy"}
    code, out = run(project_root, capsys, "wiki", "export", str(tmp_path / "v"), "--internal",
                    "--namespace", "notes")
    assert code == 0 and "leave the project" in out
    assert bundle([], project="p", scope="public")["pages"] == []

"""GUI manifest and preferences: modules describe their GUI as data, people keep their choices."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.constants import ExitCode
from korodexios_core.kernel.ipc import IpcRequest
from korodexios_core.kernel.ui_manifest import build_manifest
from korodexios_core.kernel.ui_prefs import PrefsError, PrefsStore, empty_prefs, validate_prefs
from korodexios_core.modules.web_console.module import (
    MANIFEST_PATH,
    PREFS_PATH,
    STATIC_DIR,
)

from .test_ipc import base_args
from .test_web_console import console_of, ipc_chassis, request_for

COMMANDS = {"queue purge", "queue submit"}


def one(declaration: Any, **kwargs: Any) -> dict[str, Any]:
    return build_manifest([("demo", declaration)], known_commands=COMMANDS, **kwargs)


# -- manifest ------------------------------------------------------------------


def test_a_valid_declaration_becomes_a_module_entry() -> None:
    manifest = one(
        {
            "title": "Demo",
            "order": 5,
            "panels": [
                {"id": "rows", "type": "table", "source": "/api/v1/queue", "path": "tasks",
                 "columns": ["id", {"key": "state", "label": "State"}], "refresh": 5},
                {"id": "info", "type": "text", "text": "hello"},
                {"id": "go", "type": "form", "command": "queue submit", "args": {"kind": "x"}},
                {"id": "buttons", "type": "actions",
                 "buttons": [{"id": "purge", "label": "Purge", "command": "queue purge",
                              "confirm": True}]},
            ],
        }
    )
    assert manifest["issues"] == []
    (entry,) = manifest["modules"]
    assert (entry["module"], entry["title"], entry["order"]) == ("demo", "Demo", 5)
    rows = entry["panels"][0]
    assert rows["columns"] == [{"key": "id", "label": "id"}, {"key": "state", "label": "State"}]
    assert rows["refresh"] == 5
    assert entry["panels"][2]["args"] == {"kind": "x"}
    assert entry["panels"][3]["buttons"][0]["confirm"] is True


def test_a_broken_panel_is_dropped_and_reported_not_fatal() -> None:
    manifest = one(
        {
            "panels": [
                {"id": "ok", "type": "text", "text": "fine"},
                {"id": "Bad Id", "type": "text", "text": "x"},
                {"id": "kind", "type": "carousel"},
                {"id": "far", "type": "table", "source": "http://evil.example/x"},
                {"id": "up", "type": "table", "source": "/api/v1/../secret"},
                {"id": "cmd", "type": "form", "command": "rm -rf"},
                {"id": "ghost", "type": "form", "command": "not a command"},
                {"id": "ok", "type": "text", "text": "dup"},
                {"id": "empty", "type": "actions", "buttons": []},
            ]
        }
    )
    (entry,) = manifest["modules"]
    assert [panel["id"] for panel in entry["panels"]] == ["ok"]
    problems = " | ".join(issue["message"] for issue in manifest["issues"])
    for needle in ("id like", "unknown panel type", "control-plane route", "not a command path",
                   "not a runnable command", "duplicate panel id", "at least one valid button"):
        assert needle in problems, needle
    assert all(issue["module"] == "demo" for issue in manifest["issues"])


def test_modules_without_a_usable_ui_have_no_entry() -> None:
    assert one(None)["modules"] == []
    hidden = {"nav": False, "panels": [{"id": "a", "type": "text", "text": "x"}]}
    assert one(hidden)["modules"] == []
    none_valid = one({"panels": [{"id": "a", "type": "nope"}]})
    assert none_valid["modules"] == []
    assert any("no valid panel" in issue["message"] for issue in none_valid["issues"])
    assert "must be a table" in one("text")["issues"][0]["message"]


def test_entries_sort_by_order_then_name_and_translation_is_applied() -> None:
    panel = [{"id": "a", "type": "text", "text": "hello", "title": "Greeting"}]
    manifest = build_manifest(
        [
            ("zeta", {"order": 1, "panels": panel, "i18n": "demo"}),
            ("alpha", {"order": 2, "panels": panel}),
            ("beta", {"order": 2, "panels": panel}),
        ],
        translate=lambda message, domain: f"{domain}:{message}".upper(),
        locale="xx",
    )
    assert [entry["module"] for entry in manifest["modules"]] == ["zeta", "alpha", "beta"]
    assert manifest["modules"][0]["panels"][0]["title"] == "DEMO:GREETING"
    assert manifest["modules"][1]["panels"][0]["title"] == "Greeting"  # no i18n domain declared
    assert manifest["locale"] == "xx"


# -- preferences ---------------------------------------------------------------


def test_prefs_are_remembered_merged_and_reset(tmp_path: Path) -> None:
    store = PrefsStore(tmp_path / "ui")
    assert store.load("ann") == empty_prefs()
    store.patch("ann", {"values": {"queue.submit": {"kind": "a", "delay": 2}}})
    store.patch("ann", {"values": {"queue.submit": {"kind": "b", "delay": None}}})
    store.patch("ann", {"defaults": {"queue.submit": {"kind": "d"}}})
    prefs = store.patch(
        "ann", {"settings": {"theme": "dark"}, "panels": {"q.tasks": {"label": "T"}}}
    )
    assert prefs["values"] == {"queue.submit": {"kind": "b"}}
    assert (tmp_path / "ui" / "prefs-ann.json").is_file()
    assert store.load("bob") == empty_prefs()  # each person has their own file
    forgotten = store.reset("ann")
    assert forgotten["values"] == {} and forgotten["panels"] == {}
    assert forgotten["defaults"] == {"queue.submit": {"kind": "d"}}  # pinned defaults survive
    assert store.reset("ann", ("defaults",))["defaults"] == {}
    store.patch("ann", {"values": {"queue.submit": None}})


def test_prefs_buttons_replace_the_list_and_are_validated(tmp_path: Path) -> None:
    store = PrefsStore(tmp_path)
    button = {"id": "purge-old", "label": "Purge", "command": "queue purge",
              "args": {"days": 30, "dry_run": True}, "confirm": True}
    assert store.patch(None, {"buttons": [button]})["buttons"][0]["args"] == {
        "days": 30, "dry_run": True}
    assert store.patch(None, {"buttons": []})["buttons"] == []
    for bad in (
        {"buttons": [{"id": "x", "label": "x", "command": "rm -rf /"}]},
        {"buttons": [{"id": "X!", "label": "x", "command": "queue purge"}]},
        {"buttons": [button, button]},
        {"buttons": [{"id": "x", "label": "x", "command": "queue purge", "args": {"a": [1]}}]},
        {"settings": {"colour": "red"}},
        {"nope": {}},
        {"values": {"Bad Key": {"a": 1}}},
        {"panels": {"q.p": {"order": "first"}}},
    ):
        with pytest.raises(PrefsError):
            store.patch(None, bad)
    with pytest.raises(PrefsError):
        validate_prefs({"values": {"k": {"a": "x" * 70000}}})
    with pytest.raises(PrefsError):
        validate_prefs([])


def test_a_damaged_prefs_file_is_set_aside_not_fatal(tmp_path: Path) -> None:
    store = PrefsStore(tmp_path)
    path = store.path_for("ann")
    path.write_text("{not json", encoding="utf-8")
    assert store.load("ann") == empty_prefs()
    assert path.with_suffix(".bad").is_file() and not path.exists()
    path.write_text(json.dumps({"settings": {"colour": "red"}}), encoding="utf-8")
    assert store.load("ann") == empty_prefs()


def test_user_names_cannot_escape_the_directory(tmp_path: Path) -> None:
    store = PrefsStore(tmp_path / "ui")
    path = store.path_for("../../etc/passwd")
    assert path.parent == tmp_path / "ui"
    assert store.path_for("") == store.path_for(None)


# -- the control plane and the terminal ------------------------------------------


def test_routes_serve_the_manifest_and_the_callers_prefs(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        console = console_of(chassis)
        manifest = console._manifest_route(request_for(MANIFEST_PATH))
        queue = next(entry for entry in manifest["modules"] if entry["module"] == "job_queue")
        assert {panel["type"] for panel in queue["panels"]} == {
            "keyvalue", "table", "form", "actions", "settings"}
        assert manifest["issues"] == []
        tasks = next(panel for panel in queue["panels"] if panel["id"] == "tasks")
        assert [action["id"] for action in tasks["actions"]] == ["retry", "cancel"]
        assert tasks["actions"][0]["args"] == {"task_id": "{id}"}
        assert tasks["detail"]["source"] == "/api/v1/queue?id={id}"
        assert next(c for c in tasks["columns"] if c["key"] == "state")["kind"] == "chip"

        def call(method: str, payload: Any = None, **query: str) -> Any:
            return console._prefs_route(IpcRequest(
                method=method, path=PREFS_PATH, payload=payload,
                query={key: [value] for key, value in query.items()}))

        assert call("GET") == empty_prefs()
        assert call("PATCH", {"settings": {"last_tab": "mod-job_queue"}})["settings"] == {
            "last_tab": "mod-job_queue"}
        assert call("PUT", {"panels": {"job_queue.tasks": {"hidden": True}}})["settings"] == {}
        status, body = call("PATCH", {"settings": {"colour": "red"}})
        assert status == 400 and "unknown setting" in body["error"]
        assert call("DELETE")["panels"] == {}
    finally:
        chassis.shutdown()


def test_a_module_can_declare_its_ui_in_code_and_a_bug_is_contained(
    project_root, monkeypatch
) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        console = console_of(chassis)
        record = chassis.registry.require("heartbeat")
        # module instances are process-wide singletons: patch them so the change is undone
        monkeypatch.setattr(record.module, "ui_spec", lambda: {
            "title": "Beat", "panels": [{"id": "n", "type": "text", "text": "hi"}]})
        broken = chassis.registry.require("system_info")

        def boom() -> Any:
            raise RuntimeError("bad spec")

        monkeypatch.setattr(broken.module, "ui_spec", boom)
        manifest = console.manifest()
        assert "heartbeat" in [entry["module"] for entry in manifest["modules"]]
        assert any(i["module"] == "system_info" and "bad spec" in i["message"]
                   for i in manifest["issues"])
    finally:
        chassis.shutdown()


def test_terminal_reads_the_manifest_and_edits_prefs(project_root, capsys) -> None:
    args = base_args(project_root)
    assert main([*args, "ui", "manifest", "--json"]) == int(ExitCode.SUCCESS)
    manifest = json.loads(_json_part(capsys.readouterr().out))
    assert "job_queue" in [entry["module"] for entry in manifest["modules"]]

    assert main([*args, "ui", "manifest"]) == int(ExitCode.SUCCESS)
    assert "Latest tasks" in capsys.readouterr().out

    change = json.dumps({"defaults": {"job_queue.submit": {"kind": "demo"}}})
    assert main([*args, "ui", "prefs", "patch", change, "--user", "ann", "--json"]) == 0
    assert json.loads(_json_part(capsys.readouterr().out))["defaults"] == {
        "job_queue.submit": {"kind": "demo"}}
    assert main([*args, "ui", "prefs", "show", "--user", "ann"]) == 0
    assert "demo" in capsys.readouterr().out
    assert main([*args, "ui", "prefs", "patch", "{broken", "--user", "ann"]) == int(
        ExitCode.USAGE_ERROR)
    assert main([*args, "ui", "prefs", "reset", "nonsense", "--user", "ann"]) == int(
        ExitCode.USAGE_ERROR)
    assert main([*args, "ui", "prefs", "reset", "defaults", "--user", "ann", "--json"]) == 0
    assert json.loads(_json_part(capsys.readouterr().out))["defaults"] == {}


def _json_part(output: str) -> str:
    """Return the JSON document of a command's output (logs may precede it)."""
    start = output.index("{")
    return output[start:output.rindex("}") + 1]


def test_the_page_loads_modules_js_and_never_writes_html() -> None:
    page = (STATIC_DIR / "console.html").read_text(encoding="utf-8")
    assert 'src="modules.js"' in page
    script = (STATIC_DIR / "modules.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script
    assert "eval(" not in script


def test_the_prefs_store_is_shared_so_concurrent_changes_are_not_lost(project_root) -> None:
    import threading

    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        console = console_of(chassis)
        assert console.prefs is console.prefs  # one store, one lock

        def patch(index: int) -> None:
            console.prefs.patch("ann", {"values": {f"form.f{index}": {"a": index}}})

        threads = [threading.Thread(target=patch, args=(i,)) for i in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(console.prefs.load("ann")["values"]) == 12  # no update overwrote another
        assert not list((project_root / "data" / "ui").glob("*.tmp"))
    finally:
        chassis.shutdown()


def test_a_module_without_a_ui_spec_hook_is_skipped_not_reported(project_root, monkeypatch) -> None:
    from korodexios_core.kernel.contract import Module

    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        monkeypatch.delattr(Module, "ui_spec")  # a module that only satisfies ModuleProtocol
        manifest = console_of(chassis).manifest()
        assert [i for i in manifest["issues"] if "ui_spec" in i["message"]] == []
    finally:
        chassis.shutdown()


# -- typed cells, row actions, detail, form fields, settings (0.6.3) -----------------


def panel_of(declaration: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    manifest = one({"panels": [declaration]})
    problems = [issue["message"] for issue in manifest["issues"]]
    modules = manifest["modules"]
    return (modules[0]["panels"][0] if modules else {}), problems


def test_table_columns_carry_a_kind_and_tones_and_stay_plain_without_one() -> None:
    panel, problems = panel_of(
        {"id": "jobs", "type": "table", "source": "/api/v1/queue", "path": "tasks",
         "columns": [
             "id",
             {"key": "state", "kind": "chip", "tones": {"done": "ok", "weird": "loud"}},
             {"key": "size", "kind": "bytes"},
             {"key": "age", "kind": "sparkle"},
             {"key": "note", "tones": {"x": "ok"}},  # tones mean nothing off a chip
         ]}
    )
    assert panel["columns"][0] == {"key": "id", "label": "id"}  # unchanged shape
    assert panel["columns"][1] == {"key": "state", "label": "state", "kind": "chip",
                                   "tones": {"done": "ok"}}
    assert panel["columns"][2]["kind"] == "bytes"
    assert "kind" not in panel["columns"][3] and "tones" not in panel["columns"][4]
    joined = " | ".join(problems)
    assert "tone 'loud'" in joined or "loud" in joined
    assert "sparkle" in joined


def test_row_actions_use_column_placeholders_and_known_commands() -> None:
    panel, problems = panel_of(
        {"id": "jobs", "type": "table", "source": "/api/v1/queue", "path": "tasks",
         "actions": [
             {"id": "purge", "label": "Purge", "command": "queue purge",
              "args": {"id": "{id}", "tag": "job-{meta.tag}"}, "confirm": True, "danger": True},
             {"id": "ghost", "command": "no such thing"},
             {"id": "bad", "command": "queue purge", "args": {"id": "{id"}},
             {"id": "worse", "command": "queue purge", "args": {"id": "{a b}"}},
             {"id": "purge", "command": "queue purge"},
             "not a table",
         ]}
    )
    assert [action["id"] for action in panel["actions"]] == ["purge"]
    action = panel["actions"][0]
    assert action["args"] == {"id": "{id}", "tag": "job-{meta.tag}"}
    assert (action["confirm"], action["danger"]) == (True, True)
    joined = " | ".join(problems)
    assert "not a runnable command" in joined or "not a command path" in joined
    assert "unbalanced" in joined and "not a column name" in joined and "unique id" in joined


def test_row_actions_are_capped() -> None:
    many = [{"id": f"a{n}", "command": "queue purge"} for n in range(12)]
    panel, _ = panel_of({"id": "jobs", "type": "table", "source": "/api/v1/queue",
                         "actions": many})
    assert len(panel["actions"]) == 8


def test_a_table_detail_is_a_route_with_placeholders_or_it_is_dropped() -> None:
    panel, problems = panel_of(
        {"id": "jobs", "type": "table", "source": "/api/v1/queue",
         "detail": {"source": "/api/v1/queue?id={id}", "path": "task"}}
    )
    assert panel["detail"] == {"source": "/api/v1/queue?id={id}", "path": "task"}
    assert problems == []
    for bad in ("http://evil.example/{id}", "/api/v1/../x/{id}", "/api/v1/q/{a b}", "/api/v1/{id"):
        panel, problems = panel_of(
            {"id": "jobs", "type": "table", "source": "/api/v1/queue", "detail": {"source": bad}}
        )
        assert "detail" not in panel and problems, bad


def test_form_fields_name_where_choices_come_from_and_how_to_draw() -> None:
    panel, problems = panel_of(
        {"id": "go", "type": "form", "command": "queue submit",
         "fields": {
             "model": {"choices_from": "/api/v1/models", "path": "items",
                       "value": "id", "label": "name"},
             "body": {"kind": "textarea", "rows": 99},
             "level": {"kind": "slider"},
             "odd": {"kind": "carousel"},
             "far": {"choices_from": "http://evil.example/x"},
             "Bad Name": {"kind": "text"},
         }}
    )
    fields = panel["fields"]
    assert fields["model"] == {"kind": "select", "choices_from": "/api/v1/models",
                               "path": "items", "value": "id", "label": "name"}
    assert fields["body"] == {"kind": "textarea", "rows": 20}  # capped
    assert fields["level"] == {"kind": "slider"}
    assert set(fields) == {"model", "body", "level"}
    joined = " | ".join(problems)
    assert "carousel" in joined and "choices_from" in joined and "Bad Name" in joined


def test_settings_panel_points_at_the_schema_route() -> None:
    panel, problems = panel_of({"id": "prefs", "type": "settings", "module": "heartbeat"})
    assert (panel["source"], panel["module"], panel["group"]) == (
        "/api/v1/ui/config/schema", "heartbeat", "")
    assert problems == []
    panel, _ = panel_of({"id": "prefs", "type": "settings", "group": "core.log"})
    assert panel["group"] == "core.log"
    for bad in ({}, {"module": "Bad Module"}, {"group": "core log!"}):
        panel, problems = panel_of({"id": "prefs", "type": "settings", **bad})
        assert panel == {} and "settings panel needs" in " ".join(problems), bad


def test_the_console_scripts_stay_free_of_innerhtml_and_unsafe_links() -> None:
    script = (STATIC_DIR / "modules.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script
    assert "safeHref" in script  # a link cell never follows a javascript: address


# -- embed panel -----------------------------------------------------------------


def test_an_embed_panel_is_a_path_on_this_daemon_with_a_clamped_height() -> None:
    panel = {"id": "player", "type": "embed", "src": "/player/", "height": 9000}
    manifest = one({"panels": [panel]})
    built = manifest["modules"][0]["panels"][0]
    assert (built["src"], built["height"]) == ("/player/", 2000)
    manifest = one({"panels": [{"id": "player", "type": "embed", "src": "/player/"}]})
    assert manifest["modules"][0]["panels"][0]["height"] == 360


@pytest.mark.parametrize(
    "src", ["", "player", "//evil.example/x", "https://evil.example/", "/a/../b", "/api/v1/status"]
)
def test_an_embed_panel_refuses_anything_but_a_local_mount(src: str) -> None:
    manifest = one({"panels": [{"id": "player", "type": "embed", "src": src}]})
    assert manifest["issues"]
    assert not manifest["modules"]


def test_the_shell_may_frame_embeds_but_the_pages_stay_unframable() -> None:
    shell = (STATIC_DIR / "shell.html").read_text(encoding="utf-8")
    assert "frame-src 'self'" in shell
    assert "frame-ancestors" not in shell  # a meta CSP ignores it; the header says 'none'

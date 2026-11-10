"""UI manifest: how a module describes its part of the GUI, with no GUI code.

A module declares a ``[ui]`` table in ``kdx_module.toml`` (or returns the same shape from
``Module.ui_spec()``). The chassis validates it and serves one manifest, so a generic
renderer (the web console, or a project's own React app) draws the navigation entry and
the panels of every module that is loaded - and a module that is not loaded has no entry.

Panel types (every field is plain data; nothing here runs in a browser)::

    table      rows from a control-plane route          source, path, columns
    keyvalue   one object as label/value pairs          source, path
    log        text lines from a route                  source, path
    text       static text                              text
    form       a module command with a form             command, args, fields
    actions    buttons that run commands                buttons
    settings   typed form of declared config keys       module or group

A ``table`` column may carry a ``kind`` (``chip`` with optional ``tones``, ``bytes``,
``duration``, ``time``, ``link``, ``code``), the table may list per-row ``actions`` whose
``args`` use ``{column}`` placeholders, and a ``detail`` route opened from a row. A ``form``
may say, per argument, where its choices come from (``choices_from``) or how it is drawn
(``textarea``, ``number``, ``slider``; limits come from the command's own argument).
Nothing here is code: the renderer decides colours and widgets.

A shell (the window with a ribbon, side list, bottom panel and inspector) reads three more
optional declarations: ``position = "bottom"`` and ``badge`` (a route returning a count) on
the ``[ui]`` table place and decorate the module's ribbon entry; ``[[ui.sections]]`` give the
side list one collapsible tree each (``id``, ``title``, ``tree`` route, ``empty`` text; the
route answers with the nodes of ``kernel/ui_tree.py``); and a panel may say ``dock = "bottom"``
(shown in the bottom panel while the person navigates) or ``locked = true`` (never hidden).

The manifest is a contract for humans, agents and tests alike: ``kdx ui manifest --json``
prints exactly what the browser receives. A broken declaration never takes the GUI down:
the offending panel (or module entry) is dropped and listed under ``issues``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import ui_embed

MANIFEST_VERSION = 1
PANEL_TYPES = ("table", "keyvalue", "log", "text", "form", "actions", "settings", "embed")
CELL_KINDS = ("text", "chip", "bytes", "duration", "time", "link", "code")
TONES = ("ok", "warn", "bad", "busy", "muted")
FIELD_KINDS = ("text", "textarea", "number", "slider", "select")
ROUTE_PREFIX = "/api/v1/"
NODE_PLACEHOLDER = "node"
"""``{node}`` in a panel source, detail source or argument: the id of the tree node selected."""
DETAIL_KINDS = ("text", "line")
SETTINGS_SOURCE = "/api/v1/ui/config/schema"
MAX_PANELS = 64
MAX_SECTIONS = 8
AREA_POSITIONS = ("top", "bottom")
DOCKS = ("main", "bottom")
MAX_BUTTONS = 32
MAX_ROW_ACTIONS = 8
MAX_FIELDS = 32
MAX_TONES = 32
MAX_REFRESH_SECONDS = 3600
_IDENT = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_PATH = re.compile(r"^[A-Za-z0-9_.\-]{0,128}$")
_COMMAND = re.compile(r"^[a-z][a-z0-9_-]*( [a-z][a-z0-9_-]*)*$")
_ROUTE = re.compile(r"^/[A-Za-z0-9_\-./?=&]*$")
_TEMPLATE_ROUTE = re.compile(r"^/[A-Za-z0-9_\-./{}?=&]*$")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_COLUMN = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

Translator = Callable[[str, str], str]
"""``(message, domain) -> translated`` - the manifest asks it for every visible string."""


@dataclass(slots=True)
class UiIssue:
    """One thing a declaration got wrong, and where."""

    module: str
    message: str
    panel: str = ""

    def as_dict(self) -> dict[str, str]:
        """Return the JSON view."""
        view = {"module": self.module, "message": self.message}
        if self.panel:
            view["panel"] = self.panel
        return view


@dataclass(slots=True)
class _Build:
    module: str
    known_commands: frozenset[str] | None
    translate: Translator
    domain: str
    issues: list[UiIssue] = field(default_factory=list)

    def issue(self, message: str, panel: str = "") -> None:
        self.issues.append(UiIssue(self.module, message, panel))

    def say(self, value: Any) -> str:
        text = str(value or "").strip()
        return self.translate(text, self.domain) if text and self.domain else text


def build_manifest(
    declarations: Iterable[tuple[str, Any]],
    *,
    known_commands: Iterable[str] | None = None,
    translate: Translator | None = None,
    locale: str = "",
) -> dict[str, Any]:
    """Validate every module's ``[ui]`` declaration and return the manifest.

    *declarations* yields ``(module name, declaration or None)`` in load order.
    *known_commands* are the runnable command paths (``"queue purge"``); a panel naming
    a command outside that set is dropped, so a button never points at nothing.
    """
    commands = frozenset(known_commands) if known_commands is not None else None
    say: Translator = translate or (lambda message, _domain: message)
    entries: list[dict[str, Any]] = []
    issues: list[UiIssue] = []
    for name, declaration in declarations:
        if not declaration:
            continue
        build = _Build(name, commands, say, "")
        entry = _module_entry(name, declaration, build)
        issues.extend(build.issues)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda item: (item["order"], item["module"]))
    return {
        "version": MANIFEST_VERSION,
        "locale": locale,
        "modules": entries,
        "issues": [issue.as_dict() for issue in issues],
    }


def _module_entry(name: str, declaration: Any, build: _Build) -> dict[str, Any] | None:
    if not isinstance(declaration, Mapping):
        build.issue("[ui] must be a table")
        return None
    if declaration.get("nav", True) is False:
        return None
    build.domain = str(declaration.get("i18n") or "").strip()
    raw_panels = declaration.get("panels", ())
    if not isinstance(raw_panels, list | tuple):
        build.issue("ui.panels must be an array of tables")
        return None
    panels: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_panels[:MAX_PANELS]):
        panel = _panel(raw, index, build, seen)
        if panel is not None:
            panels.append(panel)
    if len(raw_panels) > MAX_PANELS:
        build.issue(f"more than {MAX_PANELS} panels; the rest were ignored")
    sections = _sections(declaration.get("sections", ()), build)
    if not panels and not sections:
        if raw_panels:
            build.issue("no valid panel is left, so the module has no GUI entry")
        return None
    entry: dict[str, Any] = {
        "module": name,
        "title": build.say(declaration.get("title") or name),
        "icon": _short(declaration.get("icon")),
        "order": _int(declaration.get("order"), 100),
        "panels": panels,
    }
    # shell placement is optional and only present when declared, so older renderers see
    # the same shape they always did
    position = str(declaration.get("position") or "top")
    if position == "bottom":
        entry["position"] = position
    elif position != "top":
        build.issue(f"ui.position {position!r} is not one of {', '.join(AREA_POSITIONS)}")
    badge = _route_or_issue(declaration.get("badge"), "ui.badge", build, "")
    if badge:
        entry["badge"] = badge
    if sections:
        entry["sections"] = sections
    return entry


def _route_or_issue(value: Any, what: str, build: _Build, ident: str) -> str:
    """Return *value* when it is a control-plane route, else "" (with an issue when set)."""
    route = str(value or "")
    if not route:
        return ""
    if not route.startswith(ROUTE_PREFIX) or ".." in route or "//" in route or not _ROUTE.match(
        route
    ):
        build.issue(f"{what} must be a control-plane route under {ROUTE_PREFIX}", ident)
        return ""
    return route


def _sections(raw: Any, build: _Build) -> list[dict[str, Any]]:
    """Validate ``[[ui.sections]]``: each is one collapsible tree in the side list."""
    if raw in (None, (), []):
        return []
    if not isinstance(raw, list | tuple):
        build.issue("ui.sections must be an array of tables")
        return []
    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw[:MAX_SECTIONS]):
        if not isinstance(item, Mapping):
            build.issue(f"section #{index + 1} must be a table")
            continue
        ident = str(item.get("id") or "")
        if not _IDENT.match(ident) or ident in seen:
            build.issue(f"section #{index + 1} needs a unique id like 'cases'")
            continue
        tree = _route_or_issue(item.get("tree"), "section tree", build, ident)
        if not tree:
            build.issue("section needs a tree route that returns nodes", ident)
            continue
        seen.add(ident)
        sections.append(
            {
                "id": ident,
                "title": build.say(item.get("title") or ident),
                "tree": tree,
                "empty": build.say(item.get("empty")),
            }
        )
    if len(raw) > MAX_SECTIONS:
        build.issue(f"more than {MAX_SECTIONS} sections; the rest were ignored")
    return sections


def _panel(
    raw: Any, index: int, build: _Build, seen: set[str]
) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        build.issue(f"panel #{index + 1} must be a table")
        return None
    ident = str(raw.get("id") or "")
    if not _IDENT.match(ident):
        build.issue(f"panel #{index + 1} needs an id like 'tasks' (lowercase, - or _)")
        return None
    if ident in seen:
        build.issue("duplicate panel id", ident)
        return None
    kind = str(raw.get("type") or "")
    if kind not in PANEL_TYPES:
        build.issue(f"unknown panel type {kind!r}; use one of {', '.join(PANEL_TYPES)}", ident)
        return None
    panel: dict[str, Any] = {
        "id": ident,
        "type": kind,
        "title": build.say(raw.get("title") or ident),
        "help": build.say(raw.get("help")),
        "refresh": min(MAX_REFRESH_SECONDS, max(0, _int(raw.get("refresh"), 0))),
    }
    dock = str(raw.get("dock") or "main")
    if dock == "bottom":
        panel["dock"] = dock  # shown in the bottom panel, wherever the person navigates
    elif dock != "main":
        build.issue(f"dock {dock!r} is not one of {', '.join(DOCKS)}", ident)
    if raw.get("locked") is True:
        panel["locked"] = True  # the shell must not let a person hide this panel
    ok = _TYPES[kind](raw, panel, build, ident)
    if not ok:
        return None
    seen.add(ident)
    return panel


def _source(raw: Mapping[str, Any], panel: dict[str, Any], build: _Build, ident: str) -> bool:
    source = str(raw.get("source") or "")
    if (
        not source.startswith(ROUTE_PREFIX)
        or ".." in source
        or "//" in source
        or not _TEMPLATE_ROUTE.match(source)
        or any(ch in _PLACEHOLDER.sub("", source) for ch in "{}")
        or any(name != NODE_PLACEHOLDER for name in _PLACEHOLDER.findall(source))
    ):
        build.issue(
            f"source must be a control-plane route under {ROUTE_PREFIX} "
            f"(the only placeholder allowed is {{{NODE_PLACEHOLDER}}})",
            ident,
        )
        return False
    path = str(raw.get("path") or "")
    if not _PATH.match(path):
        build.issue("path must be a dotted key such as 'tasks'", ident)
        return False
    panel["source"] = source
    panel["path"] = path
    return True


def _tones(value: Any, build: _Build, ident: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    tones: dict[str, str] = {}
    for word, tone in list(value.items())[:MAX_TONES]:
        if str(tone) in TONES:
            tones[str(word)] = str(tone)
        else:
            build.issue(f"tone {tone!s} for {word!s} is not one of {', '.join(TONES)}", ident)
    return tones


def _column(
    item: Any, build: _Build, ident: str
) -> dict[str, Any] | None:
    if isinstance(item, Mapping):
        key, label = str(item.get("key") or ""), build.say(item.get("label") or item.get("key"))
        kind = str(item.get("kind") or "text")
    else:
        key, label, kind = str(item), build.say(item), "text"
    if not key or not _PATH.match(key):
        return None
    if kind not in CELL_KINDS:
        build.issue(f"column {key!r}: kind {kind!r} is not one of {', '.join(CELL_KINDS)}", ident)
        kind = "text"
    column: dict[str, Any] = {"key": key, "label": label}
    if kind != "text":
        column["kind"] = kind
    if kind == "chip" and isinstance(item, Mapping):
        tones = _tones(item.get("tones"), build, ident)
        if tones:
            column["tones"] = tones
    return column


def _placeholders_ok(text: str, build: _Build, ident: str, what: str) -> bool:
    """Return True when every ``{name}`` in *text* names a column; a stray brace is refused."""
    stripped = _PLACEHOLDER.sub("", text)
    if "{" in stripped or "}" in stripped:
        build.issue(f"{what} has an unbalanced brace", ident)
        return False
    for name in _PLACEHOLDER.findall(text):
        if not _COLUMN.match(name):
            build.issue(f"{what}: {{{name}}} is not a column name", ident)
            return False
    return True


def _row_actions(raw: Any, build: _Build, ident: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    declared = raw if isinstance(raw, list | tuple) else ()
    for index, item in enumerate(declared[:MAX_ROW_ACTIONS]):
        if not isinstance(item, Mapping):
            build.issue(f"row action #{index + 1} must be a table", ident)
            continue
        action_id = str(item.get("id") or "")
        if not _IDENT.match(action_id) or any(a["id"] == action_id for a in actions):
            build.issue(f"row action #{index + 1} needs a unique id", ident)
            continue
        command = _command_ok(item.get("command"), build, ident)
        args = _args(item.get("args"))
        if command is None or not all(
            _placeholders_ok(value, build, ident, f"row action {action_id!r} argument {name!r}")
            for name, value in args.items()
            if isinstance(value, str)
        ):
            continue
        actions.append(
            {
                "id": action_id,
                "label": build.say(item.get("label") or action_id),
                "command": command,
                "args": args,
                "confirm": bool(item.get("confirm", False)),
                "danger": bool(item.get("danger", False)),
            }
        )
    return actions


def _detail_kinds(raw: Any, build: _Build, ident: str) -> dict[str, str]:
    """Return ``{field: "text"|"line"}``: ``text`` is always a wrapped block, ``line`` never."""
    if not isinstance(raw, Mapping):
        return {}
    kinds: dict[str, str] = {}
    for key, kind in list(raw.items())[:MAX_FIELDS]:
        if _PATH.match(str(key)) and str(key) and str(kind) in DETAIL_KINDS:
            kinds[str(key)] = str(kind)
        else:
            known = ", ".join(DETAIL_KINDS)
            build.issue(f"detail kind {kind!s} for {key!s} is not one of {known}", ident)
    return kinds


def _detail(raw: Any, build: _Build, ident: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    source = str(raw.get("source") or "") if isinstance(raw, Mapping) else ""
    path = str(raw.get("path") or "") if isinstance(raw, Mapping) else ""
    if (
        not source.startswith(ROUTE_PREFIX)
        or ".." in source
        or "//" in source
        or not _TEMPLATE_ROUTE.match(source)
        or not _PATH.match(path)
        or not _placeholders_ok(source, build, ident, "detail source")
    ):
        build.issue(
            f"detail needs a source under {ROUTE_PREFIX} (it may hold {{column}} placeholders)",
            ident,
        )
        return None
    detail: dict[str, Any] = {"source": source, "path": path}
    kinds = _detail_kinds(raw.get("kinds"), build, ident)
    if kinds:
        detail["kinds"] = kinds
    return detail


def _table(raw: Mapping[str, Any], panel: dict[str, Any], build: _Build, ident: str) -> bool:
    if not _source(raw, panel, build, ident):
        return False
    columns: list[dict[str, Any]] = []
    declared = raw.get("columns", ())
    for item in declared if isinstance(declared, list | tuple) else ():
        column = _column(item, build, ident)
        if column is not None:
            columns.append(column)
    panel["columns"] = columns
    actions = _row_actions(raw.get("actions"), build, ident)
    if actions:
        panel["actions"] = actions
    detail = _detail(raw.get("detail"), build, ident)
    if detail is not None:
        panel["detail"] = detail
    return True


def _text(raw: Mapping[str, Any], panel: dict[str, Any], build: _Build, ident: str) -> bool:
    text = build.say(raw.get("text"))
    if not text:
        build.issue("a text panel needs 'text'", ident)
        return False
    panel["text"] = text
    return True


def _command_ok(command: Any, build: _Build, ident: str) -> str | None:
    text = str(command or "")
    if not _COMMAND.match(text):
        build.issue(f"command {text!r} is not a command path like 'queue purge'", ident)
        return None
    if build.known_commands is not None and text not in build.known_commands:
        build.issue(f"command {text!r} is not a runnable command of this chassis", ident)
        return None
    return text


def _args(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, str | int | float | bool) or item is None
    }


def _form(raw: Mapping[str, Any], panel: dict[str, Any], build: _Build, ident: str) -> bool:
    command = _command_ok(raw.get("command"), build, ident)
    if command is None:
        return False
    panel["command"] = command
    panel["args"] = _args(raw.get("args"))
    fields = _fields(raw.get("fields"), build, ident)
    if fields:
        panel["fields"] = fields
    return True


def _fields(raw: Any, build: _Build, ident: str) -> dict[str, dict[str, Any]]:
    """Validate ``[ui.panels.fields.<argument>]`` tables: how one argument is drawn."""
    if not isinstance(raw, Mapping):
        return {}
    fields: dict[str, dict[str, Any]] = {}
    for dest, spec in list(raw.items())[:MAX_FIELDS]:
        name = str(dest)
        if not _IDENT.match(name) or not isinstance(spec, Mapping):
            build.issue(f"field {name!r} must be a table named after a command argument", ident)
            continue
        kind = str(spec.get("kind") or "")
        if kind and kind not in FIELD_KINDS:
            known = ', '.join(FIELD_KINDS)
            build.issue(f"field {name!r}: kind {kind!r} is not one of {known}", ident)
            continue
        field: dict[str, Any] = {}
        if kind:
            field["kind"] = kind
        source = str(spec.get("choices_from") or "")
        if source:
            value, label = str(spec.get("value") or ""), str(spec.get("label") or "")
            path = str(spec.get("path") or "")
            if (
                not source.startswith(ROUTE_PREFIX)
                or ".." in source
                or "//" in source
                or not _ROUTE.match(source)
                or not all(_PATH.match(part) for part in (value, label, path))
            ):
                build.issue(f"field {name!r}: choices_from must be under {ROUTE_PREFIX}", ident)
                continue
            field.update({"choices_from": source, "path": path, "value": value, "label": label})
            field.setdefault("kind", "select")
        if spec.get("rows") is not None:
            field["rows"] = min(20, max(1, _int(spec.get("rows"), 4)))
        if field:
            fields[name] = field
    return fields


def _settings(raw: Mapping[str, Any], panel: dict[str, Any], build: _Build, ident: str) -> bool:
    module = str(raw.get("module") or "")
    group = str(raw.get("group") or "")
    if (not module and not group) or (module and not _IDENT.match(module.lower())) or (
        group and not _PATH.match(group)
    ):
        build.issue("a settings panel needs 'module' (a module name) or 'group' (a prefix)", ident)
        return False
    panel["source"] = SETTINGS_SOURCE
    panel["module"] = module
    panel["group"] = group
    return True


def _actions(raw: Mapping[str, Any], panel: dict[str, Any], build: _Build, ident: str) -> bool:
    buttons: list[dict[str, Any]] = []
    declared = raw.get("buttons", ())
    declared = declared if isinstance(declared, list | tuple) else ()
    for index, item in enumerate(declared[:MAX_BUTTONS]):
        if not isinstance(item, Mapping):
            build.issue(f"button #{index + 1} must be a table", ident)
            continue
        button_id = str(item.get("id") or "")
        if not _IDENT.match(button_id) or any(b["id"] == button_id for b in buttons):
            build.issue(f"button #{index + 1} needs a unique id", ident)
            continue
        command = _command_ok(item.get("command"), build, ident)
        if command is None:
            continue
        buttons.append(
            {
                "id": button_id,
                "label": build.say(item.get("label") or button_id),
                "command": command,
                "args": _args(item.get("args")),
                "confirm": bool(item.get("confirm", False)),
                "danger": bool(item.get("danger", False)),
            }
        )
    if not buttons:
        build.issue("an actions panel needs at least one valid button", ident)
        return False
    panel["buttons"] = buttons
    return True


_TYPES: dict[str, Callable[[Mapping[str, Any], dict[str, Any], _Build, str], bool]] = {
    "table": _table,
    "keyvalue": _source,
    "log": _source,
    "text": _text,
    "form": _form,
    "actions": _actions,
    "settings": _settings,
    "embed": ui_embed.declare,
}


def _int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _short(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z0-9_-]{1,32}", text) else ""

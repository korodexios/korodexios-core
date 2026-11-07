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
    form       a module command with a form             command, args
    actions    buttons that run commands                buttons

The manifest is a contract for humans, agents and tests alike: ``kdx ui manifest --json``
prints exactly what the browser receives. A broken declaration never takes the GUI down:
the offending panel (or module entry) is dropped and listed under ``issues``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

MANIFEST_VERSION = 1
PANEL_TYPES = ("table", "keyvalue", "log", "text", "form", "actions")
ROUTE_PREFIX = "/api/v1/"
MAX_PANELS = 64
MAX_BUTTONS = 32
MAX_REFRESH_SECONDS = 3600
_IDENT = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_PATH = re.compile(r"^[A-Za-z0-9_.\-]{0,128}$")
_COMMAND = re.compile(r"^[a-z][a-z0-9_-]*( [a-z][a-z0-9_-]*)*$")
_ROUTE = re.compile(r"^/[A-Za-z0-9_\-./]*$")

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
    if not panels:
        if raw_panels:
            build.issue("no valid panel is left, so the module has no GUI entry")
        return None
    return {
        "module": name,
        "title": build.say(declaration.get("title") or name),
        "icon": _short(declaration.get("icon")),
        "order": _int(declaration.get("order"), 100),
        "panels": panels,
    }


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
        or not _ROUTE.match(source)
    ):
        build.issue(f"source must be a control-plane route under {ROUTE_PREFIX}", ident)
        return False
    path = str(raw.get("path") or "")
    if not _PATH.match(path):
        build.issue("path must be a dotted key such as 'tasks'", ident)
        return False
    panel["source"] = source
    panel["path"] = path
    return True


def _table(raw: Mapping[str, Any], panel: dict[str, Any], build: _Build, ident: str) -> bool:
    if not _source(raw, panel, build, ident):
        return False
    columns: list[dict[str, str]] = []
    declared = raw.get("columns", ())
    for item in declared if isinstance(declared, list | tuple) else ():
        if isinstance(item, Mapping):
            key, label = str(item.get("key") or ""), build.say(item.get("label") or item.get("key"))
        else:
            key, label = str(item), build.say(item)
        if _PATH.match(key) and key:
            columns.append({"key": key, "label": label})
    panel["columns"] = columns
    return True


def _keyvalue(raw: Mapping[str, Any], panel: dict[str, Any], build: _Build, ident: str) -> bool:
    return _source(raw, panel, build, ident)


def _log(raw: Mapping[str, Any], panel: dict[str, Any], build: _Build, ident: str) -> bool:
    return _source(raw, panel, build, ident)


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
    "keyvalue": _keyvalue,
    "log": _log,
    "text": _text,
    "form": _form,
    "actions": _actions,
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

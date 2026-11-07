"""GUI preferences: what a person changed in the interface, remembered on the server.

The browser keeps a copy for speed, but the source of truth is a small JSON file per
person under ``data/ui/``, so the same choices follow them to another browser, another
machine and the terminal (``kdx ui prefs``). One file holds five kinds of choice:

``nav`` / ``panels``
    relabel, hide or reorder a module's entry or a panel (keyed ``module`` and
    ``module.panel``) - the manifest stays the default, this only overlays it;
``buttons``
    the person's own buttons: a label that runs a known command with fixed arguments;
``values``
    the last values typed into each form, restored next time;
``defaults``
    values the person pinned on purpose; a form shows them when nothing is remembered
    and on "Restore defaults", and they survive a reset of the remembered ones;
``settings``
    interface settings such as ``locale``, ``theme`` and ``last_tab``.

Everything is validated and size-capped on the way in; a file that is damaged or edited by
hand into nonsense is ignored (and kept as ``*.bad``) instead of breaking the GUI.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PREFS_VERSION = 1
SECTIONS = ("nav", "panels", "buttons", "values", "defaults", "settings")
MAX_BYTES = 64 * 1024
MAX_ITEMS = 200
MAX_TEXT = 200
DEFAULT_USER = "default"
_KEY = re.compile(r"^[a-z][a-z0-9_.\-]{0,95}$")
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]{0,63}$")
_COMMAND = re.compile(r"^[a-z][a-z0-9_-]*( [a-z][a-z0-9_-]*)*$")
_SETTING_KEYS = ("locale", "theme", "last_tab", "density")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


class PrefsError(ValueError):
    """The preferences sent in were not acceptable; the message says what to fix."""


def empty_prefs() -> dict[str, Any]:
    """Return preferences with nothing chosen."""
    return {
        "version": PREFS_VERSION,
        "nav": {},
        "panels": {},
        "buttons": [],
        "values": {},
        "defaults": {},
        "settings": {},
    }


class PrefsStore:
    """Per-person GUI preferences in ``<directory>/prefs-<name>.json``."""

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._lock = threading.Lock()

    def path_for(self, user: str | None) -> Path:
        """Return the file that holds *user*'s preferences."""
        name = _SAFE_NAME.sub("_", user or DEFAULT_USER)[:64].strip(".") or DEFAULT_USER
        return self._directory / f"prefs-{name}.json"

    def load(self, user: str | None = None) -> dict[str, Any]:
        """Return the stored preferences (a fresh set when none are saved or usable)."""
        with self._lock:
            return self._load(user)

    def replace(self, user: str | None, prefs: Any) -> dict[str, Any]:
        """Validate and store *prefs* as the whole set."""
        clean = validate_prefs(prefs)
        with self._lock:
            self._save(user, clean)
        return clean

    def patch(self, user: str | None, changes: Any) -> dict[str, Any]:
        """Merge *changes* into the stored preferences.

        A section present in *changes* is merged key by key (a ``None`` value deletes
        that key); ``buttons`` is a list, so it replaces the stored list as a whole.
        """
        if not isinstance(changes, Mapping):
            raise PrefsError("preferences must be a JSON object")
        unknown = sorted(set(changes) - set(SECTIONS) - {"version"})
        if unknown:
            raise PrefsError(f"unknown preference section(s): {', '.join(unknown)}")
        with self._lock:
            merged = self._load(user)
            for section in SECTIONS:
                if section not in changes:
                    continue
                incoming = changes[section]
                if section == "buttons":
                    merged["buttons"] = incoming
                    continue
                if not isinstance(incoming, Mapping):
                    raise PrefsError(f"'{section}' must be an object")
                target = dict(merged[section])
                for key, value in incoming.items():
                    if value is None:
                        target.pop(str(key), None)
                    elif section in ("values", "defaults", "nav", "panels") and isinstance(
                        value, Mapping
                    ):
                        inner = dict(target.get(str(key), {}))
                        for field_name, field_value in value.items():
                            if field_value is None:
                                inner.pop(str(field_name), None)
                            else:
                                inner[str(field_name)] = field_value
                        target[str(key)] = inner
                    else:
                        target[str(key)] = value
                merged[section] = target
            clean = validate_prefs(merged)
            self._save(user, clean)
        return clean

    def reset(self, user: str | None = None, sections: tuple[str, ...] = ()) -> dict[str, Any]:
        """Forget *sections* (all of them when none are named); ``defaults`` only by name."""
        chosen = tuple(sections) or tuple(s for s in SECTIONS if s != "defaults")
        unknown = sorted(set(chosen) - set(SECTIONS))
        if unknown:
            raise PrefsError(f"unknown preference section(s): {', '.join(unknown)}")
        with self._lock:
            current = self._load(user)
            fresh = empty_prefs()
            for section in chosen:
                current[section] = fresh[section]
            self._save(user, current)
            return current

    # -- file handling ----------------------------------------------------------

    def _load(self, user: str | None) -> dict[str, Any]:
        path = self.path_for(user)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return empty_prefs()
        except OSError:
            return empty_prefs()
        try:
            return validate_prefs(json.loads(raw))
        except (ValueError, PrefsError):
            with contextlib.suppress(OSError):
                os.replace(path, path.with_suffix(".bad"))
            return empty_prefs()

    def _save(self, user: str | None, prefs: dict[str, Any]) -> None:
        path = self.path_for(user)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(prefs, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)


def validate_prefs(prefs: Any) -> dict[str, Any]:
    """Return a clean copy of *prefs*, or raise :class:`PrefsError` saying what is wrong."""
    if not isinstance(prefs, Mapping):
        raise PrefsError("preferences must be a JSON object")
    unknown = sorted(set(prefs) - set(SECTIONS) - {"version"})
    if unknown:
        raise PrefsError(f"unknown preference section(s): {', '.join(unknown)}")
    clean = empty_prefs()
    for section in ("nav", "panels"):
        clean[section] = _overlay(prefs.get(section), section)
    clean["buttons"] = _buttons(prefs.get("buttons"))
    for section in ("values", "defaults"):
        clean[section] = _forms(prefs.get(section), section)
    clean["settings"] = _settings(prefs.get("settings"))
    size = len(json.dumps(clean, ensure_ascii=False).encode("utf-8"))
    if size > MAX_BYTES:
        raise PrefsError(f"preferences are {size} bytes; the limit is {MAX_BYTES}")
    return clean


def _text(value: Any, what: str) -> str:
    text = str(value).strip()
    if len(text) > MAX_TEXT or any(ord(ch) < 32 for ch in text):
        raise PrefsError(f"{what} must be one line of at most {MAX_TEXT} characters")
    return text


def _key(value: Any, what: str) -> str:
    key = str(value)
    if not _KEY.match(key):
        raise PrefsError(f"{what} {key!r} is not a valid key (lowercase letters, digits, . - _)")
    return key


def _overlay(value: Any, section: str) -> dict[str, dict[str, Any]]:
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping) or len(value) > MAX_ITEMS:
        raise PrefsError(f"'{section}' must be an object of at most {MAX_ITEMS} entries")
    result: dict[str, dict[str, Any]] = {}
    for raw_key, entry in value.items():
        key = _key(raw_key, f"{section} key")
        if not isinstance(entry, Mapping):
            raise PrefsError(f"{section}.{key} must be an object")
        item: dict[str, Any] = {}
        label = entry.get("label", entry.get("title"))
        if label not in (None, ""):
            item["label"] = _text(label, f"{section}.{key}.label")
        if entry.get("hidden") is not None:
            item["hidden"] = bool(entry["hidden"])
        if entry.get("order") is not None:
            order = entry["order"]
            if isinstance(order, bool) or not isinstance(order, int):
                raise PrefsError(f"{section}.{key}.order must be a whole number")
            item["order"] = order
        if item:
            result[key] = item
    return result


def _buttons(value: Any) -> list[dict[str, Any]]:
    if value in (None, []):
        return []
    if not isinstance(value, list | tuple) or len(value) > MAX_ITEMS:
        raise PrefsError(f"'buttons' must be a list of at most {MAX_ITEMS} entries")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(value):
        if not isinstance(entry, Mapping):
            raise PrefsError(f"button #{index + 1} must be an object")
        ident = _key(entry.get("id"), f"button #{index + 1} id")
        if ident in seen:
            raise PrefsError(f"duplicate button id {ident!r}")
        seen.add(ident)
        command = str(entry.get("command") or "")
        if not _COMMAND.match(command):
            raise PrefsError(f"button {ident!r}: command must look like 'queue purge'")
        result.append(
            {
                "id": ident,
                "label": _text(entry.get("label") or ident, f"button {ident!r} label"),
                "command": command,
                "args": _scalars(entry.get("args"), f"button {ident!r} args"),
                "confirm": bool(entry.get("confirm", False)),
                "danger": bool(entry.get("danger", False)),
            }
        )
    return result


def _forms(value: Any, section: str) -> dict[str, dict[str, Any]]:
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping) or len(value) > MAX_ITEMS:
        raise PrefsError(f"'{section}' must be an object of at most {MAX_ITEMS} forms")
    result: dict[str, dict[str, Any]] = {}
    for raw_key, fields in value.items():
        key = _key(raw_key, f"{section} key")
        scalars = _scalars(fields, f"{section}.{key}")
        if scalars:
            result[key] = scalars
    return result


def _scalars(value: Any, what: str) -> dict[str, Any]:
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping) or len(value) > MAX_ITEMS:
        raise PrefsError(f"{what} must be an object of plain values")
    result: dict[str, Any] = {}
    for name, item in value.items():
        if not _FIELD.match(str(name)):
            raise PrefsError(f"{what}: {str(name)!r} is not a valid field name")
        if isinstance(item, str):
            if len(item) > 2000:
                raise PrefsError(f"{what}.{name} is longer than 2000 characters")
            result[str(name)] = item
        elif isinstance(item, bool | int | float):
            result[str(name)] = item
        elif item is None:
            continue
        else:
            raise PrefsError(f"{what}.{name} must be text, a number or true/false")
    return result


def _settings(value: Any) -> dict[str, str]:
    if value in (None, {}):
        return {}
    if not isinstance(value, Mapping):
        raise PrefsError("'settings' must be an object")
    result: dict[str, str] = {}
    for name, item in value.items():
        if name not in _SETTING_KEYS:
            raise PrefsError(
                f"unknown setting {name!r}; known settings: {', '.join(_SETTING_KEYS)}"
            )
        if item not in (None, ""):
            result[str(name)] = _text(item, f"settings.{name}")
    return result

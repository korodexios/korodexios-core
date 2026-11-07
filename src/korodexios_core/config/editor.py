"""Editing the local configuration layer: one writer for the terminal and the web console.

``kdx config set|unset`` and the console's settings form both end here, so the two can never
disagree about what is allowed. The rules are few and strict:

* only ``kdx.local.yml`` is written - never ``kdx.yml``, never ``.env``;
* a secret (a key a schema declares ``secret=True``, or one whose name says so) is refused:
  secrets keep the ``.env`` recipe;
* a key must be one the chassis or a loaded module declares, and the value must pass the
  declaration (type, ``choices``, ``minimum``/``maximum``) before anything is written;
* a batch is all-or-nothing: one bad key writes nothing.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from ..fsio import atomic_write_text
from .defaults import CORE_DEFAULTS
from .keys import MODULE_SCOPE
from .layered import is_secret_key
from .schema import ConfigField, ConfigKind, ConfigSchema

__all__ = [
    "CORE_LIVE_KEYS",
    "ConfigEditError",
    "Declared",
    "EditPlan",
    "declared_keys",
    "plan_edits",
    "write_local",
]

LOCAL_FILE_NAME: Final[str] = "kdx.local.yml"

CORE_LIVE_KEYS: Final[frozenset[str]] = frozenset({"core.log.level"})
"""Chassis keys the running daemon applies on reload; every other chassis key is read at boot."""

_SKIPPED_CORE: Final[frozenset[str]] = frozenset({"core.home"})

_CORE_CHOICES: Final[dict[str, tuple[str, ...]]] = {
    "core.log.level": ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
}
"""Chassis keys whose value the chassis refuses at boot unless it is one of these."""

_CORE_RANGES: Final[dict[str, tuple[float | None, float | None]]] = {
    "core.ipc.port": (0, 65535),
    "core.cli.width": (20, 400),
    "core.log.backup_count": (0, 1000),
    "core.log.max_bytes": (1024, None),
    "core.lifecycle.tick_interval": (0.01, None),
    "core.lifecycle.stop_timeout": (0.1, None),
}
"""Sane bounds, so a typo cannot write a local layer the next boot trips over."""

_UPPERCASE: Final[frozenset[str]] = frozenset({"core.log.level"})
_MISSING: Final[object] = object()


class ConfigEditError(ValueError):
    """A refused edit: what was wrong, and what to do instead."""

    def __init__(self, message: str, *, key: str = "", hint: str = "") -> None:
        super().__init__(message)
        self.key = key
        self.hint = hint


@dataclass(frozen=True, slots=True)
class Declared:
    """One key somebody declared: the chassis, or a module through its schema."""

    key: str
    module: str
    field: ConfigField
    live: bool
    secret: bool

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON view a renderer builds a typed form from."""
        view = self.field.as_dict()
        view.update(
            key=self.key,
            name=self.field.key,
            module=self.module,
            live=self.live,
            secret=self.secret,
        )
        view.pop("sentinel", None)
        return view


@dataclass(frozen=True, slots=True)
class EditPlan:
    """A validated batch: what to write, and what each key will mean."""

    sets: dict[str, Any]
    unsets: tuple[str, ...]
    declared: dict[str, Declared]


def _core_kind(default: Any) -> ConfigKind:
    if isinstance(default, bool):
        return ConfigKind.BOOL
    if isinstance(default, int):
        return ConfigKind.INT
    if isinstance(default, float):
        return ConfigKind.FLOAT
    if isinstance(default, list | tuple):
        return ConfigKind.LIST
    return ConfigKind.STR


def declared_keys(
    schemas: Mapping[str, ConfigSchema],
    reloadable: Iterable[str] = (),
) -> dict[str, Declared]:
    """Return every editable key by its full dotted name.

    *reloadable* names the modules that react to a configuration reload; a field that does
    not say ``live`` itself inherits that fact from its module.
    """
    reacts = set(reloadable)
    found: dict[str, Declared] = {}
    for key, default in sorted(CORE_DEFAULTS.items()):
        if key in _SKIPPED_CORE or isinstance(default, Mapping):
            continue
        low, high = _CORE_RANGES.get(key, (None, None))
        found[key] = Declared(
            key=key,
            module="core",
            field=ConfigField(
                key,
                kind=_core_kind(default),
                default=default,
                choices=_CORE_CHOICES.get(key, ()),
                minimum=low,
                maximum=high,
            ),
            live=key in CORE_LIVE_KEYS,
            secret=is_secret_key(key),
        )
    for module, schema in sorted(schemas.items()):
        for field in schema.fields:
            full = f"{MODULE_SCOPE}.{module}.{field.key}"
            found[full] = Declared(
                key=full,
                module=module,
                field=field,
                live=field.live if field.live is not None else module in reacts,
                secret=field.secret or is_secret_key(full),
            )
    return found


def _parse(field: ConfigField, text: str) -> Any:
    """Turn what a person typed into the value the field stores."""
    raw = text.strip()
    if field.kind is ConfigKind.LIST:
        if raw.startswith("["):
            try:
                loaded = yaml.safe_load(raw)
            except yaml.YAMLError as exc:
                raise ConfigEditError(f"{field.key} is not a valid list: {exc}") from exc
            if not isinstance(loaded, list):
                raise ConfigEditError(f"{field.key} must be a list")
            return loaded
        return [item.strip() for item in raw.split(",") if item.strip()]
    if field.kind is ConfigKind.ANY:
        try:
            return yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ConfigEditError(f"{field.key} is not valid YAML: {exc}") from exc
    return text if field.kind in (ConfigKind.STR, ConfigKind.PATH) else raw


def _stored(field: ConfigField, value: Any) -> Any:
    """Return the value to write: coerced, except a path, which keeps the way it was typed."""
    if field.kind is ConfigKind.PATH:
        return str(value)
    return field.coerce(value)


def _hint(declared: Declared) -> str:
    field = declared.field
    parts = [field.description] if field.description else []
    if field.choices:
        parts.append("choices: " + ", ".join(field.choices))
    if field.minimum is not None or field.maximum is not None:
        low = "-" if field.minimum is None else f"{field.minimum:g}"
        high = "-" if field.maximum is None else f"{field.maximum:g}"
        parts.append(f"range: {low}..{high}")
    return "; ".join(parts)


def _resolve(key: str, module: str, declared: Mapping[str, Declared]) -> Declared:
    text = str(key).strip()
    scope = str(module or "").strip()
    full = f"{MODULE_SCOPE}.{scope}.{text}" if scope and not text.startswith(
        (f"{MODULE_SCOPE}.", "core.")
    ) else text
    found = declared.get(full)
    if found is not None:
        return found
    near = difflib.get_close_matches(full, list(declared), n=3, cutoff=0.7)
    hint = "see 'kdx config schema' for every key that can be set"
    if near:
        hint = "did you mean " + ", ".join(near[:3]) + "?"
    raise ConfigEditError(f"{full} is not a key the chassis or a loaded module declares",
                          key=full, hint=hint)


def plan_edits(
    declared: Mapping[str, Declared],
    *,
    sets: Mapping[str, Any] | None = None,
    unsets: Iterable[str] = (),
    module: str = "",
) -> EditPlan:
    """Validate a batch; raise ConfigEditError for the first key that is not allowed.

    A value may be text (as typed in a terminal) or already typed (as a web form sends it);
    either way it passes the declaration before any file is touched.
    """
    chosen: dict[str, Any] = {}
    used: dict[str, Declared] = {}
    for raw_key, raw_value in (sets or {}).items():
        item = _resolve(raw_key, module, declared)
        _refuse_secret(item)
        value = _parse(item.field, raw_value) if isinstance(raw_value, str) else raw_value
        if item.key in _UPPERCASE and isinstance(value, str):
            value = value.strip().upper()
        problem = item.field.check(value)
        if problem:
            raise ConfigEditError(problem, key=item.key, hint=_hint(item))
        chosen[item.key] = _stored(item.field, value)
        used[item.key] = item
    removed: list[str] = []
    for raw_key in unsets:
        item = _resolve(raw_key, module, declared)
        _refuse_secret(item)
        if item.key in chosen:
            raise ConfigEditError(f"{item.key} is both set and unset in one change", key=item.key)
        removed.append(item.key)
        used[item.key] = item
    if not chosen and not removed:
        raise ConfigEditError("nothing to change", hint="name a key and a value")
    return EditPlan(chosen, tuple(removed), used)


def _refuse_secret(item: Declared) -> None:
    if item.secret:
        raise ConfigEditError(
            f"{item.key} is a secret; it is never written by the settings editor",
            key=item.key,
            hint="put it in .env (KDX_... variable) instead",
        )


def _load(path: Path) -> dict[str, Any]:
    if path.suffix.lower() not in (".yml", ".yaml"):
        raise ConfigEditError(
            f"{path.name} is not YAML, so the editor leaves it alone",
            hint="edit it by hand, or use a kdx.local.yml",
        )
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigEditError(f"{path} cannot be read, so it was left alone: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigEditError(f"{path.name} must hold a mapping at the top level")
    return dict(loaded)


def _put(tree: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    cursor = tree
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[parts[-1]] = value


def _drop(tree: dict[str, Any], key: str) -> bool:
    parts = key.split(".")
    trail: list[tuple[dict[str, Any], str]] = []
    cursor = tree
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            return False
        trail.append((cursor, part))
        cursor = child
    if parts[-1] not in cursor:
        return False
    del cursor[parts[-1]]
    for parent, name in reversed(trail):  # prune the tables this left empty
        if parent[name]:
            break
        del parent[name]
    return True


def write_local(path: Path, plan: EditPlan) -> list[str]:
    """Write *plan* into the local YAML layer; return the keys that were really removed.

    Comments in a hand-edited file do not survive a rewrite (YAML has no comment model in
    PyYAML), which is why only the machine-local, gitignored layer is ever written.
    """
    data = _load(path)
    literal = {key for key in (*plan.sets, *plan.unsets) if data.pop(key, _MISSING) is not _MISSING}
    for key, value in plan.sets.items():
        _put(data, key, value)
    removed = [key for key in plan.unsets if _drop(data, key) or key in literal]
    atomic_write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return removed

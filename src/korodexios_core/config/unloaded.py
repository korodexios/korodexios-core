"""Values of modules that were not loaded are masked: their schema could not say what is secret.

A module marks a secret its name does not betray (``ConfigField("targets", secret=True)``)
in its schema, and a schema exists only for a module that was loaded. With the module
disabled, or discovery off, ``modules.<name>.*`` would be printed in clear. Until the registry
has seen the module, every string under its keys is treated as sensitive.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable

_LOADED: set[str] = set()
_LOCK = threading.Lock()
_PREFIX = "modules."


def declare_loaded_modules(names: Iterable[str]) -> None:
    """Remember modules the registry loaded: their schema has already declared its secrets."""
    with _LOCK:
        _LOADED.update(str(name).strip().lower() for name in names if str(name).strip())


def is_unloaded_module_key(key: str) -> bool:
    """Return True for ``modules.<name>.<setting>`` when module <name> was never loaded."""
    lowered = str(key).strip().lower()
    if not lowered.startswith(_PREFIX):
        return False
    name, dot, _setting = lowered[len(_PREFIX):].partition(".")
    if not dot:
        return False
    with _LOCK:
        return name not in _LOADED

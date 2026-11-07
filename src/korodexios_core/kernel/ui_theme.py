"""Themes: one token set that every console of every project reads.

A theme is a small set of colour tokens, written as CSS custom properties (``--kdx-bg``) in a
``.css`` file or as a ``.json`` object, the way an Obsidian snippet restyles an app. Three
sources, later ones win by name: the built-in themes, ``<project>/themes/`` and the person's
own folder under ``data/ui/themes/``. A file that sets only some tokens inherits the rest
from the built-in ``graphite`` theme; a file with a colour that is not ``#rgb`` / ``#rrggbb``
is refused. ``check_theme`` measures contrast (WCAG 2): 4.5:1 for text, 7:1 at level AAA.

``ALIASES`` names the variables projects used before there was a shared set (``--fg``,
``--muted``, ``--acc`` ...), so a page written against them follows the active theme too.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOKENS = (
    "bg", "panel", "sel", "text", "muted", "line", "accent", "on-accent", "ok", "warn", "bad",
)
DEFAULT_THEME = "graphite"
MAX_THEME_BYTES = 16 * 1024
SUFFIXES = (".css", ".json")
_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_DECLARATION = re.compile(r"--kdx-([a-z][a-z-]*)\s*:\s*([^;}]+?)\s*(?:;|(?=\}))")

BUILTIN: dict[str, dict[str, str]] = {
    "graphite": {
        "bg": "#17161c", "panel": "#1e1d25", "sel": "#2a2736", "text": "#e6e4ee",
        "muted": "#a8a4ba", "line": "#34313f", "accent": "#a78bfa", "on-accent": "#17161c",
        "ok": "#6ee7a8", "warn": "#f5c26b", "bad": "#f28b8b",
    },
    "light": {
        "bg": "#f6f5fa", "panel": "#ecebf3", "sel": "#ddd9ee", "text": "#1d1b26",
        "muted": "#5b5870", "line": "#cfcce0", "accent": "#5b3fd1", "on-accent": "#ffffff",
        "ok": "#17794a", "warn": "#85560a", "bad": "#b42323",
    },
    "contrast": {
        "bg": "#000000", "panel": "#0b0b0f", "sel": "#1f1a33", "text": "#ffffff",
        "muted": "#d6d3e6", "line": "#8d88a8", "accent": "#c4b0ff", "on-accent": "#000000",
        "ok": "#7dffb4", "warn": "#ffd37a", "bad": "#ff9a9a",
    },
}
HIGH_CONTRAST = frozenset({"contrast"})

ALIASES: dict[str, tuple[str, ...]] = {
    "bg": ("--bg",),
    "panel": ("--panel",),
    "text": ("--text", "--fg"),
    "muted": ("--muted", "--mut"),
    "line": ("--line",),
    "accent": ("--accent", "--acc"),
    "ok": ("--ok",),
    "warn": ("--warn",),
    "bad": ("--bad",),
}

# (foreground token, background token, what it is) - every pair a person must be able to read
PAIRS = (
    ("text", "bg", "body text on the page"),
    ("text", "panel", "body text on a panel"),
    ("muted", "bg", "secondary text on the page"),
    ("muted", "panel", "secondary text on a panel"),
    ("on-accent", "accent", "text on an accent button"),
    ("ok", "panel", "ok state on a panel"),
    ("warn", "panel", "warning state on a panel"),
    ("bad", "panel", "error state on a panel"),
    ("accent", "panel", "accent on a panel"),
)


class ThemeError(ValueError):
    """A theme was not acceptable; the message says what to fix."""


@dataclass(frozen=True, slots=True)
class Theme:
    """One theme: its name, where it came from and a complete token set."""

    name: str
    source: str  # builtin | project | user
    tokens: dict[str, str]
    path: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON view."""
        view: dict[str, Any] = {"name": self.name, "source": self.source, "tokens": self.tokens}
        if self.path:
            view["path"] = self.path
        return view


def css_variables(tokens: Mapping[str, str]) -> dict[str, str]:
    """Return every CSS variable a page can use for *tokens*: ``--kdx-*`` plus the aliases."""
    variables: dict[str, str] = {}
    for token, value in tokens.items():
        variables[f"--kdx-{token}"] = value
        for alias in ALIASES.get(token, ()):
            variables[alias] = value
    return variables


def css_text(theme: Theme) -> str:
    """Return *theme* as a ``:root`` rule a page can include."""
    lines = [f"/* theme: {theme.name} */", ":root {"]
    lines += [f"  {name}: {value};" for name, value in css_variables(theme.tokens).items()]
    lines.append("}")
    return "\n".join(lines) + "\n"


def _clean_tokens(raw: Mapping[str, Any], where: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for name, value in raw.items():
        if name not in TOKENS:
            raise ThemeError(f"{where}: unknown token {name!r}; known: {', '.join(TOKENS)}")
        text = str(value).strip()
        if not _COLOR.match(text):
            raise ThemeError(f"{where}: {name} must be a colour like #1e1d25, not {text!r}")
        tokens[str(name)] = text.lower()
    return tokens


def parse_theme(text: str, suffix: str, where: str) -> dict[str, str]:
    """Read the tokens a ``.css`` or ``.json`` theme sets (not yet completed)."""
    if len(text.encode("utf-8")) > MAX_THEME_BYTES:
        raise ThemeError(f"{where}: larger than {MAX_THEME_BYTES} bytes")
    if suffix == ".json":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ThemeError(f"{where}: not valid JSON ({exc})") from exc
        if not isinstance(data, Mapping):
            raise ThemeError(f"{where}: must be a JSON object")
        raw = data.get("tokens", data)
        if not isinstance(raw, Mapping):
            raise ThemeError(f"{where}: 'tokens' must be an object")
        return _clean_tokens({k: v for k, v in raw.items() if k != "name"}, where)
    found = {match.group(1): match.group(2) for match in _DECLARATION.finditer(text)}
    if not found:
        raise ThemeError(f"{where}: no --kdx-* declaration found")
    return _clean_tokens(found, where)


def complete(partial: Mapping[str, str]) -> dict[str, str]:
    """Return *partial* with every missing token taken from the default theme."""
    tokens = dict(BUILTIN[DEFAULT_THEME])
    tokens.update(partial)
    return tokens


def _luminance(color: str) -> float:
    digits = color.lstrip("#")
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    channels = [int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """Return the WCAG 2 contrast ratio of two ``#rgb``/``#rrggbb`` colours (1 to 21)."""
    first, second = _luminance(foreground), _luminance(background)
    light, dark = max(first, second), min(first, second)
    return (light + 0.05) / (dark + 0.05)


def check_theme(tokens: Mapping[str, str], level: str = "AA") -> list[dict[str, Any]]:
    """Return one row per readable pair: ``ok`` is whether it meets the level (AA 4.5, AAA 7)."""
    if level not in ("AA", "AAA"):
        raise ThemeError("level must be AA or AAA")
    need = 7.0 if level == "AAA" else 4.5
    rows: list[dict[str, Any]] = []
    for fore, back, what in PAIRS:
        ratio = round(contrast_ratio(tokens[fore], tokens[back]), 2)
        rows.append(
            {"pair": f"{fore} on {back}", "what": what, "ratio": ratio, "need": need,
             "ok": ratio >= need}
        )
    return rows


class ThemeStore:
    """Finds themes in the built-in set, the project folder and the person's folder."""

    def __init__(self, project_dir: Path, user_dir: Path | None = None) -> None:
        self._folders = (("project", Path(project_dir)),) + (
            (("user", Path(user_dir)),) if user_dir is not None else ()
        )

    def themes(self) -> tuple[dict[str, Theme], list[str]]:
        """Return every usable theme by name, and a problem line for each file refused."""
        found = {n: Theme(n, "builtin", dict(t)) for n, t in BUILTIN.items()}
        problems: list[str] = []
        for source, folder in self._folders:
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                if path.suffix not in SUFFIXES or not path.is_file():
                    continue
                name = path.stem.lower()
                if not _NAME.match(name):
                    problems.append(f"{path.name}: the file name must be like 'my-theme'")
                    continue
                try:
                    partial = parse_theme(path.read_text(encoding="utf-8"), path.suffix, path.name)
                except (OSError, UnicodeDecodeError, ThemeError) as exc:
                    problems.append(str(exc))
                    continue
                found[name] = Theme(name, source, complete(partial), str(path))
        return found, problems

    def get(self, name: str) -> Theme:
        """Return the theme called *name*, or raise :class:`ThemeError` listing the choices."""
        themes, _problems = self.themes()
        if name not in themes:
            raise ThemeError(f"no theme {name!r}; available: {', '.join(sorted(themes))}")
        return themes[name]

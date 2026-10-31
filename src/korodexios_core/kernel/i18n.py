"""Message catalogues: one language service the chassis, its modules, and projects share.

The chassis writes English source strings. A *domain* (the chassis is ``korodexios``, a
module or a project picks its own) owns a ``locales`` directory that holds gettext
sources, read directly - there is no compile step and no new dependency:

    <locales>/<domain>.pot                       the template: every source string
    <locales>/<locale>/<domain>.po               a translation
    <locales>/<locale>/LC_MESSAGES/<domain>.po   the same, in the classic gettext layout

``translate()`` returns the source string when a locale, a catalogue, or one entry is
missing, so a project runs before any translation exists. Diagnostics on stderr, logs,
and machine payloads (``--json``) are never translated: a grep or a parser must see the
same bytes in every language.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DEFAULT_LOCALE: Final[str] = "en"
CORE_DOMAIN: Final[str] = "korodexios"
AUTO_LOCALE: Final[str] = "auto"
ENVIRONMENT_KEYS: Final[tuple[str, ...]] = ("LC_ALL", "LC_MESSAGES", "LANG")
"""Consulted, in order, when the configured locale is ``auto``."""

_ESCAPES: Final[dict[str, str]] = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
_LOCALE_SHAPE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]{2,3}(?:_[A-Za-z0-9]{2,4})?$")
_FIELD: Final[re.Pattern[str]] = re.compile(r"\{(\w+)\}")


def normalize_locale(value: str | None) -> str:
    """Return *value* as ``ll`` or ``ll_RR`` ('sk_SK.UTF-8' -> 'sk_SK'), or '' when unusable."""
    text = str(value or "").strip().split(".", 1)[0].split("@", 1)[0].replace("-", "_")
    if not _LOCALE_SHAPE.match(text):
        return ""
    language, _, region = text.partition("_")
    return f"{language.lower()}_{region.upper()}" if region else language.lower()


def locale_chain(locale: str) -> tuple[str, ...]:
    """Return the locales to try for *locale*, most specific first: sk_SK -> (sk_SK, sk)."""
    cleaned = normalize_locale(locale)
    if not cleaned:
        return ()
    language = cleaned.partition("_")[0]
    return (cleaned,) if cleaned == language else (cleaned, language)


def _unquote(raw: str) -> str:
    body = raw.strip()
    if len(body) < 2 or body[0] != '"' or body[-1] != '"':
        return ""
    text, out, index = body[1:-1], [], 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            out.append(_ESCAPES.get(text[index + 1], text[index + 1]))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


@dataclass(frozen=True, slots=True)
class PoEntry:
    """One entry of a gettext source file."""

    msgid: str
    msgstr: str
    fuzzy: bool = False
    line: int = 0

    @property
    def translated(self) -> bool:
        """Return True when the entry carries a usable translation."""
        return bool(self.msgstr) and not self.fuzzy


def parse_po(text: str) -> list[PoEntry]:
    """Parse gettext source *text* into entries; the header (empty msgid) is skipped.

    Supports comments, the ``fuzzy`` flag, and strings continued over several lines.
    Plural forms and contexts are not used by the chassis and are ignored.
    """
    entries: list[PoEntry] = []
    msgid: str | None = None
    msgstr = ""
    target = ""
    fuzzy = False
    start = 0
    pending_fuzzy = False

    def flush() -> None:
        nonlocal msgid, msgstr, fuzzy
        if msgid:
            entries.append(PoEntry(msgid, msgstr, fuzzy, start))
        msgid, msgstr, fuzzy = None, "", False

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                pending_fuzzy = True
            continue
        if line.startswith("msgid "):
            flush()
            msgid, target, start = _unquote(line[6:]), "id", number
            fuzzy, pending_fuzzy = pending_fuzzy, False
        elif line.startswith("msgstr "):
            msgstr, target = _unquote(line[7:]), "str"
        elif line.startswith('"'):
            if target == "id" and msgid is not None:
                msgid += _unquote(line)
            elif target == "str":
                msgstr += _unquote(line)
    flush()
    return entries


@dataclass(frozen=True, slots=True)
class Catalog:
    """The entries of one domain in one locale."""

    domain: str
    locale: str
    entries: Mapping[str, PoEntry]
    path: Path | None = None

    def lookup(self, message: str) -> str:
        """Return the translation of *message*, or '' when there is none."""
        entry = self.entries.get(message)
        return entry.msgstr if entry is not None and entry.translated else ""


@dataclass(frozen=True, slots=True)
class CatalogReport:
    """How complete one translation is against the domain's template."""

    domain: str
    locale: str
    total: int
    translated: int
    untranslated: tuple[str, ...]
    fuzzy: tuple[str, ...]
    obsolete: tuple[str, ...]
    broken_fields: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """Return True when nothing is missing, stale, or malformed."""
        return not (self.untranslated or self.fuzzy or self.obsolete or self.broken_fields)

    @property
    def percent(self) -> int:
        """Return the share of translated strings, as a whole number."""
        return 100 if not self.total else int(100 * self.translated / self.total)

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-friendly view."""
        return {
            "domain": self.domain,
            "locale": self.locale,
            "total": self.total,
            "translated": self.translated,
            "percent": self.percent,
            "untranslated": list(self.untranslated),
            "fuzzy": list(self.fuzzy),
            "obsolete": list(self.obsolete),
            "broken_fields": list(self.broken_fields),
            "complete": self.complete,
        }


def _read_entries(path: Path) -> dict[str, PoEntry]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    return {entry.msgid: entry for entry in parse_po(text)}


def _fields(text: str) -> frozenset[str]:
    return frozenset(_FIELD.findall(text))


class I18n:
    """The language service: registered domains, the active locale, and ``translate``."""

    def __init__(self, locale: str = DEFAULT_LOCALE) -> None:
        self._lock = threading.RLock()
        self._domains: dict[str, Path] = {}
        self._cache: dict[tuple[str, str], Catalog | None] = {}
        self._locale = normalize_locale(locale) or DEFAULT_LOCALE

    @property
    def locale(self) -> str:
        """Return the active locale."""
        return self._locale

    def set_locale(self, locale: str) -> str:
        """Switch the active locale (an unusable value falls back to the default)."""
        with self._lock:
            self._locale = normalize_locale(locale) or DEFAULT_LOCALE
            return self._locale

    def register(self, domain: str, directory: str | Path) -> None:
        """Declare that *domain* keeps its catalogues under *directory*."""
        name = str(domain).strip()
        if not name:
            raise ValueError("a message domain needs a name")
        with self._lock:
            self._domains[name] = Path(directory)
            for key in [key for key in self._cache if key[0] == name]:
                del self._cache[key]

    def domains(self) -> dict[str, Path]:
        """Return the registered domains and their catalogue directories."""
        with self._lock:
            return dict(self._domains)

    def _catalog_path(self, domain: str, locale: str) -> Path | None:
        directory = self._domains.get(domain)
        if directory is None:
            return None
        for candidate in (
            directory / locale / f"{domain}.po",
            directory / locale / "LC_MESSAGES" / f"{domain}.po",
        ):
            if candidate.is_file():
                return candidate
        return None

    def catalog(self, domain: str, locale: str) -> Catalog | None:
        """Return the catalogue of *domain* for exactly *locale*, or None."""
        key = (domain, locale)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            path = self._catalog_path(domain, locale)
            found = Catalog(domain, locale, _read_entries(path), path) if path else None
            self._cache[key] = found
            return found

    def locales(self, domain: str | None = None) -> tuple[str, ...]:
        """Return the locales with a catalogue (for *domain*, or any), plus the default."""
        found: set[str] = {DEFAULT_LOCALE}
        with self._lock:
            names = [domain] if domain else list(self._domains)
            for name in names:
                directory = self._domains.get(str(name))
                if directory is None or not directory.is_dir():
                    continue
                for child in directory.iterdir():
                    cleaned = normalize_locale(child.name)
                    if cleaned and child.is_dir() and self._catalog_path(str(name), child.name):
                        found.add(cleaned)
        return tuple(sorted(found))

    def translate(self, message: str, *, domain: str = CORE_DOMAIN, **fields: object) -> str:
        """Return *message* in the active locale, formatted with *fields*.

        A missing locale, catalogue, or entry yields the source string itself. A
        translation that names a field the call did not pass is replaced by the source
        string rather than raising: a bad catalogue must never crash a command.
        """
        text = message
        for locale in locale_chain(self._locale):
            if locale == DEFAULT_LOCALE:
                break
            catalog = self.catalog(domain, locale)
            found = catalog.lookup(message) if catalog is not None else ""
            if found and _fields(found) == _fields(message):
                text = found
                break
        if not fields:
            return text
        values = {key: str(value) for key, value in fields.items()}
        try:
            return text.format(**values)
        except (KeyError, IndexError, ValueError):
            return message

    def check(self, domain: str, locale: str | None = None) -> list[CatalogReport]:
        """Compare translations of *domain* with its template; one report per locale."""
        directory = self._domains.get(domain)
        if directory is None:
            return []
        template = _read_entries(directory / f"{domain}.pot")
        wanted = [locale] if locale else [
            item for item in self.locales(domain) if item != DEFAULT_LOCALE
        ]
        reports: list[CatalogReport] = []
        for name in wanted:
            catalog = self.catalog(domain, str(name))
            entries = dict(catalog.entries) if catalog else {}
            untranslated = tuple(
                key for key in template if key not in entries or not entries[key].msgstr
            )
            fuzzy = tuple(key for key, item in entries.items() if item.fuzzy and key in template)
            obsolete = tuple(key for key in entries if key not in template)
            broken = tuple(
                key
                for key, item in entries.items()
                if item.translated and _fields(item.msgstr) != _fields(key)
            )
            translated = sum(
                1 for key in template if key in entries and entries[key].translated
            )
            reports.append(
                CatalogReport(
                    domain, str(name), len(template), translated,
                    untranslated, fuzzy, obsolete, broken,
                )
            )
        return reports


def environment_locale(environ: Mapping[str, str] | None = None) -> str:
    """Return the locale named by the process environment, or the default."""
    source = os.environ if environ is None else environ
    for key in ENVIRONMENT_KEYS:
        cleaned = normalize_locale(source.get(key, ""))
        if cleaned:
            return cleaned
    return DEFAULT_LOCALE


def resolve_locale(configured: str | None, environ: Mapping[str, str] | None = None) -> str:
    """Return the locale for a configured value: ``auto`` reads the environment."""
    text = str(configured or "").strip()
    if text.lower() == AUTO_LOCALE:
        return environment_locale(environ)
    return normalize_locale(text) or DEFAULT_LOCALE


def core_locales_dir() -> Path:
    """Return the directory that holds the chassis's own catalogues."""
    return Path(__file__).resolve().parent.parent / "locales"


def build_i18n(configured: str | None, extra: Iterable[tuple[str, Path]] = ()) -> I18n:
    """Return the service with the chassis domain registered and the locale resolved."""
    service = I18n(resolve_locale(configured))
    service.register(CORE_DOMAIN, core_locales_dir())
    for domain, directory in extra:
        service.register(domain, directory)
    return service

"""Bring the translations of a domain in line with a freshly extracted template."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from ..kernel.i18n import parse_po
from ..kernel.i18n_extract import compose_po


def refresh_catalogs(
    service: Any, domain: str, wanted: str, keys: Collection[str]
) -> list[tuple[str, int, int]]:
    """Merge the template *wanted* into every catalogue of *domain*; return the changes.

    Translations are kept, new strings are added empty, strings that left the source become
    obsolete. Each row is ``(locale, added, obsolete)``.
    """
    rows: list[tuple[str, int, int]] = []
    for locale in service.locales(domain):
        catalog = service.catalog(domain, locale)
        if catalog is None or catalog.path is None:
            continue
        before = catalog.path.read_text(encoding="utf-8")
        text, _, _ = compose_po(domain, locale, wanted, before, {})
        if text != before:
            catalog.path.write_text(text, encoding="utf-8", newline="\n")
        had = {entry.msgid for entry in parse_po(before)}
        rows.append((locale, len(set(keys) - had), len(had - set(keys))))
    return rows

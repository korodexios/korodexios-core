"""units.py: size and duration formatting, and the sysconf fallbacks."""

from __future__ import annotations

import os

import pytest

from korodexios_core import units
from korodexios_core.units import (
    DEFAULT_PAGE_SIZE,
    format_bytes,
    format_duration,
    page_size,
    sysconf_value,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "-"),
        (-1, "-"),
        (0, "0 bytes"),
        (1_023, "1023 bytes"),
        (1_024, "1.0 KB"),
        (1_536, "1.5 KB"),
        (5 * 1_024**2, "5.0 MB"),
        (3 * 1_024**3, "3.0 GB"),
        (2 * 1_024**7, "2048.0 EB"),
    ],
)
def test_format_bytes(value: int | None, expected: str) -> None:
    assert format_bytes(value) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, "-"),
        (-0.5, "-"),
        (1.25, "1.2s"),
        (59.9, "59.9s"),
        (270, "4m 30s"),
        (7_500, "2h 05m"),
    ],
)
def test_format_duration(seconds: float | None, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_sysconf_value_without_sysconf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(os, "sysconf", raising=False)
    assert sysconf_value("SC_PAGE_SIZE") is None
    assert page_size() == DEFAULT_PAGE_SIZE


def test_sysconf_value_rejects_errors_and_non_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(name: str) -> int:
        raise ValueError(name)

    monkeypatch.setattr(units.os, "sysconf", failing, raising=False)
    assert sysconf_value("SC_NOPE") is None
    monkeypatch.setattr(units.os, "sysconf", lambda name: -1, raising=False)
    assert sysconf_value("SC_PAGE_SIZE") is None
    assert page_size() == DEFAULT_PAGE_SIZE
    monkeypatch.setattr(units.os, "sysconf", lambda name: 16_384, raising=False)
    assert page_size() == 16_384

"""Values of modules that are not loaded are masked (review 2026-11-08, S1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from korodexios_core.cli.entry import main

SECRET = "https://hooks.example/secret-path-abc123"


def show(project_root: Path, capsys: pytest.CaptureFixture[str], *flags: str) -> str:
    code = main(["--home", str(project_root), "--no-log-file", "--no-color", *flags,
                 "config", "show", "--json"])
    assert code == 0
    return capsys.readouterr().out


def test_a_secret_of_a_module_that_is_not_loaded_is_masked(
    project_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KDX_MODULES__WEBHOOKS__TARGETS", SECRET)
    for flags in (("--no-autodiscover",), ("--disable-module", "webhooks")):
        out = show(project_root, capsys, *flags)
        assert "secret-path-abc123" not in out, flags
        assert json.loads(out)["modules.webhooks.targets"]["value"] == "***redacted***"


def test_a_loaded_module_keeps_its_readable_values(
    project_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KDX_MODULES__WEBHOOKS__TARGETS", SECRET)
    out = show(project_root, capsys)
    assert "secret-path-abc123" not in out, "declared secret=True by the loaded module"
    assert json.loads(out)["modules.webhooks.targets"]["value"] == "***redacted***"

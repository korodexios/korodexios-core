"""A seeded project calls only commands this core has (review 2026-11-08, B6).

`kdx init` pins the chassis to the tag of its own version. A command the seed runs (in the
Makefile, CI, pre-commit) that this tree lacks fails the first `make check` of every new project.
The check below finds each `kdx ...` in the seeded files and asks the parser of this core.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from korodexios_core.cli.entry import main

SEEDED = ("Makefile", ".github/workflows/*.yml", ".pre-commit-config.yaml", "*.sh", "*.ps1")
WORDS = r"[a-z][a-z0-9-]*(?:\s+[a-z][a-z0-9-]*)*"
CALL = re.compile(rf"(?:\bkdx|-m korodexios_core|\bkorodexios)\s+({WORDS})")


def seeded_calls(root: Path) -> set[tuple[str, ...]]:
    """Return the command words (up to the first flag) of every call in the seeded files."""
    calls: set[tuple[str, ...]] = set()
    for pattern in SEEDED:
        for path in root.rglob(pattern):
            if ".venv" in path.parts or "node_modules" in path.parts:
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.lstrip().startswith("#"):
                    continue
                for match in CALL.finditer(line):
                    calls.add(tuple(match.group(1).split()))
    return calls


def parses(root: Path, words: tuple[str, ...], capsys: pytest.CaptureFixture[str]) -> bool:
    """Return True when the core's parser accepts *words* as a command (asks for its help)."""
    for take in range(len(words), 0, -1):
        argv = ["--home", str(root), "--no-log-file", "--no-color", *words[:take], "--help"]
        code = main(argv)
        capsys.readouterr()
        if code == 0:  # argparse answers a known command's --help with 0, an unknown one with 2
            return True
    capsys.readouterr()
    return False


@pytest.mark.parametrize("flags", [(), ("--container",), ("--ui", "static")])
def test_every_command_a_seed_runs_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flags: tuple[str, ...]
) -> None:
    target = tmp_path / "seeded"
    code = main(["--home", str(tmp_path), "--no-log-file", "--no-color", "--yes", "init",
                 "seeded", "--path", str(target), *flags])
    capsys.readouterr()
    assert code == 0
    calls = seeded_calls(target)
    assert ("docs", "check") in calls, "the seed no longer runs 'kdx docs check'; update this test"
    missing = sorted(" ".join(words) for words in calls if not parses(tmp_path, words, capsys))
    assert not missing, f"the seeded files run commands this core does not have: {missing}"

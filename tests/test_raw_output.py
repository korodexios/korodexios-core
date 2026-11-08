"""Passed-through data is not translated (review 2026-11-08, I1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.kernel.cli_contract import CliCommand
from tests.conftest import PROJECT_CONFIG
from tests.test_ipc import base_args, ipc_chassis


def test_a_delegated_output_line_stays_as_the_command_wrote_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "kdx.yml").write_text(PROJECT_CONFIG, encoding="utf-8")
    chassis = ipc_chassis(tmp_path)
    chassis.initialize()
    chassis.registry.get("heartbeat").commands.append(  # type: ignore[union-attr]
        CliCommand(name="probe", subcommands=(
            CliCommand(name="state", handler=lambda r: (print("running"), 0)[1]),
        ))
    )
    chassis.start()
    try:
        main([*base_args(tmp_path), "--locale", "sk", "rpc", "probe", "state"])
        out = capsys.readouterr().out
    finally:
        chassis.shutdown()
    assert out.splitlines()[0] == "running"


def test_a_printed_document_is_not_translated(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--home", str(project_root), "--no-log-file", "--no-color", "--locale", "sk",
          "conventions"])
    sk = capsys.readouterr().out
    main(["--home", str(project_root), "--no-log-file", "--no-color", "conventions"])
    assert sk == capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [("agents", "sync", "--check", "--only", "bogus"), ("brief", "--check", "--file", "nope.md")],
)
def test_stderr_diagnostics_stay_english(
    project_root: Path, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
) -> None:
    """Review 2026-11-08, I2: CI greps stderr, so the language setting must not change it."""
    base = ["--home", str(project_root), "--no-log-file", "--no-color"]

    def diagnostics() -> list[str]:  # the log lines carry timestamps; the tagged ones are ours
        return [line for line in capsys.readouterr().err.splitlines() if line.startswith("[")]

    main([*base, *argv])
    english = diagnostics()
    main([*base, "--locale", "sk", *argv])
    assert diagnostics() == english != []

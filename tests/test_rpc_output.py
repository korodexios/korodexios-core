"""Delegated commands bring their stdout home and never prompt the daemon's keyboard."""

from __future__ import annotations

import io
import sys
import threading

import pytest

from korodexios_core.cli.ux import UserExperience
from korodexios_core.config.layered import LayeredConfig
from korodexios_core.kernel import rpc_output
from korodexios_core.kernel.rpc_output import capture_active, capture_output, captured_text

from .test_ipc import ipc_chassis


def test_capture_collects_this_context_only(capsys) -> None:
    with capture_output() as buffer:
        assert capture_active() is True
        print("inside")
        sys.stdout.flush()
        assert sys.stdout.isatty() is False
    assert capture_active() is False
    print("outside")
    assert buffer.getvalue() == "inside\n"
    assert capsys.readouterr().out == "outside\n"


def test_concurrent_captures_do_not_mix(capsys) -> None:
    seen: dict[str, str] = {}
    gate = threading.Barrier(2)

    def work(name: str) -> None:
        with capture_output() as buffer:
            gate.wait(timeout=5)
            for _ in range(50):
                print(name)
        seen[name] = buffer.getvalue()

    threads = [threading.Thread(target=work, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert seen == {"a": "a\n" * 50, "b": "b\n" * 50}
    assert capsys.readouterr().out == ""


def test_long_output_is_cut_with_a_note(monkeypatch) -> None:
    monkeypatch.setattr(rpc_output, "MAX_CAPTURED_CHARS", 10)
    buffer = io.StringIO("x" * 25)
    text = captured_text(buffer)
    assert text.startswith("x" * 10)
    assert "15 more characters" in text
    assert captured_text(io.StringIO("short")) == "short"


def test_a_captured_ux_never_prompts() -> None:
    config = LayeredConfig(defaults={"core.cli.interactive": True})
    assert UserExperience(config).interactive is True
    with capture_output():
        assert UserExperience(config).interactive is False


def test_dispatch_rpc_returns_the_command_output(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    chassis.start()
    try:
        reply = chassis.dispatch_rpc("heartbeat status")
        assert reply["exit_code"] == 0
        assert "heartbeat" in reply["output"].lower()
    finally:
        chassis.shutdown()


def test_dispatch_rpc_names_the_argument_that_did_not_parse(project_root) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        with pytest.raises(LookupError, match="invalid choice: 'nope'"):
            chassis.dispatch_rpc("system diagnostics", {"argv": ["--section", "nope"]})
    finally:
        chassis.shutdown()

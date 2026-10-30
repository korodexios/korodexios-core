"""'kdx service': the service definitions per platform and the install/uninstall/status flow.

No test registers anything with the host: the service-manager commands are recorded by a
fake runner, and the definitions are checked as documents.
"""

from __future__ import annotations

import io
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

import pytest

from korodexios_core.cli import entry as entry_module
from korodexios_core.cli import service as service_module
from korodexios_core.cli.entry import main
from korodexios_core.cli.service import build_plan, service_name
from korodexios_core.constants import ExitCode

from .test_ipc import base_args, ipc_chassis


class FakeManager:
    """Records service-manager commands and answers with a chosen exit code."""

    def __init__(self, code: int = 0, stdout: str = "") -> None:
        self.calls: list[list[str]] = []
        self.code = code
        self.stdout = stdout

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), self.code, self.stdout, "nope")


@pytest.fixture()
def chassis(project_root):
    built = ipc_chassis(project_root)
    yield built
    built.shutdown()


def test_names_are_safe_for_every_manager(chassis) -> None:
    assert service_name(chassis, "My Service!") == "my-service"
    assert service_name(chassis, "---") == "korodexios"
    assert service_name(chassis, None).startswith("korodexios")


def test_windows_task_is_valid_xml_running_pythonw(chassis, tmp_path: Path) -> None:
    plan = build_plan(chassis, "demo", platform="windows", home=tmp_path)
    assert plan.name == "\\Korodexios\\demo"
    assert plan.encoding == "utf-16"
    namespace = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    root = ET.fromstring(plan.definition.split("\n", 1)[1])  # ElementTree refuses the UTF-16 prolog
    arguments = root.find(".//t:Exec/t:Arguments", namespace)
    assert arguments is not None and "-m korodexios_core --home" in str(arguments.text)
    assert str(arguments.text).endswith(" run")
    assert root.find(".//t:LogonTrigger", namespace) is not None
    assert root.find(".//t:RunLevel", namespace).text == "LeastPrivilege"  # type: ignore[union-attr]
    assert ("schtasks", "/Run", "/TN", plan.name) in plan.install


def test_systemd_unit_and_launchd_plist(chassis, tmp_path: Path) -> None:
    linux = build_plan(chassis, "demo", platform="linux", home=tmp_path)
    assert linux.definition_path == tmp_path / ".config/systemd/user/demo.service"
    assert "ExecStart=" in linux.definition
    assert '"-m" "korodexios_core"' in linux.definition
    assert "Restart=on-failure" in linux.definition
    assert "SuccessExitStatus=130 143" in linux.definition
    assert ("systemctl", "--user", "enable", "--now", "demo.service") in linux.install
    assert "enable-linger" in linux.note
    mac = build_plan(chassis, "demo", platform="macos", home=tmp_path)
    assert mac.definition_path.name == "dev.korodexios.demo.plist"
    assert "<string>korodexios_core</string>" in mac.definition
    assert mac.install[0][:2] == ("launchctl", "bootstrap")
    ET.fromstring(mac.definition.split("\n", 2)[2])  # well-formed after the prolog and doctype


def test_install_print_changes_nothing(chassis, project_root, capsys, monkeypatch) -> None:
    fake = FakeManager()
    monkeypatch.setattr(service_module, "run_command", fake)
    assert main([*base_args(project_root), "service", "install", "--print"]) == 0
    out = capsys.readouterr().out
    assert "--print changed nothing" in out
    assert fake.calls == []
    assert not (project_root / "var" / "service").exists() or not any(
        (project_root / "var" / "service").iterdir()
    )


def test_install_status_uninstall_flow(chassis, project_root, capsys, monkeypatch) -> None:
    fake = FakeManager(stdout="Status:   Ready\n")
    monkeypatch.setattr(service_module, "run_command", fake)
    args = [*base_args(project_root), "service"]
    assert main([*args, "install", "--service", "demo"]) == 0
    out = capsys.readouterr().out
    assert "starts at logon" in out
    plan = build_plan(chassis, "demo")
    assert plan.definition_path.is_file()
    assert [list(command) for command in plan.install] == fake.calls
    assert main([*args, "status", "--service", "demo"]) == 0
    status = capsys.readouterr().out
    assert "registered                 yes" in status
    if plan.platform == "windows":
        assert "Ready" in status
    assert main([*args, "uninstall", "--service", "demo"]) == 0
    assert "removed" in capsys.readouterr().out
    assert not plan.definition_path.exists()


def test_failures_are_reported(chassis, project_root, capsys, monkeypatch) -> None:
    monkeypatch.setattr(service_module, "run_command", FakeManager(code=1))
    args = [*base_args(project_root), "service"]
    assert main([*args, "install", "--service", "demo"]) == int(ExitCode.ERROR)
    assert "failed: nope" in capsys.readouterr().out
    assert main([*args, "uninstall", "--service", "demo"]) == 0
    assert "was not registered" in capsys.readouterr().out
    assert main([*args, "status", "--service", "demo"]) == 0
    assert "register it with" in capsys.readouterr().out

    def broken(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("schtasks")

    monkeypatch.setattr(service_module, "run_command", broken)
    assert main([*args, "install", "--service", "demo"]) == int(ExitCode.ERROR)
    assert "could not run" in capsys.readouterr().out


def test_uninstall_stops_a_running_daemon_first(chassis, project_root, capsys, monkeypatch) -> None:
    monkeypatch.setattr(service_module, "run_command", FakeManager())
    chassis.initialize()
    chassis.start()
    assert main([*base_args(project_root), "service", "uninstall", "--service", "demo"]) == 0
    assert "asked the running daemon to stop" in capsys.readouterr().out
    assert chassis.signals.shutdown_requested


def test_a_console_less_process_gets_null_streams(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    entry_module._ensure_streams()
    assert sys.stdout is not None and sys.stderr is not None
    sys.stdout.write("swallowed")
    assert not isinstance(sys.stdout, io.StringIO)

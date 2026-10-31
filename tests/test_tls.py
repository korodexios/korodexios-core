"""https control plane: 'kdx tls init', the server, clients that verify, owner-only files."""

from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
from pathlib import Path

import pytest

from korodexios_core.cli import tls as tls_module
from korodexios_core.cli.entry import main
from korodexios_core.cli.tls import certificate_names, find_openssl, openssl_arguments
from korodexios_core.constants import ExitCode
from korodexios_core.kernel.chassis import Chassis, ChassisOptions
from korodexios_core.kernel.ipc import (
    DISCOVERY_NAME,
    HEALTH_PATH,
    STATUS_PATH,
    IpcDiscovery,
    IpcError,
    IpcOptions,
    IpcServer,
)
from korodexios_core.paths import AppPaths, restrict_to_owner

from .test_ipc import base_args

OPENSSL = find_openssl()
needs_openssl = pytest.mark.skipif(OPENSSL is None, reason="openssl is not installed")


def make_pair(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    cert, key = directory / "cert.pem", directory / "key.pem"
    command = openssl_arguments(str(OPENSSL), cert, key, certificate_names())
    subprocess.run(command, capture_output=True, check=True, timeout=60)
    return cert, key


def tls_server(tmp_path: Path, cert: Path, key: Path) -> IpcServer:
    server = IpcServer(
        status_fn=lambda: {"app": {"state": "running"}},
        shutdown_fn=lambda: None,
        state_fn=lambda: "running",
        options=IpcOptions(
            port=0,
            discovery_path=tmp_path / DISCOVERY_NAME,
            token="sekret",
            tls_cert=cert,
            tls_key=key,
        ),
    )
    server.start()
    return server


def test_certificate_names_cover_loopback_and_extras() -> None:
    names = certificate_names(["nas.lan", "192.168.1.50", "", "nas.lan"])
    assert names[:3] == ["DNS:localhost", "IP:127.0.0.1", "IP:::1"]
    assert "DNS:nas.lan" in names
    assert "IP:192.168.1.50" in names
    assert names.count("DNS:nas.lan") == 1


def test_openssl_arguments_ask_for_a_modern_self_signed_pair(tmp_path: Path) -> None:
    command = openssl_arguments("openssl", tmp_path / "c.pem", tmp_path / "k.pem", ["IP:127.0.0.1"])
    assert command[:3] == ["openssl", "req", "-x509"]
    assert "ec_paramgen_curve:prime256v1" in command
    assert "subjectAltName=IP:127.0.0.1" in command
    assert "-nodes" in command


@needs_openssl
def test_https_serves_and_a_verifying_client_trusts_the_cert(tmp_path: Path) -> None:
    cert, key = make_pair(tmp_path / "tls")
    server = tls_server(tmp_path, cert, key)
    try:
        assert server.tls is True
        assert server.as_dict()["tls"] is True
        local = IpcDiscovery.load(tmp_path / DISCOVERY_NAME)
        assert local is not None
        assert local.scheme == "https"
        assert local.cafile == str(cert)
        assert local.request(HEALTH_PATH, timeout=5)[0] == 200
        assert local.request(STATUS_PATH, timeout=5)[0] == 200
        remote = IpcDiscovery.remote(
            f"https://127.0.0.1:{local.port}", token="sekret", cafile=str(cert)
        )
        assert remote.request(STATUS_PATH, timeout=5)[0] == 200
        untrusted = IpcDiscovery.remote(f"https://127.0.0.1:{local.port}", token="sekret")
        with pytest.raises(OSError):  # urllib wraps the verify failure in URLError
            untrusted.request(STATUS_PATH, timeout=5)
        # A plain-http client is dropped quietly and the server keeps answering.
        with socket.create_connection(("127.0.0.1", local.port), timeout=5) as plain:
            plain.sendall(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            with pytest.raises((ConnectionError, OSError)):
                while plain.recv(1024):
                    pass
                raise ConnectionError("closed")
        assert local.request(HEALTH_PATH, timeout=5)[0] == 200
    finally:
        server.stop()


def test_a_missing_certificate_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(IpcError, match="cannot serve TLS") as caught:
        tls_server(tmp_path, tmp_path / "absent.pem", tmp_path / "absent-key.pem")
    assert "kdx tls init" in str(caught.value.hint)


def test_plain_discovery_files_stay_unchanged() -> None:
    plain = IpcDiscovery(pid=1, host="127.0.0.1", port=1, token="t", started_at=0.0)
    assert "scheme" not in plain.as_dict()
    assert "cafile" not in plain.as_dict()
    assert plain.ssl_context() is None
    secure = IpcDiscovery(pid=1, host="h", port=1, token="t", started_at=0.0, scheme="https")
    assert isinstance(secure.ssl_context(), ssl.SSLContext)


def test_chassis_resolves_the_default_pair_only_when_tls_is_on(project_root) -> None:
    def chassis_with(overrides: dict[str, object]) -> Chassis:
        paths = AppPaths.discover(root=str(project_root), environ={})
        return Chassis.create(
            ChassisOptions(
                home=paths.root, environ={}, cli_overrides=overrides, install_signals=False
            )
        )

    off = chassis_with({"core.log.file_enabled": False})
    assert off.tls_files() == (None, None)
    on = chassis_with({"core.log.file_enabled": False, "core.ipc.tls": True})
    cert, key = on.tls_files()
    assert cert == on.paths.state_dir / "tls" / "cert.pem"
    assert key == on.paths.state_dir / "tls" / "key.pem"
    custom = chassis_with(
        {"core.log.file_enabled": False, "core.ipc.tls": True, "core.ipc.tls_cert": "my/c.pem"}
    )
    assert custom.tls_files()[0] == Path(custom.paths.root) / "my" / "c.pem"


def test_tls_flags_reach_the_configuration(project_root, capsys) -> None:
    flags = ["--tls", "--tls-ca", "ca.pem"]
    code = main([*base_args(project_root), *flags, "config", "show", "--json"])
    assert code == int(ExitCode.SUCCESS)
    shown = json.loads(capsys.readouterr().out)
    assert shown["core.ipc.tls"]["value"] in (True, "True", "true")
    assert shown["core.ipc.tls_ca"]["value"] == "ca.pem"


@needs_openssl
def test_tls_init_writes_a_pair_and_keeps_it(project_root, capsys) -> None:
    assert main([*base_args(project_root), "tls", "init", "--host", "nas.lan"]) == 0
    out = capsys.readouterr().out
    tls_dir = project_root / "var" / "tls"
    assert (tls_dir / "cert.pem").is_file()
    assert (tls_dir / "key.pem").is_file()
    assert "DNS:nas.lan" in out
    assert "kdx run --tls" in out
    assert main([*base_args(project_root), "tls", "init"]) == 0
    assert "--force" in capsys.readouterr().out


def test_tls_init_without_openssl_prints_the_command(project_root, capsys, monkeypatch) -> None:
    monkeypatch.setattr(tls_module, "find_openssl", lambda: None)
    assert main([*base_args(project_root), "tls", "init"]) == int(ExitCode.ERROR)
    out = capsys.readouterr().out
    assert "openssl was not found" in out
    assert "req -x509" in out


def test_restrict_to_owner_limits_the_file(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("x", encoding="utf-8")
    assert restrict_to_owner(secret) is True
    if os.name == "nt":
        listing = subprocess.run(
            ["icacls", str(secret)], capture_output=True, text=True, check=True
        ).stdout
        assert "(I)" not in listing  # no inherited entries remain
    else:
        assert oct(secret.stat().st_mode & 0o777) == "0o600"
    assert restrict_to_owner(tmp_path / "missing.txt") is False

"""'kdx tls init': a self-signed certificate for the control plane, in one command.

The chassis serves https from the standard library alone, but the standard library
cannot mint a certificate, and hand-written key generation is not something a chassis
should ship. The openssl binary can, and almost every host already has one (Linux and
macOS ship it, Git for Windows bundles it), so this command drives it and writes the pair
where 'core.ipc.tls: true' looks by default. A host without openssl gets the exact
command to run instead of a half-made certificate.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..kernel.chassis import TLS_CERT_NAME, TLS_DIR_NAME, TLS_KEY_NAME
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..paths import restrict_to_owner
from .render import print_key_values, tr, write_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

CERT_DAYS = 825
"""Validity in days: the longest browsers accept for a leaf certificate."""

WINDOWS_OPENSSL = (
    r"C:\Program Files\Git\usr\bin\openssl.exe",
    r"C:\Program Files\Git\mingw64\bin\openssl.exe",
    r"C:\Program Files (x86)\Git\usr\bin\openssl.exe",
)
"""Where Git for Windows keeps openssl when it is not on PATH."""


def tls_command() -> CliCommand:
    """Return the 'kdx tls' command group."""
    return CliCommand(
        name="tls",
        help="Create the certificate for an https control plane.",
        group="core",
        show_in_wizard=False,
        subcommands=(
            CliCommand(
                name="init",
                help="Create a self-signed certificate for 'kdx run --tls'.",
                handler=_init_command,
                group="core",
                args=(
                    CliArgument(
                        ("--host",),
                        dest="hosts",
                        action="append",
                        metavar="NAME",
                        help="Extra host name or IP the certificate covers (repeatable).",
                    ),
                    CliArgument(
                        ("--force",),
                        dest="force",
                        action="store_true",
                        help="Replace an existing certificate.",
                    ),
                ),
            ),
        ),
    )


def find_openssl() -> str | None:
    """Return the openssl executable on this host, or None."""
    found = shutil.which("openssl")
    if found:
        return found
    if os.name == "nt":
        for candidate in WINDOWS_OPENSSL:
            if Path(candidate).is_file():
                return candidate
    return None


def certificate_names(extra: Sequence[str] = ()) -> list[str]:
    """Return the subjectAltName entries: loopback, this host, and *extra*."""
    names = ["DNS:localhost", "IP:127.0.0.1", "IP:::1"]
    hostname = socket.gethostname().strip()
    if hostname:
        names.append(f"DNS:{hostname}")
    for item in extra:
        text = str(item).strip()
        if not text:
            continue
        try:
            ipaddress.ip_address(text)
        except ValueError:
            names.append(f"DNS:{text}")
        else:
            names.append(f"IP:{text}")
    return list(dict.fromkeys(names))


def openssl_arguments(openssl: str, cert: Path, key: Path, names: Sequence[str]) -> list[str]:
    """Return the openssl invocation that writes a self-signed P-256 certificate."""
    return [
        openssl,
        "req",
        "-x509",
        "-newkey",
        "ec",
        "-pkeyopt",
        "ec_paramgen_curve:prime256v1",
        "-nodes",
        "-sha256",
        "-days",
        str(CERT_DAYS),
        "-subj",
        "/CN=korodexios control plane",
        "-addext",
        "subjectAltName=" + ",".join(names),
        "-keyout",
        str(key),
        "-out",
        str(cert),
    ]


def _init_command(request: CliRequest) -> int:
    chassis = cast("Chassis", request.options.get("chassis") or request.context.chassis)
    tls_dir = Path(chassis.paths.state_dir) / TLS_DIR_NAME
    cert, key = tls_dir / TLS_CERT_NAME, tls_dir / TLS_KEY_NAME
    if cert.exists() and not request.flag("force"):
        write_line(
            f"{TAG_NOTE} "
            + tr("{cert} already exists; pass --force to replace it", cert=cert)
        )
        return int(ExitCode.SUCCESS)
    names = certificate_names(request.option("hosts") or ())
    openssl = find_openssl()
    command = openssl_arguments(openssl or "openssl", cert, key, names)
    if openssl is None:
        write_line(f"{TAG_ERROR} openssl was not found on this host")
        write_line(f"{TAG_NOTE} install it (Git for Windows ships one), or run this yourself:")
        write_line(" ".join(f'"{part}"' if " " in part else part for part in command))
        return int(ExitCode.ERROR)
    tls_dir.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        write_line(f"{TAG_ERROR} " + tr("openssl could not run: {exc}", exc=exc))
        return int(ExitCode.ERROR)
    if completed.returncode != 0 or not cert.is_file() or not key.is_file():
        reason = completed.stderr.strip() or f"exit {completed.returncode}"
        write_line(f"{TAG_ERROR} " + tr("openssl failed: {reason}", reason=reason))
        return int(ExitCode.ERROR)
    restrict_to_owner(key)
    print_key_values([("certificate", cert), ("key", key), ("covers", ", ".join(names))])
    write_line(f"{TAG_OK} certificate ready; start the daemon with 'kdx run --tls'")
    write_line(
        f"{TAG_NOTE} on another machine, trust it with: kdx --tls-ca cert.pem status "
        "--target https://HOST:8123"
    )
    return int(ExitCode.SUCCESS)

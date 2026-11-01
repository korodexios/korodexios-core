"""Line endings are not drift, and durable writes put the same bytes on every platform."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from korodexios_core import fsio
from korodexios_core.kernel.audit import AuditLog
from korodexios_core.kernel.contracts import ContractRow, sha256_file, verify_contracts
from korodexios_core.text import TEXT_HASH_ALGORITHM, normalize_eol, sha256_text_file


def test_normalize_eol_rewrites_only_crlf() -> None:
    assert normalize_eol("a\r\nb\nc\rd") == "a\nb\nc\rd"
    assert normalize_eol(b"a\r\nb\r\n") == b"a\nb\n"
    assert normalize_eol("x\r\r\ny") == "x\r\ny"
    assert normalize_eol("") == ""
    assert normalize_eol(b"") == b""


def test_text_hash_ignores_line_endings_and_raw_hash_does_not(tmp_path: Path) -> None:
    lf, crlf = tmp_path / "lf.txt", tmp_path / "crlf.txt"
    lf.write_bytes(b"one\ntwo\n")
    crlf.write_bytes(b"one\r\ntwo\r\n")
    assert sha256_file(lf) != sha256_file(crlf)
    assert sha256_file(lf, text=True) == sha256_file(crlf, text=True)
    assert sha256_file(lf, text=True) == hashlib.sha256(b"one\ntwo\n").hexdigest()
    assert TEXT_HASH_ALGORITHM == "sha256-lf"


def test_crlf_split_across_blocks_is_one_line_ending(tmp_path: Path) -> None:
    from korodexios_core import text as text_module

    body = b"a" * (text_module._CHUNK - 1) + b"\r\n" + b"tail\r"
    path = tmp_path / "edge.txt"
    path.write_bytes(body)
    expected = hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()
    assert sha256_text_file(path) == expected


def test_binary_file_hashes_raw_bytes(tmp_path: Path) -> None:
    path = tmp_path / "blob.bin"
    path.write_bytes(b"\x00\r\n\x01")
    assert sha256_text_file(path) is None
    assert sha256_file(path, text=True) == sha256_file(path)


def test_verify_contracts_text_mode_accepts_line_ending_change(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_bytes(b"hello\r\nworld\r\n")
    declared = hashlib.sha256(b"hello\nworld\n").hexdigest()
    row = ContractRow(path=str(path), sha256=declared)
    assert verify_contracts([row]).drifted
    assert not verify_contracts([row], text=True).drifted
    path.write_bytes(b"hello\r\nCHANGED\r\n")
    assert verify_contracts([row], text=True).drifted


def test_atomic_write_text_keeps_lf_and_creates_parents(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "dir" / "record.json"
    fsio.atomic_write_text(target, "a\nb\n")
    assert target.read_bytes() == b"a\nb\n"
    fsio.atomic_write_text(target, "é\n", private=True, fsync=False)
    assert target.read_bytes() == "é\n".encode()
    assert [item.name for item in target.parent.iterdir()] == ["record.json"]


def test_atomic_write_failure_leaves_target_and_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "keep.json"
    target.write_bytes(b"old")
    with pytest.raises(TypeError):
        fsio.atomic_write_bytes(target, "not bytes")  # type: ignore[arg-type]
    assert target.read_bytes() == b"old"
    assert [item.name for item in tmp_path.iterdir()] == ["keep.json"]


def test_audit_log_writes_lf_only(tmp_path: Path) -> None:
    path = tmp_path / "audit.log"
    log = AuditLog(path)
    log.record("test.action", caller=None, outcome="ok")
    assert b"\r" not in path.read_bytes()

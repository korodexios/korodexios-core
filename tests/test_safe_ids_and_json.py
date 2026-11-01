"""id_path / is_safe_id keep ids inside their folder; jsonio never leaks RecursionError."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from korodexios_core import jsonio
from korodexios_core.config.loaders import load_config_file
from korodexios_core.exceptions import ConfigFileError
from korodexios_core.kernel.audit import read_audit
from korodexios_core.kernel.ipc import IpcDiscovery, _decode
from korodexios_core.paths import existing_id_path, id_path, is_safe_id

DEEP = "[" * 200_000


@pytest.mark.parametrize("ident", ["job-1", "a", "Case_7.v2", "x" * 128, "con-1", "nul_x"])
def test_plain_names_are_safe(ident: str) -> None:
    assert is_safe_id(ident)


@pytest.mark.parametrize(
    "ident",
    [
        "",
        "../x",
        "..",
        "a/b",
        "a\\b",
        "/etc/passwd",
        "C:\\x",
        "c:x",
        "a:stream",
        ".hidden",
        "trail.",
        "a..b",
        "x" * 129,
        "has space",
        "nul",
        "NUL.txt",
        "com1",
        "LPT9.log",
        "CONIN$",
        "tab\tx",
        "nul\x00x",
        "ünï",
        "a\nb",
    ],
)
def test_hostile_or_platform_unsafe_names_are_refused(ident: str) -> None:
    assert not is_safe_id(ident)


@pytest.mark.parametrize("value", [None, 5, b"abc", ["a"], Path("a")])
def test_non_strings_are_unsafe(value: object) -> None:
    assert not is_safe_id(value)


def test_id_path_is_a_direct_child(tmp_path: Path) -> None:
    result = id_path(tmp_path, "job-1")
    assert result == tmp_path / "job-1.json"
    assert result.parent == tmp_path
    assert id_path(tmp_path, "job-1", ".log").name == "job-1.log"
    assert id_path(tmp_path, "job-1", "").name == "job-1"


@pytest.mark.parametrize("ident", ["../../x", "a/b", "", "nul", "x" * 200])
def test_id_path_refuses_unsafe_ids(tmp_path: Path, ident: str) -> None:
    with pytest.raises(ValueError, match="not a valid id"):
        id_path(tmp_path, ident)


@pytest.mark.parametrize("suffix", [".", "json", "./x", "../x", ".a/b", ".a.", "/"])
def test_id_path_refuses_unsafe_suffixes(tmp_path: Path, suffix: str) -> None:
    with pytest.raises(ValueError, match="suffix"):
        id_path(tmp_path, "ok", suffix)


def test_existing_id_path_finds_files_only(tmp_path: Path) -> None:
    folder = tmp_path / "records"
    folder.mkdir()
    (folder / "a.json").write_text("{}", encoding="utf-8")
    (folder / "dir.json").mkdir()
    (tmp_path / "secret.json").write_text("{}", encoding="utf-8")
    assert existing_id_path(folder, "a") == folder / "a.json"
    assert existing_id_path(folder, "missing") is None
    assert existing_id_path(folder, "dir") is None
    assert existing_id_path(folder, "../secret") is None
    assert existing_id_path(folder, "..\\secret") is None


def test_loads_parses_normal_json() -> None:
    assert jsonio.loads('{"a": [1, 2]}') == {"a": [1, 2]}
    assert jsonio.loads(b'{"a": 1}') == {"a": 1}


def test_loads_turns_depth_overflow_into_value_error() -> None:
    with pytest.raises(jsonio.JsonLimitError, match="nested too deeply"):
        jsonio.loads(DEEP)
    with pytest.raises(ValueError, match="nested too deeply"):
        jsonio.loads(DEEP.encode("utf-8"))


def test_loads_keeps_ordinary_errors_as_value_errors() -> None:
    with pytest.raises(ValueError):
        jsonio.loads("{not json")
    with pytest.raises(ValueError):
        jsonio.loads(b"\xff\xfe\x00bad")


def test_loads_honours_max_bytes() -> None:
    jsonio.loads("[1]", max_bytes=3)
    with pytest.raises(jsonio.JsonLimitError, match="byte limit"):
        jsonio.loads("[1, 2]", max_bytes=3)
    with pytest.raises(jsonio.JsonLimitError):
        jsonio.loads('"é"', max_bytes=3)
    with pytest.raises(jsonio.JsonLimitError):
        jsonio.loads(b"[1, 2]", max_bytes=3)


def test_load_path_reads_bounded_and_propagates_oserror(tmp_path: Path) -> None:
    good = tmp_path / "ok.json"
    good.write_text('{"a": 1}', encoding="utf-8")
    assert jsonio.load_path(good) == {"a": 1}
    assert jsonio.load_path(str(good), max_bytes=100) == {"a": 1}
    big = tmp_path / "big.json"
    big.write_text("[" + "1," * 5000 + "1]", encoding="utf-8")
    with pytest.raises(jsonio.JsonLimitError):
        jsonio.load_path(big, max_bytes=100)
    deep = tmp_path / "deep.json"
    deep.write_text(DEEP, encoding="utf-8")
    with pytest.raises(ValueError):
        jsonio.load_path(deep)
    with pytest.raises(OSError):
        jsonio.load_path(tmp_path / "missing.json")


def test_ipc_decode_survives_deep_body() -> None:
    assert _decode(DEEP.encode("utf-8")) == DEEP
    assert _decode(b'{"a": 1}') == {"a": 1}


def test_discovery_file_with_deep_json_is_unreadable_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "chassis.ipc"
    path.write_text(DEEP, encoding="utf-8")
    assert IpcDiscovery.load(path) is None


def test_audit_reader_skips_deep_lines(tmp_path: Path) -> None:
    path = tmp_path / "audit.log"
    path.write_text(DEEP + "\n" + json.dumps({"action": "ok"}) + "\n", encoding="utf-8")
    assert read_audit(path) == [{"action": "ok"}]


def test_config_loader_reports_deep_json_as_config_error(tmp_path: Path) -> None:
    path = tmp_path / "kdx.json"
    path.write_text(DEEP, encoding="utf-8")
    with pytest.raises(ConfigFileError, match="not valid JSON"):
        load_config_file(path)


def test_daemon_answers_a_deeply_nested_body_with_a_clean_error(tmp_path: Path) -> None:
    from korodexios_core.kernel.ipc import RPC_PATH, TOKEN_HEADER
    from tests.test_ipc_hardening import raw_call, start

    server, ran = start(tmp_path)
    port = server.discovery.port  # type: ignore[union-attr]
    headers = {"Host": f"127.0.0.1:{port}", TOKEN_HEADER: "sekret"}
    try:
        code, payload = raw_call(
            server, RPC_PATH, method="POST", headers=headers, body=DEEP.encode("utf-8")
        )
        assert 400 <= code < 500
        assert "error" in payload
        assert ran == []
        assert raw_call(server, "/api/v1/status", headers=headers)[0] == 200
    finally:
        server.stop()

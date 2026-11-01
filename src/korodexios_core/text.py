"""Line-ending helpers: one logical record, one set of bytes, on every platform."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final, TypeVar

TEXT_HASH_ALGORITHM: Final[str] = "sha256-lf"
"""Name to store beside a hash made with ``text=True``, so it is never mistaken for raw."""

_CHUNK: Final[int] = 65_536

AnyText = TypeVar("AnyText", str, bytes)


def normalize_eol(data: AnyText) -> AnyText:
    """Return *data* with every CRLF replaced by LF; a lone CR is left as it is.

    Only the Windows pair is rewritten, so two texts that differ in nothing but their
    line endings become equal, while a stray CR (a progress bar, a classic-Mac file) never
    hides a real difference. It is one pass: the pairs present in *data* are rewritten,
    and a CR that only becomes part of a pair afterwards is not.
    """
    if isinstance(data, str):
        return data.replace("\r\n", "\n")
    return data.replace(b"\r\n", b"\n")


def sha256_text_file(path: Path) -> str | None:
    """Return the hex SHA-256 of *path* with CRLF read as LF, or None for a binary file.

    The file is streamed, so a CRLF split across two blocks still counts as one line
    ending. A file holding a NUL byte is binary and returns None: its line-ending bytes
    are data, and the caller hashes the raw bytes instead (``sha256_file``).
    """
    digest = hashlib.sha256()
    carry = b""
    with path.open("rb") as handle:
        block = handle.read(_CHUNK)
        while block:
            if b"\x00" in block:
                return None
            block = carry + block
            carry = b""
            if block.endswith(b"\r"):
                block, carry = block[:-1], b"\r"
            digest.update(normalize_eol(block))
            block = handle.read(_CHUNK)
    digest.update(carry)
    return digest.hexdigest()


__all__ = ["TEXT_HASH_ALGORITHM", "normalize_eol", "sha256_text_file"]

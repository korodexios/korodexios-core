"""The ``embed`` panel: a page another module serves, framed inside the shell.

The declaration is data (``src``, ``height``); the shell draws a sandboxed same-origin
iframe and hands the session token over by ``postMessage``, never in the URL. A mount is
framable only when its module registered it with ``embeddable=True``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

HEIGHT_MIN, HEIGHT_MAX, HEIGHT_DEFAULT = 120, 2000, 360
_SRC = re.compile(r"^/[A-Za-z0-9._~\-/%]*$")


def declare(raw: Mapping[str, Any], panel: dict[str, Any], build: Any, ident: str) -> bool:
    """Fill *panel* from the raw declaration; report a problem to *build* and return False."""
    src = str(raw.get("src") or "")
    if not _SRC.match(src) or src.startswith("//") or ".." in src or src.startswith("/api/"):
        build.issue(
            "src must be a path on this daemon such as '/player/' (a static mount, "
            "never an /api/ route or another host)",
            ident,
        )
        return False
    height = raw.get("height")
    number = height if isinstance(height, int) and not isinstance(height, bool) else HEIGHT_DEFAULT
    panel["src"] = src
    panel["height"] = min(HEIGHT_MAX, max(HEIGHT_MIN, number))
    return True

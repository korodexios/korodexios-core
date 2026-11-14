"""The ``embed`` panel: a page another module serves, framed inside the shell.

The declaration is data (``src``, ``height``); the shell draws a sandboxed same-origin
iframe and hands the session over by ``postMessage``, never in the URL. A mount is
framable only when its module registered it with ``embeddable=True``.

Embed protocol, version 1 (:data:`PROTOCOL`)::

    frame  -> host   {type: "kdx-embed-ready"}
    host   -> frame  {type: "kdx-embed-token", version: 1, token: "<string>", cookie: <bool>}

- The frame asks once, when it starts; it keeps at most one question open and never asks
  again for a token the server has already refused.
- ``token`` non-empty: send it as ``X-Kdx-Token`` on every request.
- ``token`` empty and ``cookie: true``: the browser already carries the HttpOnly session
  cookie (``core.ipc.ui_token_cookie``); same-origin requests are authenticated, send no header.
- ``token`` empty and ``cookie: false``: nobody is signed in; show a signed-out state.
- Both sides check the message: the host answers only its own iframe (``event.source``) on its
  own origin, and the frame accepts only ``window.parent`` on ``location.origin``.

``version`` and ``cookie`` were added after the first release; a page that reads only ``token``
behaves as before. The shared app client (``web/src/kdx/auth.ts`` of the ``react`` and ``vue``
tiers) implements both sides.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .i18n import N_

HEIGHT_MIN, HEIGHT_MAX, HEIGHT_DEFAULT = 120, 2000, 360
_SRC = re.compile(r"^/[A-Za-z0-9._~\-/%]*$")

PROTOCOL_VERSION = 1
READY_MESSAGE = "kdx-embed-ready"
TOKEN_MESSAGE = "kdx-embed-token"
PROTOCOL: dict[str, Any] = {
    "version": PROTOCOL_VERSION,
    "ask": {"type": READY_MESSAGE},
    "answer": {"type": TOKEN_MESSAGE, "version": PROTOCOL_VERSION, "token": "string",
               "cookie": "bool"},
    "rules": [
        N_("the frame posts the ask to window.parent with targetOrigin location.origin"),
        N_("the host answers only its own iframe (event.source) on its own origin"),
        N_("the frame accepts only window.parent on location.origin"),
        N_("token set: send it as X-Kdx-Token"),
        N_("token empty and cookie true: the session cookie authenticates; send no header"),
        N_("token empty and cookie false: nobody is signed in"),
        N_("one ask outstanding at a time; never retry a token the server refused"),
    ],
}
"""The embed handshake as data: ``kdx ui guide --json`` publishes it under ``embed``."""


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

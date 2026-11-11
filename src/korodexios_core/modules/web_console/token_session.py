"""How long the console may remember its token, and the HttpOnly session cookie.

The server sets the ceiling (``core.ipc.ui_token_store`` / ``_ttl`` / ``_cookie``); the console
chooses inside it. ``GET /api/v1/ui/token-policy`` holds no secret, so the token gate can read it
before anyone has signed in. With the cookie on, ``POST /api/v1/ui/session`` trades a valid
token for an HttpOnly, SameSite=Lax cookie that no script can read; the header and ``Bearer``
forms keep working in every mode.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from typing import Any

from ...config.layered import LayeredConfig
from ...kernel.ipc import TOKEN_HEADER, IpcRequest, IpcResponse, IpcRoute
from ...kernel.ipc_hosts import SESSION_COOKIE
from ...validate import parse_duration

TOKEN_POLICY_PATH = "/api/v1/ui/token-policy"
SESSION_PATH = "/api/v1/ui/session"
TOKEN_STORES = ("always", "ttl", "tab", "none")
DEFAULT_TOKEN_TTL = 12 * 3600  # 'ttl' without core.ipc.ui_token_ttl
COOKIE_FOREVER = 400 * 86400  # the longest a browser keeps a cookie


def _int(value: Any) -> int:
    """Return *value* as a non-negative int; anything else is 0."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _cookie(token: str, max_age: int, *, clear: bool = False) -> str:
    """Return the Set-Cookie line for the session: HttpOnly, SameSite=Lax, path-wide."""
    parts = [
        f"{SESSION_COOKIE}={urllib.parse.quote(token, safe='')}",
        "Path=/", "HttpOnly", "SameSite=Lax",
    ]
    if clear:
        parts.append("Max-Age=0")
    elif max_age > 0:
        parts.append(f"Max-Age={max_age}")
    return "; ".join(parts)


def token_policy(config: LayeredConfig) -> dict[str, Any]:
    """Return how long the console may remember its token (the server sets the ceiling)."""
    store = config.get_str("core.ipc.ui_token_store", "always").strip().lower()
    if store not in TOKEN_STORES:
        store = "always"
    seconds = 0
    text = config.get_str("core.ipc.ui_token_ttl", "").strip()
    if text:
        try:
            seconds = max(0, int(parse_duration(text)))
        except ValueError:
            seconds = 0
    if store == "ttl" and not seconds:
        seconds = DEFAULT_TOKEN_TTL
    if store in ("tab", "none"):
        seconds = 0
    return {
        "token_store": store,
        "ttl_seconds": seconds,
        "cookie": config.get_bool("core.ipc.ui_token_cookie", False),
    }


def _session(request: IpcRequest, policy: dict[str, Any]) -> Any:
    """Check, set or clear the session cookie that stands in for the token."""
    if not policy["cookie"]:
        return 403, {"error": "cookie sessions are off", "hint": "core.ipc.ui_token_cookie"}
    if request.method == "DELETE":
        return IpcResponse.text(
            json.dumps({"cleared": True}),
            content_type="application/json",
            headers=(("Set-Cookie", _cookie("", 0, clear=True)),),
        )
    if request.caller is None:
        return 401, {"error": "a valid token is needed first"}
    if request.method == "GET":
        return {"authenticated": True, "user": request.caller.name}
    header = str((request.headers or {}).get(TOKEN_HEADER) or "").strip()
    if not header:
        return 400, {"error": f"send the token in the {TOKEN_HEADER} header"}
    seconds = _int(request.argument("seconds", 0))
    cap = int(policy["ttl_seconds"])
    if cap:
        seconds = min(seconds, cap) if seconds else cap
    if not request.argument("persistent", True):
        seconds = 0
    elif not seconds:
        seconds = COOKIE_FOREVER
    if policy["token_store"] in ("tab", "none"):
        seconds = 0  # without Max-Age the browser drops the cookie when it closes
    return IpcResponse.text(
        json.dumps({"authenticated": True, "max_age": seconds}),
        content_type="application/json",
        headers=(("Set-Cookie", _cookie(header, seconds)),),
    )


def token_routes(module: str, config: Callable[[], LayeredConfig]) -> tuple[IpcRoute, ...]:
    """Return the two routes; neither needs a token to be reached, the session checks its own."""
    return (
        IpcRoute(
            path=TOKEN_POLICY_PATH,
            handler=lambda request: token_policy(config()),
            auth=False,
            module=module,
            summary="how long the console may remember its token "
            "({token_store, ttl_seconds, cookie}); holds no secret",
        ),
        IpcRoute(
            path=SESSION_PATH,
            handler=lambda request: _session(request, token_policy(config())),
            methods=("GET", "POST", "DELETE"),
            auth=False,
            module=module,
            summary="the browser session cookie (core.ipc.ui_token_cookie): GET checks it, "
            "POST with a valid token sets it, DELETE clears it",
        ),
    )

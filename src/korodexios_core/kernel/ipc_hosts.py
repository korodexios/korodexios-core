"""Which ``Host`` names and cookies the control plane trusts.

Pure functions with no listener behind them: the server asks, these answer. Kept apart from
``ipc`` so the browser-facing rules (DNS rebinding, the console's session cookie) can be read
and tested on their own.
"""

from __future__ import annotations

import contextlib
import functools
import ipaddress
import socket
import urllib.parse
from collections.abc import Iterable, Mapping
from typing import Any

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
SESSION_COOKIE = "kdx_session"
"""Name of the HttpOnly cookie that stands in for the token when ui_token_cookie is on."""


def is_loopback_host(host: str) -> bool:
    """Return True when only this machine can reach the bind address."""
    return str(host).strip() in LOOPBACK_HOSTS


def host_name(host_header: str) -> str:
    """Return the host part of a 'Host' header: 'a:1' -> 'a', '[::1]:1' -> '::1'."""
    text = host_header.strip().lower()
    if text.startswith("["):
        return text[1:].split("]", 1)[0]
    return text.rsplit(":", 1)[0] if text.count(":") == 1 else text


def is_ip_literal(name: str) -> bool:
    """Return True when *name* is an IPv4 or IPv6 address, not a DNS name."""
    try:
        ipaddress.ip_address(str(name).strip().strip("[]").split("%", 1)[0])
    except ValueError:
        return False
    return True


@functools.lru_cache(maxsize=1)
def own_host_names() -> frozenset[str]:
    """Return the names this machine calls itself: its hostname and '<host>.local'.

    Only the local hostname is read - never DNS. A reverse lookup ('socket.getfqdn')
    can stall for seconds (it did on macOS), and it would run inside the first request
    that carries a foreign Host header. A DNS name the machine is reached by belongs in
    core.ipc.allowed_hosts.
    """
    names: set[str] = set()
    with contextlib.suppress(OSError):
        host = socket.gethostname().strip().lower()
        short = host.split(".", 1)[0]
        for name in {host, short}:
            if name:
                names.update({name, f"{name}.local"})
    return frozenset(names)


def host_refusal(host_text: str, *, loopback_bind: bool, allowed: Iterable[str]) -> str | None:
    """Return why the 'Host' header *host_text* must be refused, or None to serve it.

    A loopback bind answers loopback names only. On a LAN bind a rebinding page sends its own
    name as Host and Origin alike, so only the names this machine really answers to tell the
    two apart: address literals are safe (rebinding needs a DNS name), so are this machine's
    own names (listing a first domain must not lock out the bare host name that worked
    before), and any other name must be in *allowed*.
    """
    name = host_name(host_text)
    if loopback_bind:
        if not is_loopback_host(name):
            return f"Host {host_text!r} is not a loopback name for a loopback control plane"
        return None
    listed = {str(item).strip().lower() for item in allowed if str(item).strip()}
    listed |= {host_name(item) for item in listed}
    if listed:
        if not (is_loopback_host(name) or name in listed or name in own_host_names()):
            return f"Host {host_text!r} is not in core.ipc.allowed_hosts"
    elif not (is_loopback_host(name) or is_ip_literal(name) or name in own_host_names()):
        return (
            f"Host {host_text!r} is not an address or a name of this machine; "
            "list it in core.ipc.allowed_hosts"
        )
    return None


def origin_refusal(origin_text: str, host_text: str) -> str | None:
    """Return why the 'Origin' header must be refused for this 'Host', or None."""
    parsed = urllib.parse.urlsplit(origin_text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return f"Origin {origin_text!r} is not a web origin this control plane serves"
    if not host_text or parsed.netloc.lower() != host_text.lower():
        return f"Origin {origin_text!r} is not this control plane's own origin"
    return None


def cookie_token(headers: Mapping[str, str] | Any) -> str:
    """Return the token in the session cookie, or an empty string."""
    for part in str(headers.get("Cookie") or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE:
            return urllib.parse.unquote(value.strip())
    return ""

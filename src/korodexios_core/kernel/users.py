"""Named users with roles: who may read, run, or administer a chassis over its control plane.

One shared token is one operator. A team needs each person to hold a token of their own,
with only the rights their work calls for, and a record of who did what. This module is
that store: users live in '<durable_dir>/users.json', each with a role and the SHA-256 of
a token that is shown exactly once, when it is minted. The file never holds a token in
clear, so a copy of it lets no one in; it is restricted to its owner all the same.

Roles are ordered - ``viewer`` < ``contributor`` < ``operator`` < ``admin``:

- viewer: status, logs, configuration, events, jobs - reading only;
- contributor: also calls the module routes that declare ``role="contributor"`` (a
  drop-box: hand something in, operate nothing); runs no command;
- operator: also runs module commands, in the foreground or as jobs;
- admin: also runs commands marked 'danger', reloads, stops the daemon.

The shared ``core.ipc.token`` keeps working and acts as the built-in admin ``owner``, so
a single-person setup needs no user file at all. Users are managed in the terminal
('kdx users ...'), which edits the file directly; a running daemon notices the change on
the next request. Every role runs with the daemon's own operating-system account: roles
decide what the control plane lets a caller ask for, not what the process may touch.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .. import jsonio
from ..fsio import atomic_write_text

ROLES: Final[tuple[str, ...]] = ("viewer", "contributor", "operator", "admin")
"""Every role, weakest first; a role holds every right of the roles before it."""

OWNER_NAME: Final[str] = "owner"
"""The name the shared core.ipc.token answers to; it always holds the admin role."""

USERS_FILE_NAME: Final[str] = "users.json"
TOKEN_BYTES: Final[int] = 32
NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,31}$")


class UserError(ValueError):
    """Raised for a bad user name, an unknown role, or a user that does not exist."""


def role_rank(role: str) -> int:
    """Return the position of *role* in ROLES; an unknown role ranks below viewer."""
    try:
        return ROLES.index(str(role))
    except ValueError:
        return -1


def role_allows(role: str, required: str) -> bool:
    """Return True when *role* holds at least the rights of *required*."""
    return role_rank(role) >= role_rank(required) >= 0


def hash_token(token: str) -> str:
    """Return the stored form of a token: its SHA-256, hex encoded.

    Tokens are 256 random bits, so a fast hash is the right one: there is no dictionary
    to grind through, and the comparison stays cheap on every request.
    """
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Caller:
    """Who is asking, as the control plane established it for one request."""

    name: str
    role: str
    source: str = "token"
    """'shared' for core.ipc.token, 'user' for a named user, 'anonymous' with auth off."""

    def allows(self, required: str) -> bool:
        """Return True when this caller holds at least *required*."""
        return role_allows(self.role, required)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "role": self.role, "source": self.source}


_CALLER: ContextVar[Caller | None] = ContextVar("kdx_caller", default=None)


def current_caller() -> Caller | None:
    """Return the caller of the control-plane request this thread is serving, if any."""
    return _CALLER.get()


@contextlib.contextmanager
def acting_as(caller: Caller | None) -> Iterator[None]:
    """Run the block on behalf of *caller*; the chassis reads it to authorize and audit."""
    token = _CALLER.set(caller)
    try:
        yield
    finally:
        _CALLER.reset(token)


@dataclass(frozen=True, slots=True)
class UserRecord:
    """One stored user; the token itself is never kept."""

    name: str
    role: str
    token_hash: str
    created_at: float

    def as_dict(self, *, secret: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "role": self.role,
            "created_at": round(self.created_at, 3),
        }
        if secret:
            payload["token_sha256"] = self.token_hash
        return payload


class UserStore:
    """Reads and writes '<durable_dir>/users.json'; safe across threads and processes.

    Writes go to a sibling file restricted to its owner and are renamed into place, so a
    daemon reading the file never sees half of it. Reads are cached on the file's
    modification stamp, so a 'kdx users add' in another terminal reaches a running daemon
    on its next request without a reload.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._stamp: tuple[int, int] | None = None
        self._users: dict[str, UserRecord] = {}

    def users(self) -> list[UserRecord]:
        """Return every stored user, sorted by name."""
        with self._lock:
            self._refresh()
            return sorted(self._users.values(), key=lambda user: user.name)

    def get(self, name: str) -> UserRecord | None:
        with self._lock:
            self._refresh()
            return self._users.get(str(name))

    def add(self, name: str, role: str) -> str:
        """Create *name* with *role*; return the new token, which is never stored in clear."""
        clean = validate_name(name)
        validate_role(role)
        with self._lock:
            self._refresh()
            if clean in self._users:
                raise UserError(
                    f"user {clean!r} already exists; 'kdx users rotate {clean}' issues a new token"
                )
            token = secrets.token_urlsafe(TOKEN_BYTES)
            self._users[clean] = UserRecord(clean, role, hash_token(token), time.time())
            self._write()
        return token

    def remove(self, name: str) -> None:
        with self._lock:
            self._refresh()
            if self._users.pop(str(name), None) is None:
                raise UserError(f"no user named {name!r}")
            self._write()

    def rotate(self, name: str) -> str:
        """Replace the token of *name*; the old one stops working at once."""
        with self._lock:
            self._refresh()
            user = self._users.get(str(name))
            if user is None:
                raise UserError(f"no user named {name!r}")
            token = secrets.token_urlsafe(TOKEN_BYTES)
            self._users[user.name] = UserRecord(
                user.name, user.role, hash_token(token), user.created_at
            )
            self._write()
        return token

    def set_role(self, name: str, role: str) -> UserRecord:
        validate_role(role)
        with self._lock:
            self._refresh()
            user = self._users.get(str(name))
            if user is None:
                raise UserError(f"no user named {name!r}")
            updated = UserRecord(user.name, role, user.token_hash, user.created_at)
            self._users[user.name] = updated
            self._write()
        return updated

    def authenticate(self, token: str | None) -> UserRecord | None:
        """Return the user *token* belongs to, comparing every hash in constant time."""
        presented = str(token or "")
        if not presented:
            return None
        digest = hash_token(presented)
        found: UserRecord | None = None
        for user in self.users():
            if hmac.compare_digest(digest, user.token_hash):
                found = user
        return found

    def _refresh(self) -> None:
        try:
            stat = self.path.stat()
        except OSError:
            self._stamp, self._users = None, {}
            return
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._stamp:
            return
        try:
            raw = jsonio.load_path(self.path)
        except (OSError, ValueError) as exc:
            raise UserError(f"cannot read {self.path}: {exc}") from exc
        users: dict[str, UserRecord] = {}
        for item in raw.get("users", []) if isinstance(raw, dict) else []:
            try:
                record = UserRecord(
                    name=validate_name(item["name"]),
                    role=validate_role(item["role"]),
                    token_hash=str(item["token_sha256"]),
                    created_at=float(item.get("created_at", 0.0)),
                )
            except (KeyError, TypeError, ValueError):
                continue
            users[record.name] = record
        self._users, self._stamp = users, stamp

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": 1,
            "users": [user.as_dict(secret=True) for user in self._users.values()],
        }
        atomic_write_text(
            self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n", private=True
        )
        stat = self.path.stat()
        self._stamp = (stat.st_mtime_ns, stat.st_size)


def validate_name(name: Any) -> str:
    """Return *name* when it is a lower-case user name of 1-32 characters."""
    text = str(name or "").strip()
    if text == OWNER_NAME:
        raise UserError(f"{OWNER_NAME!r} is the shared token's name; pick another")
    if not NAME_PATTERN.match(text):
        raise UserError(
            f"user name {text!r} must be 1-32 characters: a-z, 0-9, '.', '_', '-', "
            "starting with a letter or digit"
        )
    return text


def validate_role(role: Any) -> str:
    """Return *role* when it is one of ROLES."""
    text = str(role or "").strip()
    if text not in ROLES:
        raise UserError(f"role {text!r} is not one of {', '.join(ROLES)}")
    return text

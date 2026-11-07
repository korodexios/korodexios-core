"""What the host says about users and containers - read only, never changed.

A rootless container engine (Podman, rootless Docker) needs a range of subordinate user ids
that actually exists in the user namespace the process runs in. On a plain host any range
works; inside an LXC container or another user namespace the range must lie inside the ids that
namespace maps, and it must not collide with ids other users or services already own. The
chassis therefore *reads* the facts (this user's uid and gid, the namespace map, ``/etc/passwd``,
``/etc/subuid``, ``/etc/subgid``, the engines on PATH) and *proposes* a range that is free;
it never edits system files and never touches an id someone else uses - changing
``/etc/subuid`` is the administrator's decision, so the proposal is a command to read and run.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

PREFERRED_RANGE = 65536
STANDARD_FLOOR = 100000
"""Where useradd starts subordinate ranges; used when the namespace has room for it."""
ACCOUNT_FLOOR = 10000
"""Inside a small namespace, ids below this stay free for accounts created later."""
MIN_USEFUL_RANGE = 1000
DEFAULT_CONTAINER_UID = 10001
_UNLIMITED = 2**32 - 1

ReadText = Callable[[str], "str | None"]


def read_text(path: str) -> str | None:
    """Return a small system file's text, or None when it is absent or unreadable."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def parse_id_map(text: str | None) -> list[tuple[int, int]]:
    """Return the ids a namespace maps as (first id, count) pairs from /proc/self/uid_map."""
    ranges: list[tuple[int, int]] = []
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            ranges.append((int(parts[0]), int(parts[2])))
    return ranges


def parse_subids(text: str | None) -> list[tuple[str, int, int]]:
    """Return /etc/subuid or /etc/subgid entries as (name, first id, count)."""
    entries: list[tuple[str, int, int]] = []
    for line in (text or "").splitlines():
        parts = line.strip().split(":")
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            entries.append((parts[0], int(parts[1]), int(parts[2])))
    return entries


def passwd_ids(text: str | None) -> dict[int, str]:
    """Return the ids /etc/passwd (or /etc/group) assigns, as id -> name."""
    found: dict[int, str] = {}
    for line in (text or "").splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[2].isdigit():
            found[int(parts[2])] = parts[0]
    return found


def _subtract(
    free: list[tuple[int, int]], first: int, count: int
) -> list[tuple[int, int]]:
    """Remove [first, first + count) from the free intervals (start, end-exclusive)."""
    end = first + count
    result: list[tuple[int, int]] = []
    for start, stop in free:
        if end <= start or first >= stop:
            result.append((start, stop))
            continue
        if first > start:
            result.append((start, first))
        if end < stop:
            result.append((end, stop))
    return result


def propose_range(
    mapped: list[tuple[int, int]],
    taken_ids: set[int],
    foreign: list[tuple[str, int, int]],
    user: str,
    floor: int = 0,
) -> tuple[int, int] | None:
    """Return the largest block inside *mapped* that nobody else uses, as (first, count).

    Ids held by an account (*taken_ids*) and ranges granted to other users (*foreign*) are
    never proposed, and ids below *floor* are kept free for accounts created later (when
    that leaves nothing, the floor is dropped). None when nothing of at least
    MIN_USEFUL_RANGE ids is free.
    """
    if floor:
        preferred = propose_range(mapped, taken_ids, foreign, user)
        with_floor = _largest_free(mapped, taken_ids, foreign, user, floor)
        return with_floor or preferred
    return _largest_free(mapped, taken_ids, foreign, user, 0)


def _largest_free(
    mapped: list[tuple[int, int]],
    taken_ids: set[int],
    foreign: list[tuple[str, int, int]],
    user: str,
    floor: int,
) -> tuple[int, int] | None:
    free = [(first, first + count) for first, count in mapped]
    if floor:
        free = _subtract(free, 0, floor)
    for taken in sorted(taken_ids):
        free = _subtract(free, taken, 1)
    for name, first, count in foreign:
        if name != user:
            free = _subtract(free, first, count)
    free = [(start, stop) for start, stop in free if stop - start >= MIN_USEFUL_RANGE]
    if not free:
        return None
    start, stop = max(free, key=lambda block: block[1] - block[0])
    return start, min(stop - start, PREFERRED_RANGE)


def _inside(mapped: list[tuple[int, int]], first: int, count: int) -> bool:
    return any(start <= first and first + count <= start + size for start, size in mapped)


def probe_container_host(
    *,
    read: ReadText = read_text,
    which: Callable[[str], str | None] = shutil.which,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    ids: tuple[int, int] | None = None,
    user: str | None = None,
) -> dict[str, Any]:
    """Return who we are, how ids are mapped, and what a rootless engine would need."""
    env = os.environ if environ is None else environ
    system = sys.platform if platform is None else platform
    if ids is not None:
        uid, gid = ids
    else:
        get_uid = getattr(os, "getuid", None)
        get_gid = getattr(os, "getgid", None)
        uid, gid = (get_uid(), get_gid()) if get_uid and get_gid else (-1, -1)  # Windows: none
    name = user or env.get("USER") or env.get("USERNAME") or ""
    mapped = parse_id_map(read("/proc/self/uid_map")) or [(0, _UNLIMITED)]
    namespaced = mapped != [(0, _UNLIMITED)] and mapped[0][1] < _UNLIMITED
    passwd = passwd_ids(read("/etc/passwd"))
    if uid >= 0 and not name:
        name = passwd.get(uid, "")
    own = [entry for entry in parse_subids(read("/etc/subuid")) if entry[0] in (name, str(uid))]
    mine = (name, str(uid))
    foreign = [e for e in parse_subids(read("/etc/subuid")) if e[0] not in mine]
    own_gid = [e for e in parse_subids(read("/etc/subgid")) if e[0] in mine]
    foreign_gid = [e for e in parse_subids(read("/etc/subgid")) if e[0] not in mine]
    engines = [engine for engine in ("docker", "podman") if which(engine)]
    problems: list[str] = []
    advice: dict[str, Any] = {}
    rootless_ok = True
    if uid == 0:
        rootless_ok = True  # root needs no subordinate ids
    elif uid > 0 and "podman" in engines:
        usable = [e for e in own if _inside(mapped, e[1], e[2])]
        if not own:
            rootless_ok = False
            problems.append(f"{name or uid} has no entry in /etc/subuid")
        elif not usable:
            rootless_ok = False
            problems.append(
                "the subordinate id range of "
                f"{name or uid} ({own[0][1]}-{own[0][1] + own[0][2] - 1}) lies outside the "
                "ids this user namespace maps "
                + ", ".join(f"{s}-{s + c - 1}" for s, c in mapped)
            )
        if not rootless_ok:
            roomy = _inside(mapped, STANDARD_FLOOR, PREFERRED_RANGE)
            floor = STANDARD_FLOOR if roomy else ACCOUNT_FLOOR
            proposal = propose_range(mapped, set(passwd), foreign, name, floor)
            gid_proposal = propose_range(
                mapped, set(passwd_ids(read("/etc/group"))), foreign_gid, name, floor
            )
            if proposal and gid_proposal:
                advice = _advice(name or str(uid), own, own_gid, proposal, gid_proposal)
            else:
                problems.append("no free block of ids is left in the namespace to propose")
    return {
        "platform": system,
        "user": {"name": name, "uid": uid, "gid": gid, "root": uid == 0},
        "namespace": {
            "mapped": [{"first": s, "count": c} for s, c in mapped],
            "restricted": namespaced,
        },
        "subuid": [{"name": n, "first": s, "count": c} for n, s, c in own],
        "engines": engines,
        "rootless_ready": rootless_ok,
        "problems": problems,
        "advice": advice,
        "container_user": {"uid": uid if uid > 0 else DEFAULT_CONTAINER_UID,
                           "gid": gid if gid > 0 else DEFAULT_CONTAINER_UID},
    }


def _advice(
    name: str,
    own: list[tuple[str, int, int]],
    own_gid: list[tuple[str, int, int]],
    uid_block: tuple[int, int],
    gid_block: tuple[int, int],
) -> dict[str, Any]:
    first, count = uid_block
    gfirst, gcount = gid_block
    parts = ["sudo usermod"]
    for _, start, size in own:
        parts.append(f"--del-subuids {start}-{start + size - 1}")
    for _, start, size in own_gid:
        parts.append(f"--del-subgids {start}-{start + size - 1}")
    parts.append(f"--add-subuids {first}-{first + count - 1}")
    parts.append(f"--add-subgids {gfirst}-{gfirst + gcount - 1} {name}")
    fallback = ""
    if len(own) == 1 and len(own_gid) == 1:
        # usermod refuses while the user has running processes (their own login); the two
        # lines can be replaced directly, touching nothing else in the files
        (_, ustart, usize), (_, gstart, gsize) = own[0], own_gid[0]
        fallback = (
            f"sudo sed -i 's/^{name}:{ustart}:{usize}$/{name}:{first}:{count}/' /etc/subuid"
            f" && sudo sed -i 's/^{name}:{gstart}:{gsize}$/{name}:{gfirst}:{gcount}/' /etc/subgid"
        )
    return {
        "subuid": {"first": first, "count": count},
        "subgid": {"first": gfirst, "count": gcount},
        "command": " ".join(parts),
        "fallback": fallback,
        "note": "chosen from ids no account or other user's range uses, keeping the low ids free "
        "for accounts created later; review it, the chassis never edits /etc/subuid itself. "
        "With fewer than 65536 ids set ignore_chown_errors = \"true\" under "
        "[storage.options.overlay] in ~/.config/containers/storage.conf; inside an LXC the "
        "real fix is a wider idmap on the LXC host.",
        "short_range": count < PREFERRED_RANGE,
    }

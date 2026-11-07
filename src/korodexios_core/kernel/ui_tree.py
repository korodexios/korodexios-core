"""Tree nodes: the one shape every side-list tree route answers with.

A module declares ``[[ui.sections]]`` in its ``[ui]`` table; each section names a
control-plane route that returns the nodes of its tree. The route is the module's own, so
the chassis knows nothing about what the nodes mean. This file only fixes the shape, so a
shell can draw any module's tree and an agent can read it::

    {"nodes": [{"id": "c0412", "label": "Case 0412", "meta": "review", "tone": "warn",
                "has_children": true, "opens": "case-detail"}]}

``id`` is unique inside the section. A node with ``has_children`` is fetched again with
``?parent=<id>`` when a person opens it, so a large tree is never sent whole. ``opens``
names the panel of the module that shows the node when it is selected.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .ui_manifest import TONES

MAX_NODES = 500
MAX_LABEL = 200
_ID = re.compile(r"^[A-Za-z0-9_.:/\-]{1,96}$")
_PANEL = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")


class TreeError(ValueError):
    """A node was not acceptable; the message says which one and why."""


def node(
    node_id: str,
    label: str,
    *,
    meta: str = "",
    tone: str = "",
    has_children: bool = False,
    opens: str = "",
    more: bool = False,
    next: str = "",
) -> dict[str, Any]:
    """Return one valid node, or raise :class:`TreeError` saying what to fix.

    A *more* node is the "there is more below" marker of a long list: it carries the cursor
    (*next*) the shell sends back as ``?after=<cursor>`` and opens nothing.
    """
    if not _ID.match(str(node_id)):
        raise TreeError(f"node id {node_id!r} must be 1-96 of letters, digits and . : / - _")
    text = str(label).strip()
    if not text or len(text) > MAX_LABEL or any(ord(ch) < 32 for ch in text):
        raise TreeError(f"node {node_id!r}: label must be one line of 1-{MAX_LABEL} characters")
    if tone and tone not in TONES:
        raise TreeError(f"node {node_id!r}: tone {tone!r} is not one of {', '.join(TONES)}")
    if opens and not _PANEL.match(opens):
        raise TreeError(f"node {node_id!r}: opens must be a panel id such as 'case-detail'")
    if more:
        if not _ID.match(str(next)):
            raise TreeError(
                f"node {node_id!r}: a 'more' node needs a cursor 'next' "
                "(1-96 of letters, digits and . : / - _)"
            )
        if has_children or opens:
            raise TreeError(f"node {node_id!r}: a 'more' node opens nothing and has no children")
    elif next:
        raise TreeError(f"node {node_id!r}: 'next' belongs to a 'more' node")
    result: dict[str, Any] = {"id": str(node_id), "label": text}
    if more:
        result["more"] = True
        result["next"] = str(next)
    if meta:
        result["meta"] = str(meta)[:MAX_LABEL]
    if tone:
        result["tone"] = tone
    if has_children:
        result["has_children"] = True
    if opens:
        result["opens"] = opens
    return result


def nodes_payload(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return ``{"nodes": [...]}`` for *items*, each checked, ids unique, at most 500."""
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if len(nodes) >= MAX_NODES:
            raise TreeError(f"a tree answer holds at most {MAX_NODES} nodes; page it by parent")
        checked = node(
            str(item.get("id", "")),
            str(item.get("label", "")),
            meta=str(item.get("meta", "")),
            tone=str(item.get("tone", "")),
            has_children=bool(item.get("has_children", False)),
            opens=str(item.get("opens", "")),
            more=bool(item.get("more", False)),
            next=str(item.get("next", "")),
        )
        if checked["id"] in seen:
            raise TreeError(f"duplicate node id {checked['id']!r}")
        seen.add(checked["id"])
        nodes.append(checked)
    if any(item.get("more") for item in nodes[:-1]):
        raise TreeError("only the last node of an answer may be a 'more' node")
    return {"nodes": nodes}


def page_nodes(
    items: Sequence[Mapping[str, Any]],
    *,
    after: str = "",
    size: int = MAX_NODES - 1,
    label: str = "Load more",
) -> dict[str, Any]:
    """Return one page of *items* as a valid answer, with a 'more' node when some are left.

    *after* is the cursor of the previous page (empty for the first); the cursor is the offset
    of the next item, so a route that holds its children in a list can serve ``?after=`` with
    one call. A page never holds more than ``MAX_NODES`` nodes, the marker included.
    """
    if not 1 <= size < MAX_NODES:
        raise TreeError(f"a page holds 1-{MAX_NODES - 1} items")
    try:
        start = int(after) if after else 0
    except ValueError:
        raise TreeError(f"cursor {after!r} is not one this tree handed out") from None
    if start < 0 or start > len(items):
        raise TreeError(f"cursor {after!r} is outside this list")
    chunk = list(items[start : start + size])
    end = start + len(chunk)
    if end < len(items):
        marker = {"id": f"more.{end}", "label": f"{label} ({len(items) - end})"}
        chunk.append({**marker, "more": True, "next": str(end)})
    return nodes_payload(chunk)

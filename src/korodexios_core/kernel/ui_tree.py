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
from collections.abc import Iterable, Mapping
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
) -> dict[str, Any]:
    """Return one valid node, or raise :class:`TreeError` saying what to fix."""
    if not _ID.match(str(node_id)):
        raise TreeError(f"node id {node_id!r} must be 1-96 of letters, digits and . : / - _")
    text = str(label).strip()
    if not text or len(text) > MAX_LABEL or any(ord(ch) < 32 for ch in text):
        raise TreeError(f"node {node_id!r}: label must be one line of 1-{MAX_LABEL} characters")
    if tone and tone not in TONES:
        raise TreeError(f"node {node_id!r}: tone {tone!r} is not one of {', '.join(TONES)}")
    if opens and not _PANEL.match(opens):
        raise TreeError(f"node {node_id!r}: opens must be a panel id such as 'case-detail'")
    result: dict[str, Any] = {"id": str(node_id), "label": text}
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
        )
        if checked["id"] in seen:
            raise TreeError(f"duplicate node id {checked['id']!r}")
        seen.add(checked["id"])
        nodes.append(checked)
    return {"nodes": nodes}

"""Relay attribution for channel messages.

Enriches each message row with a ``relay`` field:
  hops       – integer hop count (already in the messages table, copied here for convenience)
  path       – list of relay-node hex prefixes from the matching packet
  label      – display string: "↻ N" for outgoing/direct; "via p1 → p2" for relayed received

The ``sender`` hash can be compared against this station's key prefix to determine
whether a message was sent by us (outgoing) or by someone else (received).
"""

from __future__ import annotations

import json
from typing import Any

from .store import Store


def _fmt_label(hops: int, path: list[str], station_prefix: str | None,
               sender: str | None) -> str:
    """Human-readable relay label for one message."""
    is_outgoing = station_prefix and sender and sender.startswith(station_prefix.lower())
    if hops == 0 or not path:
        return "↻ 0" if is_outgoing else "direct"
    if is_outgoing:
        return f"↻ {hops}"
    nodes = " → ".join(path)
    return f"via {nodes}"


def enrich_messages(
    store: Store,
    messages: list[dict[str, Any]],
    station_prefix: str | None,
) -> list[dict[str, Any]]:
    """Add a ``relay`` dict to each message, resolved against the packets table."""
    if not messages:
        return messages

    hashes = [m["packet_hash"] for m in messages]
    placeholders = ",".join("?" * len(hashes))
    rows = store.q(
        f"SELECT hash, path, hops FROM packets WHERE hash IN ({placeholders})",
        hashes,
    )
    path_by_hash: dict[str, list[str]] = {r["hash"]: json.loads(r["path"]) for r in rows}

    for msg in messages:
        path = path_by_hash.get(msg["packet_hash"], [])
        hops = msg.get("hops") or 0
        label = _fmt_label(hops, path, station_prefix, msg.get("sender"))
        msg["relay"] = {"hops": hops, "path": path, "label": label}
    return messages

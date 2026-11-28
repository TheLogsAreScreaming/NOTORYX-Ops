"""Identify relay nodes that have forwarded packets involving this station.

Derived entirely from stored packet data — no transmissions sent.

A relay node "heard" this device when the station's key prefix appears in a received
packet's relay path alongside that relay node.  We aggregate by relay prefix, counting
appearances and tracking the most-recent occurrence to give operators a quick view of
which repeaters are actively working with this station.
"""

from __future__ import annotations

import json
import time
from typing import Any

from .store import Store

WINDOW_DAYS = 7


def repeaters_panel(
    store: Store,
    station_prefix: str | None,
    window_days: int = WINDOW_DAYS,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Relay nodes seen in paths alongside *station_prefix* in the last *window_days* days.

    Returns a list sorted by number of appearances (descending), each entry:
      prefix       – 2-hex-char relay node prefix
      appearances  – how many packets had this relay + station in the path
      first_seen   – earliest received_at for a matching packet (Unix s)
      last_seen    – most-recent received_at (Unix s)
    """
    if not station_prefix:
        return []

    now = now or time.time()
    since = now - window_days * 86400
    pre = station_prefix[:2].lower()

    rows = store.q(
        "SELECT path, received_at FROM packets WHERE path LIKE ? AND received_at >= ?",
        (f'%"{pre}%', since),
    )

    counts: dict[str, dict[str, Any]] = {}
    for row in rows:
        path: list[str] = json.loads(row["path"])
        if pre not in path:
            continue
        for node_pre in path:
            if node_pre == pre:
                continue
            entry = counts.setdefault(node_pre, {"appearances": 0, "first_seen": row["received_at"],
                                                  "last_seen": row["received_at"]})
            entry["appearances"] += 1
            if row["received_at"] < entry["first_seen"]:
                entry["first_seen"] = row["received_at"]
            if row["received_at"] > entry["last_seen"]:
                entry["last_seen"] = row["received_at"]

    return sorted(
        [{"prefix": p, **v} for p, v in counts.items()],
        key=lambda r: (-r["appearances"], -r["last_seen"]),
    )

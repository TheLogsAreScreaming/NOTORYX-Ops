"""Reconstruct observed routing paths for a fleet node from stored packets.

No packets are sent on air; this is entirely passive and reads from the
packets table.  Each entry in the returned list is a path that was observed
at least once, together with how many times it appeared and when it was
last seen.
"""

from __future__ import annotations

import json
import time


def observed_paths(store, public_key: str, days: float = 7, top: int = 20) -> list[dict]:
    """Return the top observed paths that contain *public_key*.

    Each result has:
      path       – ordered list of hop prefixes exactly as stored
      count      – number of packets that used this path
      last_seen  – timestamp of the most recent such packet
    Results are sorted by count descending, limited to *top* entries.
    """
    since = time.time() - max(0.01, days) * 86400
    prefix = public_key[:2].lower()
    rows = store.q(
        "SELECT path, received_at FROM packets"
        " WHERE path LIKE ? AND received_at >= ? AND hops > 0",
        (f'%"{prefix}%', since),
    )
    counts: dict[str, dict] = {}
    for row in rows:
        try:
            path = json.loads(row["path"])
        except (ValueError, TypeError):
            continue
        if prefix not in path:
            continue
        key = json.dumps(path)
        entry = counts.setdefault(key, {"path": path, "count": 0, "last_seen": 0.0})
        entry["count"] += 1
        if row["received_at"] > entry["last_seen"]:
            entry["last_seen"] = row["received_at"]
    results = sorted(counts.values(), key=lambda e: (-e["count"], -e["last_seen"]))
    return results[:top]


def resolve_paths(store, paths: list[dict]) -> list[dict]:
    """Annotate each hop in every path with matching node records."""
    all_prefixes: set[str] = set()
    for entry in paths:
        all_prefixes.update(entry["path"])
    resolved: dict[str, list] = {}
    for pre in all_prefixes:
        rows = store.q(
            "SELECT public_key, name, node_type FROM nodes WHERE public_key LIKE ?"
            " ORDER BY last_seen DESC LIMIT 4",
            (pre.lower() + "%",),
        )
        resolved[pre] = rows
    out = []
    for entry in paths:
        hops = [{"prefix": p, "matches": resolved.get(p, [])} for p in entry["path"]]
        out.append({**entry, "hops": hops})
    return out

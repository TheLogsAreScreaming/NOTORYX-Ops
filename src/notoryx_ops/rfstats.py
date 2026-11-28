"""Per-node RF statistics derived from received packets since the station booted."""

from __future__ import annotations

import time

_BOOT_WINDOW_DAYS = 7


def node_rf_stats(store, node_key: str, since: float | None = None) -> dict | None:
    """Return RF stats for one node, or None if no packets exist."""
    since = since if since is not None else time.time() - _BOOT_WINDOW_DAYS * 86400
    rows = store.q(
        "SELECT snr, rssi, received_at FROM packets"
        " WHERE node_key = ? AND received_at >= ? AND snr IS NOT NULL",
        (node_key, since),
    )
    if not rows:
        return None
    snrs = [r["snr"] for r in rows]
    rss = [r["rssi"] for r in rows if r["rssi"] is not None]
    latest = max(r["received_at"] for r in rows)
    out: dict = {
        "packet_count": len(rows),
        "last_seen": latest,
        "snr": {
            "min": round(min(snrs), 2),
            "max": round(max(snrs), 2),
            "avg": round(sum(snrs) / len(snrs), 2),
            "latest": round(snrs[-1], 2),
        },
    }
    if rss:
        out["rssi"] = {
            "min": round(min(rss), 2),
            "max": round(max(rss), 2),
            "avg": round(sum(rss) / len(rss), 2),
            "latest": round(rss[-1], 2),
        }
    else:
        out["rssi"] = None
    return out


def all_rf_stats(store, since: float | None = None) -> list[dict]:
    """RF stats for every node that has packets since `since`."""
    since = since if since is not None else time.time() - _BOOT_WINDOW_DAYS * 86400
    rows = store.q(
        "SELECT node_key, snr, rssi, received_at FROM packets"
        " WHERE node_key IS NOT NULL AND received_at >= ? AND snr IS NOT NULL"
        " ORDER BY received_at",
        (since,),
    )
    by_node: dict[str, list] = {}
    for r in rows:
        by_node.setdefault(r["node_key"], []).append(r)
    out = []
    for nk, pkts in by_node.items():
        snrs = [p["snr"] for p in pkts]
        rss = [p["rssi"] for p in pkts if p["rssi"] is not None]
        latest = max(p["received_at"] for p in pkts)
        entry: dict = {
            "node_key": nk,
            "packet_count": len(pkts),
            "last_seen": latest,
            "snr": {
                "min": round(min(snrs), 2),
                "max": round(max(snrs), 2),
                "avg": round(sum(snrs) / len(snrs), 2),
                "latest": round(snrs[-1], 2),
            },
        }
        if rss:
            entry["rssi"] = {
                "min": round(min(rss), 2),
                "max": round(max(rss), 2),
                "avg": round(sum(rss) / len(rss), 2),
                "latest": round(rss[-1], 2),
            }
        else:
            entry["rssi"] = None
        out.append(entry)
    out.sort(key=lambda e: -e["last_seen"])
    return out

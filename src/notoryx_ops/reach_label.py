"""Per-node reachability labels derived from stored packet data.

No transmissions are sent; all inference is from already-received packets.

Labels:
  "two_way"   – The node's transmissions reached us directly (hops=0).
                 Implies bidirectional, because if we can hear them at 0 hops,
                 they are likely in direct RF range of our station.
  "heard_me"  – Our station's key prefix appears in a relay path of one of
                 their packets, meaning they forwarded something we sent.
  "no_answer" – We hear them only through intermediate relays; no sign of
                 direct RF range or relay back.
  "unknown"   – Never received an advert or packet from this node.
"""

from __future__ import annotations

import time

from .store import Store

WINDOW_DAYS = 7

LABELS = ("two_way", "heard_me", "no_answer", "unknown")


def node_reach(store: Store, public_key: str, station_prefix: str | None,
               now: float | None = None) -> str:
    now = now or time.time()
    since = now - WINDOW_DAYS * 86400

    if not store.one("SELECT 1 FROM nodes WHERE public_key = ?", (public_key,)):
        return "unknown"

    # Direct = hops=0 means their transmission reached our antenna first-hand.
    if store.one(
        "SELECT 1 FROM packets WHERE node_key = ? AND hops = 0 AND received_at >= ? LIMIT 1",
        (public_key, since),
    ):
        return "two_way"

    # Our prefix in one of their relay paths means they forwarded our traffic.
    if station_prefix:
        pre = station_prefix[:2].lower()
        if store.one(
            "SELECT 1 FROM packets WHERE node_key = ? AND path LIKE ? AND received_at >= ? LIMIT 1",
            (public_key, f'%"{pre}%', since),
        ):
            return "heard_me"

    return "no_answer"


def fleet_reach(store: Store, station_prefix: str | None,
                now: float | None = None) -> dict[str, str]:
    """Reachability label for every known non-ignored node. Returns {public_key: label}."""
    now = now or time.time()
    nodes = store.nodes()
    return {n["public_key"]: node_reach(store, n["public_key"], station_prefix, now)
            for n in nodes}

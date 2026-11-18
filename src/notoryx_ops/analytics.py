"""Network analytics: overview numbers, activity heatmap, prefix use, links and coverage."""

from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from typing import Any

from .store import Store

EARTH_M = 6371000.0


def overview(store: Store, now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    day = now - 86400
    q1 = store.one
    pk = q1("SELECT COUNT(*) n, COUNT(DISTINCT hash) u FROM packets WHERE received_at >= ?", (day,))
    nodes = q1("SELECT COUNT(*) n FROM nodes WHERE last_seen >= ?", (day,))
    reps = q1("SELECT COUNT(*) n FROM nodes WHERE last_seen >= ? AND node_type IN ('repeater','room')", (day,))
    obs = q1("SELECT COUNT(*) n FROM observers WHERE last_seen >= ?", (now - 1800,))
    types = store.q("SELECT ptype, COUNT(*) n FROM packets WHERE received_at >= ? GROUP BY ptype ORDER BY n DESC", (day,))
    hour = q1("SELECT COUNT(DISTINCT hash) u FROM packets WHERE received_at >= ?", (now - 3600,))
    clashes = sum(1 for p in prefix_usage(store, now) if p["repeaters"] > 1)
    return {
        "packets_24h": pk["n"], "unique_24h": pk["u"], "unique_1h": hour["u"],
        "nodes_24h": nodes["n"], "repeaters_24h": reps["n"], "observers_live": obs["n"],
        "types_24h": types, "prefix_clashes": clashes,
    }


def heatmap(store: Store, days: int = 7, now: float | None = None, tz_offset_min: int = 0) -> list[list[int]]:
    """Unique packets per weekday (0 = Monday) and hour, in the viewer's time zone."""
    now = now or time.time()
    grid = [[0] * 24 for _ in range(7)]
    rows = store.q("SELECT MIN(received_at) t FROM packets WHERE received_at >= ? GROUP BY hash", (now - days * 86400,))
    for r in rows:
        t = r["t"] - tz_offset_min * 60
        tm = time.gmtime(t)
        grid[tm.tm_wday][tm.tm_hour] += 1
    return grid


def top_nodes(store: Store, hours: int = 24, limit: int = 15, now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    since = now - hours * 3600
    rows = store.q(
        "SELECT p.node_key key, COUNT(DISTINCT p.hash) n FROM packets p WHERE p.received_at >= ? AND p.node_key IS NOT NULL"
        " GROUP BY p.node_key ORDER BY n DESC LIMIT ?", (since, limit))
    names = {r["public_key"]: r for r in store.q("SELECT public_key, name, node_type FROM nodes")}
    senders = Counter()
    for r in store.q("SELECT src_hash, hash FROM packets WHERE received_at >= ? AND src_hash IS NOT NULL GROUP BY hash", (since,)):
        senders[r["src_hash"]] += 1
    out = [{"public_key": r["key"], "name": (names.get(r["key"]) or {}).get("name"),
            "node_type": (names.get(r["key"]) or {}).get("node_type"), "adverts": r["n"]} for r in rows]
    relays = Counter()
    for r in store.q("SELECT path FROM packets WHERE received_at >= ? AND ptype != 'trace' GROUP BY hash", (since,)):
        for h in json.loads(r["path"]):
            relays[h[:2]] += 1
    return {"adverts": out, "senders": senders.most_common(limit), "relays": relays.most_common(limit)}


def observer_stats(store: Store, now: float | None = None) -> list[dict[str, Any]]:
    now = now or time.time()
    out = []
    for o in store.q("SELECT * FROM observers ORDER BY last_seen DESC"):
        s = store.one(
            "SELECT COUNT(*) n, AVG(snr) snr, AVG(rssi) rssi FROM packets WHERE observer = ? AND received_at >= ?",
            (o["name"], now - 86400))
        hourly = [0] * 24
        for r in store.q("SELECT received_at FROM packets WHERE observer = ? AND received_at >= ?", (o["name"], now - 86400)):
            hourly[min(23, int((now - r["received_at"]) // 3600))] += 1
        out.append({**o, "packets_24h": s["n"], "avg_snr": s["snr"], "avg_rssi": s["rssi"],
                    "hourly": list(reversed(hourly)), "live": now - o["last_seen"] < 1800})
    return out


def prefix_usage(store: Store, now: float | None = None, days: int = 7) -> list[dict[str, Any]]:
    """For all 256 one-byte prefixes: how many repeaters and other nodes use each."""
    now = now or time.time()
    use: dict[str, dict[str, Any]] = {f"{i:02x}": {"prefix": f"{i:02x}", "repeaters": 0, "others": 0, "names": []}
                                      for i in range(256)}
    for n in store.q("SELECT prefix, name, node_type, public_key FROM nodes WHERE last_seen >= ?", (now - days * 86400,)):
        u = use[n["prefix"][:2].lower()]
        if n["node_type"] in ("repeater", "room"):
            u["repeaters"] += 1
            u["names"].append(n["name"] or n["public_key"][:8])
        else:
            u["others"] += 1
    return list(use.values())


def free_prefixes(store: Store, now: float | None = None) -> list[str]:
    return [p["prefix"] for p in prefix_usage(store, now) if p["repeaters"] == 0 and p["others"] == 0]


def _resolve(prefix: str, by_prefix: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    cands = by_prefix.get(prefix[:2].lower(), [])
    reps = [c for c in cands if c["node_type"] in ("repeater", "room")]
    pick = reps if reps else cands
    return pick[0] if len(pick) == 1 else None


def links(store: Store, hours: int = 24, now: float | None = None) -> list[dict[str, Any]]:
    """Links between nodes, from consecutive hops in packet paths and from repeater neighbour lists."""
    now = now or time.time()
    nodes = store.q("SELECT public_key, prefix, name, node_type, lat, lon FROM nodes")
    by_prefix: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in nodes:
        by_prefix[n["prefix"][:2].lower()].append(n)
    pairs: dict[tuple[str, str], dict[str, Any]] = {}

    def add(a: dict, b: dict, snr: float | None, source: str) -> None:
        if a["public_key"] == b["public_key"]:
            return
        k = tuple(sorted((a["public_key"], b["public_key"])))
        e = pairs.setdefault(k, {"a": k[0], "b": k[1], "count": 0, "snr": None, "source": source})
        e["count"] += 1
        if snr is not None:
            e["snr"] = snr if e["snr"] is None else max(e["snr"], snr)
            e["source"] = source

    for r in store.q("SELECT path FROM packets WHERE received_at >= ? AND ptype != 'trace' AND hops > 1 GROUP BY hash",
                     (now - hours * 3600,)):
        path = json.loads(r["path"])
        for x, y in zip(path, path[1:], strict=False):
            a, b = _resolve(x, by_prefix), _resolve(y, by_prefix)
            if a and b:
                add(a, b, None, "paths")

    keyed = {n["public_key"]: n for n in nodes}
    for rep in store.q("SELECT id, public_key FROM repeaters"):
        st = store.one("SELECT neighbours FROM repeater_status WHERE repeater_id = ? AND neighbours IS NOT NULL"
                       " ORDER BY polled_at DESC LIMIT 1", (rep["id"],))
        me = keyed.get(rep["public_key"])
        if not st or not me:
            continue
        for nb in json.loads(st["neighbours"]):
            match = [n for n in nodes if n["public_key"].startswith(nb.get("pubkey", "").lower())]
            if len(match) == 1:
                add(me, match[0], nb.get("snr"), "neighbours")
    return list(pairs.values())


# ---- coverage hexes -------------------------------------------------------

def _project(lat: float, lon: float, lat0: float) -> tuple[float, float]:
    x = math.radians(lon) * EARTH_M * math.cos(math.radians(lat0))
    y = math.radians(lat) * EARTH_M
    return x, y


def _unproject(x: float, y: float, lat0: float) -> tuple[float, float]:
    lat = math.degrees(y / EARTH_M)
    lon = math.degrees(x / (EARTH_M * math.cos(math.radians(lat0))))
    return lat, lon


def hex_of(lat: float, lon: float, size_m: float, lat0: float) -> tuple[int, int]:
    """Axial coordinates of the pointy-top hex that holds a point."""
    x, y = _project(lat, lon, lat0)
    q = (math.sqrt(3) / 3 * x - y / 3) / size_m
    r = (2 / 3 * y) / size_m
    s = -q - r
    rq, rr, rs = round(q), round(r), round(s)
    dq, dr, ds = abs(rq - q), abs(rr - r), abs(rs - s)
    if dq > dr and dq > ds:
        rq = -rr - rs
    elif dr > ds:
        rr = -rq - rs
    return int(rq), int(rr)


def hex_polygon(q: int, r: int, size_m: float, lat0: float) -> list[list[float]]:
    cx = size_m * math.sqrt(3) * (q + r / 2)
    cy = size_m * 1.5 * r
    pts = []
    for i in range(6):
        ang = math.radians(60 * i - 30)
        lat, lon = _unproject(cx + size_m * math.cos(ang), cy + size_m * math.sin(ang), lat0)
        pts.append([lon, lat])
    pts.append(pts[0])
    return pts


def coverage(store: Store, size_m: float = 1500, days: int = 30, now: float | None = None) -> dict[str, Any]:
    """Hexes where nodes with a known position were heard directly, coloured by the best SNR."""
    now = now or time.time()
    rows = store.q(
        "SELECT n.lat, n.lon, p.snr, p.observer FROM packets p JOIN nodes n ON n.public_key = p.node_key"
        " WHERE p.ptype = 'advert' AND p.hops = 0 AND n.lat IS NOT NULL AND p.received_at >= ?",
        (now - days * 86400,))
    if not rows:
        return {"type": "FeatureCollection", "features": [], "gaps": []}
    lat0 = round(sum(r["lat"] for r in rows) / len(rows), 1)
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    for r in rows:
        k = hex_of(r["lat"], r["lon"], size_m, lat0)
        c = cells.setdefault(k, {"samples": 0, "best_snr": None, "observers": set()})
        c["samples"] += 1
        c["observers"].add(r["observer"])
        if r["snr"] is not None and (c["best_snr"] is None or r["snr"] > c["best_snr"]):
            c["best_snr"] = r["snr"]
    feats = [{"type": "Feature",
              "geometry": {"type": "Polygon", "coordinates": [hex_polygon(q, r, size_m, lat0)]},
              "properties": {"samples": c["samples"], "best_snr": c["best_snr"], "observers": sorted(c["observers"])}}
             for (q, r), c in cells.items()]
    return {"type": "FeatureCollection", "features": feats, "gaps": coverage_gaps(store, now)}


def distance_m(a_lat, a_lon, b_lat, b_lon) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(h))


def coverage_gaps(store: Store, now: float | None = None, far_km: float = 8) -> list[dict[str, Any]]:
    """Nodes with a position that are far from every known repeater: candidate spots for a new one."""
    now = now or time.time()
    nodes = store.q("SELECT public_key, name, node_type, lat, lon FROM nodes WHERE lat IS NOT NULL AND last_seen >= ?",
                    (now - 30 * 86400,))
    reps = [n for n in nodes if n["node_type"] in ("repeater", "room")]
    out = []
    for n in nodes:
        if n["node_type"] in ("repeater", "room"):
            continue
        near = min((distance_m(n["lat"], n["lon"], r["lat"], r["lon"]) for r in reps), default=None)
        if near is None or near > far_km * 1000:
            out.append({"public_key": n["public_key"], "name": n["name"], "lat": n["lat"], "lon": n["lon"],
                        "nearest_repeater_km": None if near is None else round(near / 1000, 1)})
    return out

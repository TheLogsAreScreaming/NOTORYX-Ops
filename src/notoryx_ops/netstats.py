"""Network stats for a public page: what this station heard, as counts and short ids only.

The station builds a small snapshot (no names, no positions) and can push it to a stats
relay, which merges snapshots from every station that sends one and serves the totals.
Short ids (the first 8 hex digits of a public key) let the relay count a node heard by two
stations once. Public keys are already broadcast in every advert, so they reveal nothing new.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import __version__, analytics
from .config import Config
from .store import NODE_NOT_IGNORED, Store

log = logging.getLogger(__name__)

SCHEMA = 1
WINDOW_DAYS = 7
PUSH_EVERY_S = 900
ID_LEN = 8


# ---- country lookup ------------------------------------------------------------------

@lru_cache(maxsize=1)
def _countries() -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parent / "geo" / "countries.json"
    return json.loads(path.read_text())["countries"]


def _in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def country_of(lat: float | None, lon: float | None, slack_deg: float = 0.3) -> str | None:
    """ISO 3166-1 alpha-2 code for a position, or None at sea / without a position.

    Borders are simplified, so a node on a coast or an island can fall just outside its
    country; the closest border point within slack_deg (about 30 km) is used then.
    """
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0 and lon == 0:
        return None  # "no fix" placeholder some firmware sends
    near: list[dict[str, Any]] = []
    for c in _countries():
        x0, y0, x1, y1 = c["bbox"]
        if x0 - slack_deg <= lon <= x1 + slack_deg and y0 - slack_deg <= lat <= y1 + slack_deg:
            if x0 <= lon <= x1 and y0 <= lat <= y1 and any(_in_ring(lon, lat, r) for r in c["rings"]):
                return c["cc"]
            near.append(c)
    best, best_d = None, slack_deg
    k = math.cos(math.radians(lat))
    for c in near:
        for r in c["rings"]:
            for x, y in r:
                d = math.hypot((x - lon) * k, y - lat)
                if d < best_d:
                    best, best_d = c["cc"], d
    return best


# ---- snapshot ---------------------------------------------------------------------------

def _sid(public_key: str) -> str:
    return public_key[:ID_LEN].lower()


def snapshot(store: Store, cfg: Config, now: float | None = None) -> dict[str, Any]:
    """Counts plus short ids for the last WINDOW_DAYS. Ignored nodes are left out."""
    now = now or time.time()
    since = now - WINDOW_DAYS * 86400
    rows = store.q(f"SELECT public_key, node_type, lat, lon, first_seen FROM nodes WHERE last_seen >= ? AND {NODE_NOT_IGNORED}",
                   (since,))
    repeaters, nodes = [], []
    newest: dict[str, Any] | None = None
    for r in rows:
        cc = country_of(r["lat"], r["lon"])
        nodes.append([_sid(r["public_key"]), cc])
        if r["node_type"] == "repeater":
            repeaters.append([_sid(r["public_key"]), cc])
            if newest is None or r["first_seen"] > newest["first_seen"]:
                newest = {"id": _sid(r["public_key"]), "country": cc, "first_seen": round(r["first_seen"])}
    active = {r["public_key"] for r in rows}
    links = sorted({tuple(sorted((_sid(lk["a"]), _sid(lk["b"]))))
                    for lk in analytics.links(store, WINDOW_DAYS * 24, now) if lk["a"] in active and lk["b"] in active})
    countries = sorted({cc for _, cc in nodes if cc})
    return {
        "schema": SCHEMA,
        "station_version": __version__,
        "window_days": WINDOW_DAYS,
        "generated_at": round(now),
        "counts": {"repeaters": len(repeaters), "nodes": len(nodes), "countries": len(countries), "links": len(links)},
        "countries": countries,
        "repeaters": repeaters,
        "nodes": nodes,
        "links": [list(p) for p in links],
        "newest_repeater": newest,
    }


def public_counts(snap: dict[str, Any]) -> dict[str, Any]:
    """The snapshot without ids, for the station's own /api/public/stats."""
    nr = snap["newest_repeater"]
    return {"window_days": snap["window_days"], "generated_at": snap["generated_at"], **snap["counts"],
            "country_list": snap["countries"],
            "newest_repeater": {"country": nr["country"], "first_seen": nr["first_seen"]} if nr else None}


# ---- push ---------------------------------------------------------------------------------

class StatsPush:
    """Sends a snapshot to the stats relay every PUSH_EVERY_S seconds while it is turned on."""

    def __init__(self, cfg: Config, store: Store, send=None):
        self.cfg = cfg
        self.store = store
        self._send = send or self._post
        self.state = "off"
        self.error: str | None = None
        self.last_push: float | None = None
        self.last_counts: dict[str, int] | None = None
        self._task: asyncio.Task | None = None

    def info(self) -> dict[str, Any]:
        return {"enabled": self.cfg.stats_push_enabled, "state": self.state, "error": self.error,
                "last_push": self.last_push, "last_counts": self.last_counts}

    @property
    def ready(self) -> bool:
        return bool(self.cfg.stats_push_enabled and self.cfg.stats_push_url and self.cfg.stats_push_token)

    def _post(self, body: bytes) -> int:
        from .net import tls_context

        req = urllib.request.Request(
            self.cfg.stats_push_url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.cfg.stats_push_token}",
                     "User-Agent": f"notoryx-ops/{__version__}"})
        with urllib.request.urlopen(req, timeout=20, context=tls_context()) as resp:  # noqa: S310 (URL set by the admin)
            return resp.status

    def push_once(self) -> dict[str, int]:
        snap = snapshot(self.store, self.cfg)
        status = self._send(json.dumps(snap, separators=(",", ":")).encode())
        if status >= 300:
            raise RuntimeError(f"stats relay answered HTTP {status}")
        self.last_counts = snap["counts"]
        return snap["counts"]

    def start(self) -> None:
        if self.ready:
            self._task = asyncio.create_task(self._loop(), name="stats-push")
        else:
            self.state = "off"

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        self.state = "off"

    async def _loop(self) -> None:
        await asyncio.sleep(30)  # let the radio connect and the first adverts arrive
        while True:
            try:
                self.state = "sending"
                await asyncio.to_thread(self.push_once)
                self.state, self.error = "ok", None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.state, self.error = "error", str(exc).split("\n")[0][:300]
                log.warning("stats push failed: %s", self.error)
            self.last_push = time.time()
            await asyncio.sleep(PUSH_EVERY_S)

"""Coverage planning API: terrain coverage, link profiles, high points, what-if relays, calibration."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import analytics
from .config import Config
from .store import NODE_NOT_IGNORED, Store
from .terrain import (
    ATTRIBUTION,
    MAX_RADIUS_KM,
    RadioParams,
    Terrain,
    TerrainError,
    Tiles,
    calibrate,
    distance_m,
)

REPEATER_TYPES = ("repeater", "room")


class Planner:
    """One Terrain for the station, rebuilt when radio settings change."""

    def __init__(self, cfg: Config, store: Store, fetch=None):
        self.cfg = cfg
        self.store = store
        self._fetch = fetch
        self._lock = threading.Lock()
        self._terrain: Terrain | None = None
        self._key: tuple | None = None

    def _params(self) -> RadioParams:
        c = self.cfg
        return RadioParams(
            freq_mhz=c.freq_mhz, bandwidth_khz=c.bandwidth_khz, spreading_factor=c.spreading_factor,
            tx_power_dbm=c.tx_power_dbm, antenna_gain_dbi=c.antenna_gain_dbi, cable_loss_db=c.cable_loss_db,
            noise_figure_db=c.noise_figure_db, offset_db=self.offset() if c.use_calibration else 0.0,
        )

    @property
    def terrain(self) -> Terrain:
        p = self._params()
        key = (self.cfg.terrain_source, tuple(sorted(vars(p).items())))
        with self._lock:
            if self._terrain is None or self._terrain.tiles.source != self.cfg.terrain_source:
                self._terrain = Terrain(Tiles(self.cfg.data_dir / "terrain", self.cfg.terrain_source, fetch=self._fetch), p)
            elif key != self._key:
                self._terrain.params = p
            self._key = key
            return self._terrain

    # ---- calibration ---------------------------------------------------------
    def offset(self) -> float:
        r = self.store.one("SELECT value FROM meta WHERE key = 'terrain_calibration'")
        if not r:
            return 0.0
        try:
            return float(json.loads(r["value"]).get("offset_db") or 0.0)
        except (ValueError, TypeError):
            return 0.0

    def calibration(self) -> dict[str, Any] | None:
        r = self.store.one("SELECT value FROM meta WHERE key = 'terrain_calibration'")
        return json.loads(r["value"]) if r else None

    def recalibrate(self) -> dict[str, Any]:
        nodes = {n["public_key"]: n for n in self.store.q(f"SELECT * FROM nodes WHERE {NODE_NOT_IGNORED}")}
        links = []
        for lk in analytics.links(self.store, hours=24 * 14):
            a, b = nodes.get(lk["a"]), nodes.get(lk["b"])
            if not a or not b or a["lat"] is None or b["lat"] is None:
                continue
            links.append({**lk, "a_lat": a["lat"], "a_lon": a["lon"], "b_lat": b["lat"], "b_lon": b["lon"],
                          "a_name": a["name"], "b_name": b["name"]})
        heights = {k: self.height_for(n) for k, n in nodes.items()}
        res = calibrate(self.terrain, links, heights, self.cfg.client_height_m)
        res["at"] = time.time()
        self.store.x("INSERT INTO meta(key, value) VALUES ('terrain_calibration', ?)"
                     " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (json.dumps(res),))
        return res

    # ---- sites ----------------------------------------------------------------
    def height_for(self, node: dict[str, Any]) -> float:
        return self.cfg.repeater_height_m if node.get("node_type") in REPEATER_TYPES else self.cfg.client_height_m

    def repeater_sites(self, lat: float, lon: float, within_km: float, days: int = 30) -> list[dict[str, Any]]:
        """Repeaters with a known position near a place, nearest first (at most 25)."""
        rows = self.store.q(
            f"SELECT public_key, name, lat, lon FROM nodes WHERE lat IS NOT NULL AND node_type IN ('repeater','room')"
            f" AND last_seen >= ? AND {NODE_NOT_IGNORED}", (time.time() - days * 86400,))
        out = []
        for r in rows:
            d = distance_m(lat, lon, r["lat"], r["lon"])
            if d <= within_km * 1000:
                out.append({"lat": r["lat"], "lon": r["lon"], "height_m": self.cfg.repeater_height_m,
                            "name": r["name"], "public_key": r["public_key"], "_d": d})
        out.sort(key=lambda s: s["_d"])
        for s in out:
            s.pop("_d")
        return out[:25]


class Site(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    height_m: float = Field(default=10, ge=0, le=300)
    name: str | None = Field(default=None, max_length=60)


class WhatIfIn(BaseModel):
    new: list[Site] = Field(default_factory=list, max_length=10)
    existing: list[Site] | None = Field(default=None, max_length=25)
    radius_km: float = Field(default=15, ge=1, le=MAX_RADIUS_KM)


def router(planner: Planner) -> APIRouter:
    r = APIRouter(prefix="/api/terrain")

    def run(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except TerrainError as exc:
            raise HTTPException(400, str(exc)) from exc

    @r.get("/status")
    def status():
        t = planner.terrain
        p = t.params
        return {"source": t.tiles.source, "cache": t.tiles.cached(), "attribution": ATTRIBUTION,
                "params": {**vars(p), "noise_dbm": round(p.noise_dbm, 1), "snr_limit": p.snr_limit},
                "calibration": planner.calibration(), "use_calibration": planner.cfg.use_calibration,
                "repeater_height_m": planner.cfg.repeater_height_m, "client_height_m": planner.cfg.client_height_m}

    @r.get("/elevation")
    def elevation(lat: float, lon: float):
        return {"elevation_m": round(float(run(planner.terrain.tiles.elevation, [lat], [lon])[0]), 1)}

    @r.get("/profile")
    def profile(a_lat: float, a_lon: float, b_lat: float, b_lon: float,
                a_h: float | None = None, b_h: float | None = None):
        a_h = planner.cfg.client_height_m if a_h is None else max(0, min(a_h, 300))
        b_h = planner.cfg.client_height_m if b_h is None else max(0, min(b_h, 300))
        return run(planner.terrain.profile, a_lat, a_lon, a_h, b_lat, b_lon, b_h)

    @r.get("/coverage")
    def coverage(lat: float, lon: float, height_m: float | None = None, radius_km: float = 15,
                 rx_height_m: float | None = None):
        h = planner.cfg.repeater_height_m if height_m is None else max(0, min(height_m, 300))
        rx = planner.cfg.client_height_m if rx_height_m is None else max(0, min(rx_height_m, 300))
        return run(planner.terrain.coverage, lat, lon, h, radius_km, rx)

    @r.get("/high-points")
    def high_points(lat: float, lon: float, radius_km: float = 15, count: int = 8):
        return run(planner.terrain.high_points, lat, lon, radius_km, max(1, min(count, 20)))

    @r.get("/repeaters")
    def repeaters(lat: float, lon: float, within_km: float = 30):
        return planner.repeater_sites(lat, lon, max(1, min(within_km, 120)))

    @r.post("/what-if")
    def what_if(body: WhatIfIn):
        new = [s.model_dump() for s in body.new]
        if body.existing is not None:
            existing = [s.model_dump() for s in body.existing]
        else:
            anchor = (new or [{"lat": 0, "lon": 0}])[0]
            existing = planner.repeater_sites(anchor["lat"], anchor["lon"], body.radius_km * 2) if new else []
        return run(planner.terrain.what_if, existing, new, body.radius_km)

    @r.get("/suggest")
    def suggest(lat: float, lon: float, radius_km: float = 15, height_m: float | None = None):
        h = planner.cfg.repeater_height_m if height_m is None else max(0, min(height_m, 300))
        existing = planner.repeater_sites(lat, lon, radius_km * 2)
        return {"existing": existing, "suggestions": run(planner.terrain.suggest, existing, lat, lon, radius_km, h)}

    @r.get("/calibration")
    def calibration():
        return planner.calibration() or {"links": [], "offset_db": 0.0, "rms_db": None, "used": 0, "at": None}

    return r

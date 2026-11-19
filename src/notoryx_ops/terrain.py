"""Terrain: elevation tiles, path profiles, radio coverage and relay planning.

Elevation comes from the Copernicus DEM (GLO-90 by default, GLO-30 optional), which the
European Space Agency publishes free of charge as Cloud-Optimized GeoTIFFs. Tiles are
downloaded on first use and kept in the station's data folder.

The radio model is deliberately simple and explainable:
  free-space loss + knife-edge diffraction over the Bullington equivalent edge
  (ITU-R P.526), the same for single links and coverage maps, on a 4/3-earth profile,
  plus an optional offset learned from links the station has actually measured.
"""

from __future__ import annotations

import base64
import logging
import math
import struct
import threading
import time
import urllib.error
import urllib.request
import zlib
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .net import urlopen

log = logging.getLogger(__name__)

EARTH_M = 6371000.0
SOURCES = {
    # name: (url template, arc-second code in the file name)
    "glo90": ("https://copernicus-dem-90m.s3.amazonaws.com/{n}/{n}.tif", "30"),
    "glo30": ("https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif", "10"),
}
ATTRIBUTION = (
    "Elevation: Copernicus DEM, © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, "
    "provided under COPERNICUS by the European Union and ESA."
)
# LoRa demodulation floor (SNR in dB) for each spreading factor.
SF_SNR_LIMIT = {5: -2.5, 6: -5.0, 7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}
MAX_RADIUS_KM = 60
MAX_PROFILE_KM = 200


class TerrainError(RuntimeError):
    pass


# ---- GeoTIFF ---------------------------------------------------------------

_TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 11: ("f", 4), 12: ("d", 8), 16: ("Q", 8)}


@dataclass
class Grid:
    """A north-up elevation grid. Row 0 is the north edge; (lon0, lat0) is its top-left corner."""

    z: np.ndarray
    lon0: float
    lat0: float
    dlon: float
    dlat: float

    def sample(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        """Bilinear elevation at points inside this grid."""
        fx = (np.asarray(lon) - self.lon0) / self.dlon - 0.5
        fy = (self.lat0 - np.asarray(lat)) / self.dlat - 0.5
        h, w = self.z.shape
        fx = np.clip(fx, 0, w - 1.000001)
        fy = np.clip(fy, 0, h - 1.000001)
        x0, y0 = fx.astype(np.int64), fy.astype(np.int64)
        tx, ty = fx - x0, fy - y0
        z = self.z
        top = z[y0, x0] * (1 - tx) + z[y0, x0 + 1] * tx
        bot = z[y0 + 1, x0] * (1 - tx) + z[y0 + 1, x0 + 1] * tx
        return top * (1 - ty) + bot * ty


def read_geotiff(data: bytes) -> Grid:
    """Read the full-resolution image of a single-band float32 or int16 GeoTIFF.

    Handles what the Copernicus tiles use: tiled or striped, no compression or deflate,
    and predictor 1 (none), 2 (horizontal) or 3 (floating point).
    """
    if data[:2] == b"II":
        e = "<"
    elif data[:2] == b"MM":
        e = ">"
    else:
        raise TerrainError("not a TIFF file")
    if struct.unpack(e + "H", data[2:4])[0] != 42:
        raise TerrainError("BigTIFF and other variants are not supported")
    off = struct.unpack(e + "I", data[4:8])[0]
    n = struct.unpack(e + "H", data[off:off + 2])[0]
    tags: dict[int, tuple] = {}
    for i in range(n):
        tag, typ, count, raw = struct.unpack(e + "HHI4s", data[off + 2 + i * 12: off + 14 + i * 12])
        if typ not in _TYPES:
            continue
        fmt, size = _TYPES[typ]
        if size * count <= 4:
            buf = raw[: size * count]
        else:
            vo = struct.unpack(e + "I", raw)[0]
            buf = data[vo: vo + size * count]
        tags[tag] = struct.unpack(e + fmt * count, buf) if typ != 2 else (buf,)

    width, height = tags[256][0], tags[257][0]
    bits = tags.get(258, (16,))[0]
    comp = tags.get(259, (1,))[0]
    pred = tags.get(317, (1,))[0]
    fmt = tags.get(339, (1,))[0]
    if tags.get(277, (1,))[0] != 1:
        raise TerrainError("only single-band elevation files are supported")
    if comp not in (1, 8, 32946):
        raise TerrainError(f"TIFF compression {comp} is not supported")
    if fmt == 3 and bits == 32:
        dtype = np.dtype(e + "f4")
    elif bits == 16:
        dtype = np.dtype(e + ("i2" if fmt == 2 else "u2"))
    else:
        raise TerrainError("only float32 or 16-bit elevation is supported")
    if 33550 not in tags or 33922 not in tags:
        raise TerrainError("the file has no georeferencing")
    sx, sy = tags[33550][0], tags[33550][1]
    tie = tags[33922]
    lon0 = tie[3] - tie[0] * sx
    lat0 = tie[4] + tie[1] * sy

    if 322 in tags:
        cw, ch = tags[322][0], tags[323][0]
        offsets, counts = tags[324], tags[325]
    else:
        cw, ch = width, tags.get(278, (height,))[0]
        offsets, counts = tags[273], tags[279]
    across = math.ceil(width / cw)
    out = np.zeros((math.ceil(height / ch) * ch, across * cw), dtype=np.float32)
    bps = dtype.itemsize
    for idx, (o, c) in enumerate(zip(offsets, counts, strict=False)):
        if c == 0:
            continue
        raw = data[o: o + c]
        if comp in (8, 32946):
            raw = zlib.decompress(raw)
        rows = len(raw) // (cw * bps)
        if pred == 3:
            b = np.frombuffer(raw[: rows * cw * bps], np.uint8).reshape(rows, cw * bps)
            b = np.cumsum(b, axis=1, dtype=np.uint8)
            b = b.reshape(rows, bps, cw).transpose(0, 2, 1).copy()
            chunk = b.view(">f4" if bps == 4 else ">i2").reshape(rows, cw)
        else:
            chunk = np.frombuffer(raw[: rows * cw * bps], dtype).reshape(rows, cw)
            if pred == 2:
                chunk = np.cumsum(chunk, axis=1, dtype=chunk.dtype)
        ty, tx = divmod(idx, across)
        out[ty * ch: ty * ch + rows, tx * cw: tx * cw + cw] = chunk
    z = out[:height, :width]
    z[~np.isfinite(z)] = 0
    z[z < -1000] = 0  # no-data markers: treat as sea level
    return Grid(np.ascontiguousarray(z), lon0, lat0, sx, sy)


# ---- tiles -----------------------------------------------------------------

def tile_name(lat_floor: int, lon_floor: int, source: str = "glo90") -> str:
    code = SOURCES[source][1]
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return f"Copernicus_DSM_COG_{code}_{ns}{abs(lat_floor):02d}_00_{ew}{abs(lon_floor):03d}_00_DEM"


class Tiles:
    """Downloads, caches and samples elevation tiles. Thread-safe."""

    def __init__(self, folder: Path, source: str = "glo90", keep: int = 16, fetch=None):
        if source not in SOURCES:
            raise TerrainError(f"unknown elevation source {source!r}")
        self.folder = Path(folder)
        self.source = source
        self.keep = keep
        self._mem: OrderedDict[tuple[int, int], Grid | None] = OrderedDict()
        self._lock = threading.Lock()
        self._fetch = fetch or self._download
        self.downloads = 0

    def _path(self, la: int, lo: int) -> Path:
        return self.folder / self.source / f"{tile_name(la, lo, self.source)}.tif"

    def _download(self, url: str) -> bytes | None:
        req = urllib.request.Request(url, headers={"User-Agent": "NOTORYX-Ops"})
        try:
            with urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                return None  # no tile: open sea
            raise TerrainError(f"elevation download failed ({exc.code})") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TerrainError(f"elevation download failed: {exc}. The station needs internet access the first time an area is used.") from exc

    def tile(self, la: int, lo: int) -> Grid | None:
        key = (la, lo)
        with self._lock:
            if key in self._mem:
                self._mem.move_to_end(key)
                return self._mem[key]
        path = self._path(la, lo)
        sea = path.with_suffix(".sea")
        grid: Grid | None
        if sea.exists():
            grid = None
        else:
            if not path.exists():
                name = tile_name(la, lo, self.source)
                data = self._fetch(SOURCES[self.source][0].format(n=name))
                self.downloads += 1
                path.parent.mkdir(parents=True, exist_ok=True)
                if data is None:
                    sea.write_text("")
                else:
                    tmp = path.with_suffix(".part")
                    tmp.write_bytes(data)
                    tmp.replace(path)
            grid = None if sea.exists() else read_geotiff(path.read_bytes())
        with self._lock:
            self._mem[key] = grid
            while len(self._mem) > self.keep:
                self._mem.popitem(last=False)
        return grid

    def cached(self) -> dict[str, Any]:
        d = self.folder / self.source
        files = list(d.glob("*.tif")) if d.exists() else []
        return {"tiles": len(files), "mb": round(sum(f.stat().st_size for f in files) / 1e6, 1),
                "sea_tiles": len(list(d.glob("*.sea"))) if d.exists() else 0}

    def elevation(self, lat, lon) -> np.ndarray:
        """Elevation in metres for arrays of points. Points over open sea read 0."""
        lat = np.atleast_1d(np.asarray(lat, dtype=np.float64))
        lon = np.atleast_1d(np.asarray(lon, dtype=np.float64))
        if lat.size and (np.abs(lat).max() > 90 or np.abs(lon).max() > 180):
            raise TerrainError("position off the globe")
        out = np.zeros(lat.shape, dtype=np.float64)
        la_f = np.floor(lat).astype(np.int64)
        lo_f = np.floor(lon).astype(np.int64)
        keys = np.unique(np.stack([la_f.ravel(), lo_f.ravel()], axis=1), axis=0)
        if len(keys) > 12:
            raise TerrainError("that area is too large; keep it under about 3° across")
        for la, lo in keys:
            m = (la_f == la) & (lo_f == lo)
            g = self.tile(int(la), int(lo))
            if g is not None:
                out[m] = g.sample(lat[m], lon[m])
        return out


# ---- radio model -----------------------------------------------------------

@dataclass
class RadioParams:
    freq_mhz: float = 910.525
    bandwidth_khz: float = 62.5
    spreading_factor: int = 7
    tx_power_dbm: float = 22
    antenna_gain_dbi: float = 3
    cable_loss_db: float = 1
    noise_figure_db: float = 6
    k_factor: float = 4 / 3
    offset_db: float = 0.0  # calibration: added to the predicted SNR

    @property
    def wavelength(self) -> float:
        return 299_792_458 / (self.freq_mhz * 1e6)

    @property
    def noise_dbm(self) -> float:
        return -174 + 10 * math.log10(self.bandwidth_khz * 1000) + self.noise_figure_db

    @property
    def snr_limit(self) -> float:
        return SF_SNR_LIMIT.get(int(self.spreading_factor), -7.5)

    def eirp_gain(self) -> float:
        """Transmit power plus both antennas, less cable loss at both ends."""
        return self.tx_power_dbm + 2 * self.antenna_gain_dbi - 2 * self.cable_loss_db

    def fspl(self, d_m) -> np.ndarray:
        d = np.maximum(np.asarray(d_m, dtype=np.float64), 1.0)
        return 20 * np.log10(d / 1000) + 20 * math.log10(self.freq_mhz) + 32.44


def knife_edge_db(v) -> np.ndarray:
    """ITU-R P.526 approximation of the loss behind one knife edge."""
    v = np.asarray(v, dtype=np.float64)
    out = 6.9 + 20 * np.log10(np.sqrt((v - 0.1) ** 2 + 1) + v - 0.1)
    return np.where(v > -0.78, out, 0.0)


def _dest(lat: float, lon: float, bearing_rad, dist_m) -> tuple[np.ndarray, np.ndarray]:
    p1, l1 = math.radians(lat), math.radians(lon)
    a = np.asarray(dist_m) / EARTH_M
    b = np.asarray(bearing_rad)
    p2 = np.arcsin(math.sin(p1) * np.cos(a) + math.cos(p1) * np.sin(a) * np.cos(b))
    l2 = l1 + np.arctan2(np.sin(b) * np.sin(a) * math.cos(p1), np.cos(a) - math.sin(p1) * np.sin(p2))
    return np.degrees(p2), (np.degrees(l2) + 540) % 360 - 180


def distance_m(a_lat, a_lon, b_lat, b_lon) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(min(1.0, h)))


def bearing_rad(a_lat, a_lon, b_lat, b_lon) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dl = math.radians(b_lon - a_lon)
    return math.atan2(math.sin(dl) * math.cos(p2), math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))


def bullington_v(d: np.ndarray, h: np.ndarray, a_h: float, b_h: float, lam: float) -> float:
    """Diffraction parameter of the Bullington equivalent edge (ITU-R P.526) over one profile.

    d: distances from A (first = 0, last = total); h: terrain heights including earth bulge;
    a_h, b_h: antenna tops above sea level at each end.
    """
    total = float(d[-1] - d[0])
    if len(d) < 3 or total <= 0:
        return -10.0
    di = d[1:-1] - d[0]
    hi = h[1:-1]
    s_tim = float(np.max((hi - a_h) / di))           # steepest slope seen from A
    s_tr = (b_h - a_h) / total                       # straight line A -> B
    if s_tim < s_tr:
        # Line of sight: the obstacle that comes closest to the line decides.
        return float(np.max((hi - (a_h + s_tr * di)) * np.sqrt(2 * total / (lam * di * (total - di)))))
    s_rim = float(np.max((hi - b_h) / (total - di)))  # steepest slope seen from B
    db = (b_h - a_h + s_rim * total) / (s_tim + s_rim)
    db = min(max(db, 1e-3), total - 1e-3)
    return (a_h + s_tim * db - (a_h * (total - db) + b_h * db) / total) * math.sqrt(2 * total / (lam * db * (total - db)))


class Terrain:
    def __init__(self, tiles: Tiles, params: RadioParams):
        self.tiles = tiles
        self.params = params
        self._cov: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
        self._rad: OrderedDict[tuple, tuple] = OrderedDict()

    # ---- one link ---------------------------------------------------------
    def profile(self, a_lat, a_lon, a_h, b_lat, b_lon, b_h, samples: int | None = None) -> dict[str, Any]:
        p = self.params
        dist = distance_m(a_lat, a_lon, b_lat, b_lon)
        if dist > MAX_PROFILE_KM * 1000:
            raise TerrainError(f"keep links under {MAX_PROFILE_KM} km")
        n = samples or int(min(1200, max(32, dist / 60)))
        d = np.linspace(0, dist, n)
        lat, lon = _dest(a_lat, a_lon, bearing_rad(a_lat, a_lon, b_lat, b_lon), d)
        lat[0], lon[0], lat[-1], lon[-1] = a_lat, a_lon, b_lat, b_lon
        ground = self.tiles.elevation(lat, lon)
        bulge = d * (dist - d) / (2 * p.k_factor * EARTH_M)
        h = ground + bulge
        a_top, b_top = ground[0] + a_h, ground[-1] + b_h
        los = a_top + (b_top - a_top) * d / max(dist, 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            f1 = np.sqrt(p.wavelength * d * (dist - d) / max(dist, 1))
        f1 = np.nan_to_num(f1)
        diff = float(knife_edge_db(bullington_v(d, h, a_top, b_top, p.wavelength))) if dist > 0 else 0.0
        fspl = float(p.fspl(dist))
        rx = p.eirp_gain() - fspl - diff
        snr = rx - p.noise_dbm + p.offset_db
        clearance = (los - h)[1:-1] / np.maximum(f1[1:-1], 1e-6) if n > 2 else np.array([1.0])
        worst = int(np.argmin(clearance)) + 1 if n > 2 else 0
        step = max(1, n // 400)
        return {
            "distance_m": round(dist, 1),
            "points": [
                {"d": round(float(d[i]), 1), "ground": round(float(ground[i]), 1), "bulge": round(float(bulge[i]), 2),
                 "los": round(float(los[i] - bulge[i]), 1), "fresnel": round(float(f1[i]), 2)}
                for i in range(0, n, step)
            ],
            "a": {"ground": round(float(ground[0]), 1), "height": a_h},
            "b": {"ground": round(float(ground[-1]), 1), "height": b_h},
            "line_of_sight": bool(np.all((los - h)[1:-1] >= 0)) if n > 2 else True,
            "fresnel_clear_pct": round(float(max(0.0, min(1.0, clearance.min()))) * 100, 0) if n > 2 else 100,
            "worst": {"d": round(float(d[worst]), 1), "lat": round(float(lat[worst]), 6), "lon": round(float(lon[worst]), 6),
                      "ground": round(float(ground[worst]), 1)} if n > 2 else None,
            "budget": {
                "eirp_and_gains_db": round(p.eirp_gain(), 1), "free_space_db": round(fspl, 1),
                "diffraction_db": round(diff, 1), "calibration_db": round(p.offset_db, 1),
                "rx_dbm": round(rx + p.offset_db, 1), "noise_dbm": round(p.noise_dbm, 1),
                "snr_db": round(snr, 1), "snr_limit_db": p.snr_limit, "margin_db": round(snr - p.snr_limit, 1),
            },
            "works": bool(snr >= p.snr_limit),
        }

    # ---- coverage around one site -----------------------------------------
    def _radials(self, lat, lon, h, radius_m, n_az: int, step_m: float, rx_h: float):
        """Predicted SNR along n_az radials from a site. Results are kept for reuse."""
        key = (round(lat, 6), round(lon, 6), round(h, 2), round(radius_m), n_az, round(step_m, 1), rx_h, self._param_key())
        hit = self._rad.get(key)
        if hit is not None:
            self._rad.move_to_end(key)
            return hit
        out = self._radials_calc(lat, lon, h, radius_m, n_az, step_m, rx_h)
        self._rad[key] = out
        while len(self._rad) > 60:
            self._rad.popitem(last=False)
        return out

    def _radials_calc(self, lat, lon, h, radius_m, n_az: int, step_m: float, rx_h: float):
        p = self.params
        n = max(8, int(radius_m / step_m))
        d = np.arange(1, n + 1) * (radius_m / n)
        az = np.linspace(0, 2 * math.pi, n_az, endpoint=False)
        la, lo = _dest(lat, lon, az[:, None], d[None, :])
        ground = self.tiles.elevation(la.ravel(), lo.ravel()).reshape(n_az, n)
        site_ground = float(self.tiles.elevation([lat], [lon])[0])
        tx = site_ground + h
        lam = p.wavelength
        snr = np.empty((n_az, n))
        base = p.eirp_gain() - p.noise_dbm + p.offset_db
        fspl = p.fspl(d)
        # Bullington equivalent edge between the site and each receiver point (obstacles j < i).
        dj = d[None, :]                     # obstacle distances
        di = d[:, None]                     # receiver distances
        valid = dj < di
        span = np.maximum(di - dj, 1e-9)
        bulge = np.where(valid, dj * (di - dj) / (2 * p.k_factor * EARTH_M), 0.0)
        dcol = d
        for a in range(n_az):
            g = ground[a]
            rx_top = g + rx_h
            hj = g[None, :] + bulge
            s_tim = np.where(valid, (hj - tx) / dj, -np.inf).max(axis=1)
            s_rim = np.where(valid, (hj - rx_top[:, None]) / span, -np.inf).max(axis=1)
            s_tr = (rx_top - tx) / dcol
            # Line of sight: closest approach to the direct line.
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                los = tx + s_tr[:, None] * dj
                v_los = np.where(valid, (hj - los) * np.sqrt(2 * di / (lam * dj * span)), -10.0).max(axis=1)
                denom = np.where(s_tim + s_rim > 0, s_tim + s_rim, np.inf)
                db = np.clip((rx_top - tx + s_rim * dcol) / denom, 1e-3, dcol - 1e-3)
                v_b = (tx + s_tim * db - (tx * (dcol - db) + rx_top * db) / dcol) * np.sqrt(2 * dcol / (lam * db * (dcol - db)))
            v = np.where(s_tim < s_tr, v_los, v_b)
            v = np.nan_to_num(v, nan=-10.0, neginf=-10.0, posinf=50.0)
            snr[a] = base - fspl - knife_edge_db(v)
        return az, d, snr, site_ground

    def coverage(self, lat, lon, height_m=10.0, radius_km=15.0, rx_height_m=1.5, cells=220) -> dict[str, Any]:
        radius_km = float(max(1, min(radius_km, MAX_RADIUS_KM)))
        key = (round(lat, 5), round(lon, 5), round(height_m, 1), radius_km, rx_height_m, cells, self._param_key())
        if key in self._cov:
            self._cov.move_to_end(key)
            return self._cov[key]
        t0 = time.time()
        r_m = radius_km * 1000
        step = max(60.0, r_m / 300)
        n_az = int(min(1440, max(360, 2 * math.pi * r_m / (step * 1.5))))
        az, d, snr, site_ground = self._radials(lat, lon, height_m, r_m, n_az, step, rx_height_m)
        out = self._raster(lat, lon, r_m, az, d, snr, cells)
        out.update({"site": {"lat": lat, "lon": lon, "height_m": height_m, "ground_m": round(site_ground, 1)},
                    "radius_km": radius_km, "seconds": round(time.time() - t0, 2)})
        self._cov[key] = out
        while len(self._cov) > 40:
            self._cov.popitem(last=False)
        return out

    def _param_key(self) -> tuple:
        p = self.params
        return (p.freq_mhz, p.bandwidth_khz, p.spreading_factor, p.tx_power_dbm, p.antenna_gain_dbi,
                p.cable_loss_db, p.noise_figure_db, p.k_factor, round(p.offset_db, 2), self.tiles.source)

    def _raster(self, lat, lon, r_m, az, d, snr, cells) -> dict[str, Any]:
        """Turn a polar coverage result into a north-up grid of SNR margin (dB, NaN = outside)."""
        p = self.params
        dlat = math.degrees(r_m / EARTH_M)
        dlon = dlat / max(0.05, math.cos(math.radians(lat)))
        north, south, west, east = lat + dlat, lat - dlat, lon - dlon, lon + dlon
        ys = np.linspace(north, south, cells)
        xs = np.linspace(west, east, cells)
        gy, gx = np.meshgrid(ys, xs, indexing="ij")
        dy = np.radians(gy - lat) * EARTH_M
        dx = np.radians(gx - lon) * EARTH_M * math.cos(math.radians(lat))
        dist = np.hypot(dx, dy)
        ang = np.mod(np.arctan2(dx, dy), 2 * math.pi)
        ai = np.round(ang / (2 * math.pi) * len(az)).astype(np.int64) % len(az)
        di = np.clip(np.round(dist / d[-1] * len(d)).astype(np.int64) - 1, 0, len(d) - 1)
        margin = snr[ai, di] - p.snr_limit
        margin[dist > r_m] = np.nan
        return {"bounds": [west, south, east, north], "width": cells, "height": cells,
                **encode_margin(margin), "covered_km2": round(area_km2(margin >= 0, r_m, cells), 1)}

    # ---- planning -----------------------------------------------------------
    def high_points(self, lat, lon, radius_km=15.0, count=8, min_sep_km=2.0) -> list[dict[str, Any]]:
        """Local high ground near a place, highest first, spread apart."""
        radius_km = float(max(1, min(radius_km, MAX_RADIUS_KM)))
        r_m = radius_km * 1000
        n = 161
        dlat = math.degrees(r_m / EARTH_M)
        dlon = dlat / max(0.05, math.cos(math.radians(lat)))
        ys = np.linspace(lat + dlat, lat - dlat, n)
        xs = np.linspace(lon - dlon, lon + dlon, n)
        gy, gx = np.meshgrid(ys, xs, indexing="ij")
        z = self.tiles.elevation(gy.ravel(), gx.ravel()).reshape(n, n)
        inside = np.hypot(np.radians(gy - lat) * EARTH_M, np.radians(gx - lon) * EARTH_M * math.cos(math.radians(lat))) <= r_m
        # Only ground inside the circle competes, so the top of a slope at its edge still counts.
        zin = np.where(inside, z, -np.inf)
        pad = np.pad(zin, 3, mode="constant", constant_values=-np.inf)
        win = np.lib.stride_tricks.sliding_window_view(pad, (7, 7))
        peak = (z >= win.max(axis=(2, 3))) & inside & (z > 0)
        # How far each point stands above the lowest ground around it.
        low = np.where(np.isfinite(win), win, np.inf).min(axis=(2, 3))
        prom = z - low
        cand = sorted(zip(z[peak], prom[peak], gy[peak], gx[peak], strict=False), key=lambda t: -t[0])
        picked: list[dict[str, Any]] = []
        for elev, pr, la, lo in cand:
            if any(distance_m(la, lo, q["lat"], q["lon"]) < min_sep_km * 1000 for q in picked):
                continue
            picked.append({"lat": round(float(la), 6), "lon": round(float(lo), 6), "elevation_m": round(float(elev), 1),
                           "prominence_m": round(float(pr), 1)})
            if len(picked) >= count:
                break
        return picked

    def what_if(self, existing: list[dict[str, Any]], new: list[dict[str, Any]], radius_km=15.0,
                rx_height_m=1.5, cells=260, coarse: bool = False) -> dict[str, Any]:
        """Coverage of your sites today, and what the new sites would add.

        Each site is {lat, lon, height_m}. The answer is one grid over all sites with
        0 = not covered, 1 = covered today, 2 = covered only thanks to a new site.
        """
        sites = existing + new
        if not sites:
            raise TerrainError("add at least one site")
        if len(sites) > 30:
            raise TerrainError("up to 30 sites at a time")
        r_m = float(max(1, min(radius_km, MAX_RADIUS_KM))) * 1000
        lats = [s["lat"] for s in sites]
        lons = [s["lon"] for s in sites]
        lat_c = (min(lats) + max(lats)) / 2
        dlat = math.degrees(r_m / EARTH_M)
        dlon = dlat / max(0.05, math.cos(math.radians(lat_c)))
        north, south = max(lats) + dlat, min(lats) - dlat
        west, east = min(lons) - dlon, max(lons) + dlon
        if north - south > 3 or east - west > 3:
            raise TerrainError("those sites are too far apart; plan one area (under about 3°) at a time")
        h_cells = cells
        w_cells = int(max(40, min(600, cells * (east - west) * math.cos(math.radians(lat_c)) / max(north - south, 1e-6))))
        ys = np.linspace(north, south, h_cells)
        xs = np.linspace(west, east, w_cells)
        gy, gx = np.meshgrid(ys, xs, indexing="ij")
        best_old = np.full(gy.shape, -np.inf)
        best_new = np.full(gy.shape, -np.inf)
        per_site = []
        if coarse:  # ranking many candidates: a quick look is enough
            step, n_az = max(120.0, r_m / 120), 360
        else:
            step = max(75.0, r_m / 200)
            n_az = int(min(720, max(360, 2 * math.pi * r_m / (step * 1.5))))
        for i, s in enumerate(sites):
            az, d, snr, ground = self._radials(s["lat"], s["lon"], float(s.get("height_m", 10)), r_m, n_az, step, rx_height_m)
            dy = np.radians(gy - s["lat"]) * EARTH_M
            dx = np.radians(gx - s["lon"]) * EARTH_M * math.cos(math.radians(s["lat"]))
            dist = np.hypot(dx, dy)
            ai = np.round(np.mod(np.arctan2(dx, dy), 2 * math.pi) / (2 * math.pi) * len(az)).astype(np.int64) % len(az)
            di = np.clip(np.round(dist / d[-1] * len(d)).astype(np.int64) - 1, 0, len(d) - 1)
            m = snr[ai, di] - self.params.snr_limit
            m[dist > r_m] = -np.inf
            if i < len(existing):
                best_old = np.maximum(best_old, m)
            else:
                best_new = np.maximum(best_new, m)
            per_site.append({**s, "ground_m": round(ground, 1), "new": i >= len(existing)})
        old_ok = best_old >= 0
        new_ok = best_new >= 0
        cls = np.zeros(gy.shape, dtype=np.uint8)
        cls[old_ok] = 1
        cls[new_ok & ~old_ok] = 2
        cell_km2 = (abs(north - south) * 111.32 / h_cells) * (abs(east - west) * 111.32 * math.cos(math.radians(lat_c)) / w_cells)
        before = float(old_ok.sum()) * cell_km2
        gained = float((new_ok & ~old_ok).sum()) * cell_km2
        return {
            "bounds": [west, south, east, north], "width": w_cells, "height": h_cells,
            "classes": base64.b64encode(cls.tobytes()).decode(),
            "covered_km2_before": round(before, 1), "gained_km2": round(gained, 1),
            "covered_km2_after": round(before + gained, 1), "sites": per_site,
        }

    def suggest(self, existing: list[dict[str, Any]], lat, lon, radius_km=15.0, height_m=10.0, count=3) -> list[dict[str, Any]]:
        """Rank nearby high points by how much new area a relay there would add."""
        cands = self.high_points(lat, lon, radius_km, count=10)
        out = []
        for c in cands:
            site = {"lat": c["lat"], "lon": c["lon"], "height_m": height_m}
            res = self.what_if(existing, [site], radius_km=min(radius_km, 20), cells=120, coarse=True)
            out.append({**c, "gained_km2": res["gained_km2"]})
        out.sort(key=lambda c: -c["gained_km2"])
        return out[:count]


def encode_margin(margin: np.ndarray) -> dict[str, Any]:
    """Pack a margin grid as bytes: 0 = outside/no data, 1..255 = margin -40..+50 dB."""
    q = np.zeros(margin.shape, dtype=np.uint8)
    ok = np.isfinite(margin)
    q[ok] = np.clip(np.round((margin[ok] + 40) / 90 * 254) + 1, 1, 255).astype(np.uint8)
    return {"margin": base64.b64encode(q.tobytes()).decode(), "margin_scale": {"min_db": -40, "max_db": 50}}


def area_km2(mask: np.ndarray, r_m: float, cells: int) -> float:
    cell = (2 * r_m / 1000 / cells) ** 2
    return float(mask.sum()) * cell


# ---- calibration --------------------------------------------------------------

def calibrate(terrain: Terrain, links: list[dict[str, Any]], heights: dict[str, float],
              default_h: float = 1.5, limit: int = 40) -> dict[str, Any]:
    """Compare predicted and measured SNR on links between nodes with known positions.

    The median difference becomes the offset that coverage and link predictions use.
    Measured SNR on a link is the best the receiver heard, so the offset leans slightly optimistic.
    """
    saved = terrain.params.offset_db
    terrain.params.offset_db = 0.0
    rows = []
    try:
        usable = [lk for lk in links if lk.get("snr") is not None and None not in (lk.get("a_lat"), lk.get("b_lat"))]
        usable.sort(key=lambda lk: -(lk.get("count") or 0))
        for lk in usable[:limit]:
            dist = distance_m(lk["a_lat"], lk["a_lon"], lk["b_lat"], lk["b_lon"])
            if dist < 200 or dist > MAX_PROFILE_KM * 1000:
                continue
            try:
                pr = terrain.profile(lk["a_lat"], lk["a_lon"], heights.get(lk["a"], default_h),
                                     lk["b_lat"], lk["b_lon"], heights.get(lk["b"], default_h), samples=200)
            except TerrainError:
                continue
            pred = pr["budget"]["snr_db"]
            rows.append({"a": lk["a"], "b": lk["b"], "a_name": lk.get("a_name"), "b_name": lk.get("b_name"),
                         "km": round(dist / 1000, 2), "measured_snr": lk["snr"], "predicted_snr": pred,
                         "error_db": round(lk["snr"] - pred, 1)})
    finally:
        terrain.params.offset_db = saved
    if not rows:
        return {"links": [], "offset_db": 0.0, "rms_db": None, "used": 0}
    errs = np.array([r["error_db"] for r in rows])
    off = float(np.clip(np.median(errs), -30, 30))
    rms = float(np.sqrt(np.mean((errs - off) ** 2)))
    return {"links": rows, "offset_db": round(off, 1), "rms_db": round(rms, 1), "used": len(rows)}

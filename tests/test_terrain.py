import math
import struct
import zlib

import numpy as np
import pytest
from fastapi.testclient import TestClient

from notoryx_ops.config import Config
from notoryx_ops.server import create_app
from notoryx_ops.station import Station
from notoryx_ops.terrain import (
    RadioParams,
    Terrain,
    TerrainError,
    Tiles,
    calibrate,
    knife_edge_db,
    read_geotiff,
    tile_name,
)


def make_tiff(z: np.ndarray, lon0: float, lat0: float, px: float, tile: int = 128, predictor: int = 3) -> bytes:
    """A tiled, deflated float32 GeoTIFF like the Copernicus ones."""
    h, w = z.shape
    across, down = math.ceil(w / tile), math.ceil(h / tile)
    full = np.zeros((down * tile, across * tile), np.float32)
    full[:h, :w] = z
    chunks = []
    for ty in range(down):
        for tx in range(across):
            t = full[ty * tile:(ty + 1) * tile, tx * tile:(tx + 1) * tile]
            if predictor == 3:
                b = t.astype(">f4").view(np.uint8).reshape(tile, tile, 4).transpose(0, 2, 1).reshape(tile, tile * 4)
                d = np.diff(b.astype(np.int16), axis=1, prepend=0).astype(np.uint8)
                raw = d.tobytes()
            else:
                raw = t.astype("<f4").tobytes()
            chunks.append(zlib.compress(raw))
    n = len(chunks)
    entries = []
    extra = b""
    base = 8

    def add(tag, typ, vals):
        entries.append((tag, typ, vals))

    add(256, 3, [w])
    add(257, 3, [h])
    add(258, 3, [32])
    add(259, 3, [8])
    add(262, 3, [1])
    add(277, 3, [1])
    add(317, 3, [predictor])
    add(322, 3, [tile])
    add(323, 3, [tile])
    add(324, 4, [0] * n)  # patched below
    add(325, 4, [len(c) for c in chunks])
    add(339, 3, [3])
    add(33550, 12, [px, px, 0.0])
    add(33922, 12, [0.0, 0.0, 0.0, lon0, lat0, 0.0])
    ifd_size = 2 + 12 * len(entries) + 4
    data_start = base + ifd_size
    # Out-of-line values first, then the image chunks.
    fmts = {3: ("H", 2), 4: ("I", 4), 12: ("d", 8)}
    out_of_line = {}
    pos = data_start
    for tag, typ, vals in entries:
        f, sz = fmts[typ]
        if sz * len(vals) > 4:
            out_of_line[tag] = pos
            pos += sz * len(vals)
    offsets = []
    for c in chunks:
        offsets.append(pos)
        pos += len(c)
    entries = [(t, ty, offsets if t == 324 else v) for t, ty, v in entries]
    ifd = struct.pack("<H", len(entries))
    for tag, typ, vals in entries:
        f, sz = fmts[typ]
        if tag in out_of_line:
            ifd += struct.pack("<HHII", tag, typ, len(vals), out_of_line[tag])
            extra += struct.pack("<" + f * len(vals), *vals)
        else:
            ifd += struct.pack("<HHI", tag, typ, len(vals)) + struct.pack("<" + f * len(vals), *vals).ljust(4, b"\0")
    ifd += struct.pack("<I", 0)
    return b"II" + struct.pack("<HI", 42, 8) + ifd + extra + b"".join(chunks)


def hill_tile(lat_floor: int, lon_floor: int, n: int = 240) -> bytes:
    """Flat 100 m ground with a 600 m hill in the middle of the tile."""
    ys, xs = np.mgrid[0:n, 0:n]
    r = np.hypot(ys - n / 2, xs - n / 2)
    z = 100 + 500 * np.exp(-(r / (n / 12)) ** 2)
    return make_tiff(z.astype(np.float32), lon_floor, lat_floor + 1, 1 / n)


def fake_fetch(calls):
    def fetch(url: str):
        calls.append(url)
        part = url.rsplit("/", 1)[1]
        ns, ew = part.split("_")[4], part.split("_")[6]
        la = int(ns[1:]) * (1 if ns[0] == "N" else -1)
        lo = int(ew[1:]) * (1 if ew[0] == "E" else -1)
        if la == 0 and lo == 0:
            return None  # pretend open sea
        return hill_tile(la, lo)
    return fetch


def test_geotiff_round_trip_floating_predictor():
    z = (np.arange(300 * 260, dtype=np.float32).reshape(260, 300) % 977) - 3.25
    g = read_geotiff(make_tiff(z, 7.0, 47.0, 1 / 300, tile=128))
    assert g.z.shape == (260, 300)
    assert np.array_equal(g.z, z)
    assert (g.lon0, g.lat0) == (7.0, 47.0)


def test_geotiff_plain_and_bad_input():
    z = np.full((64, 64), 42.0, np.float32)
    assert read_geotiff(make_tiff(z, 0, 1, 1 / 64, tile=32, predictor=1)).z.mean() == 42.0
    with pytest.raises(TerrainError):
        read_geotiff(b"not a tiff at all")


def test_tile_names():
    assert tile_name(46, 7) == "Copernicus_DSM_COG_30_N46_00_E007_00_DEM"
    assert tile_name(-1, -74, "glo30") == "Copernicus_DSM_COG_10_S01_00_W074_00_DEM"


def test_tiles_cache_and_sea(tmp_path):
    calls = []
    t = Tiles(tmp_path, fetch=fake_fetch(calls))
    peak = t.elevation([44.5], [-72.5])[0]
    assert 590 < peak < 610
    assert t.elevation([44.01], [-72.99])[0] == pytest.approx(100, abs=1)
    assert t.elevation([0.5], [0.5])[0] == 0  # sea tile
    assert len(calls) == 2
    Tiles(tmp_path, fetch=fake_fetch(calls)).elevation([44.5], [-72.5])
    assert len(calls) == 2  # read back from disk
    with pytest.raises(TerrainError):
        t.elevation([95], [0])


def test_knife_edge_values():
    assert knife_edge_db(-1) == 0
    assert knife_edge_db(0) == pytest.approx(6.0, abs=0.1)
    assert knife_edge_db(2.4) == pytest.approx(20.6, abs=0.3)


def test_profile_blocked_by_hill_and_clear_beside_it(tmp_path):
    tr = Terrain(Tiles(tmp_path, fetch=fake_fetch([])), RadioParams())
    across = tr.profile(44.5, -72.85, 2, 44.5, -72.15, 2)
    assert not across["line_of_sight"] and across["budget"]["diffraction_db"] > 10
    beside = tr.profile(44.1, -72.85, 30, 44.1, -72.6, 30)
    assert beside["line_of_sight"]
    assert beside["budget"]["snr_db"] > across["budget"]["snr_db"]
    assert beside["budget"]["free_space_db"] == pytest.approx(
        20 * math.log10(beside["distance_m"] / 1000) + 20 * math.log10(910.525) + 32.44, abs=0.1)


def test_coverage_highpoints_whatif(tmp_path):
    tr = Terrain(Tiles(tmp_path, fetch=fake_fetch([])), RadioParams())
    cov = tr.coverage(44.5, -72.5, 10, 8)
    assert cov["width"] == 220 and cov["covered_km2"] > 50
    hp = tr.high_points(44.5, -72.5, 10)
    assert hp[0]["elevation_m"] > 590
    assert abs(hp[0]["lat"] - 44.5) < 0.01 and abs(hp[0]["lon"] + 72.5) < 0.01
    low = {"lat": 44.3, "lon": -72.75, "height_m": 5}
    res = tr.what_if([low], [{"lat": 44.5, "lon": -72.5, "height_m": 10}], radius_km=10)
    assert res["gained_km2"] > 0 and res["covered_km2_after"] > res["covered_km2_before"]
    sug = tr.suggest([low], 44.5, -72.5, radius_km=8)
    assert sug and sug[0]["gained_km2"] >= sug[-1]["gained_km2"]


def test_calibration_median_offset(tmp_path):
    tr = Terrain(Tiles(tmp_path, fetch=fake_fetch([])), RadioParams())
    pred = tr.profile(44.1, -72.85, 1.5, 44.1, -72.75, 1.5)["budget"]["snr_db"]
    links = [{"a": "a", "b": "b", "snr": pred + 6, "count": 3,
              "a_lat": 44.1, "a_lon": -72.85, "b_lat": 44.1, "b_lon": -72.75}]
    res = calibrate(tr, links, {})
    assert res["used"] == 1 and res["offset_db"] == pytest.approx(6, abs=0.2)


def test_terrain_api(tmp_path):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0)
    with TestClient(create_app(cfg, Station(cfg, terrain_fetch=fake_fetch([])))) as c:
        s = c.get("/api/terrain/status").json()
        assert s["source"] == "glo90" and "Copernicus" in s["attribution"]
        assert c.get("/api/terrain/elevation?lat=44.5&lon=-72.5").json()["elevation_m"] > 590
        pr = c.get("/api/terrain/profile?a_lat=44.2&a_lon=-72.6&b_lat=44.3&b_lon=-72.5").json()
        assert pr["points"] and "margin_db" in pr["budget"]
        cov = c.get("/api/terrain/coverage?lat=44.26&lon=-72.58&radius_km=5").json()
        assert cov["margin"] and len(cov["bounds"]) == 4
        assert c.get("/api/terrain/high-points?lat=44.26&lon=-72.58&radius_km=5").json()
        reps = c.get("/api/terrain/repeaters?lat=44.26&lon=-72.58").json()
        assert reps and all(r["name"].startswith("Demo") for r in reps)
        w = c.post("/api/terrain/what-if", json={"new": [{"lat": 44.3, "lon": -72.55}], "radius_km": 5}).json()
        assert "gained_km2" in w and len(w["sites"]) > 1
        assert c.get("/api/terrain/suggest?lat=44.26&lon=-72.58&radius_km=5").json()["suggestions"]
        cal = c.post("/api/terrain/calibrate").json()
        assert "offset_db" in cal
        assert c.get("/api/terrain/calibration").json()["at"]
        assert c.get("/api/terrain/coverage?lat=95&lon=0").status_code == 422 or \
            c.get("/api/terrain/coverage?lat=95&lon=0").status_code == 400


def test_terrain_settings_round_trip(tmp_path):
    from notoryx_ops import config, settings

    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path)
    settings.apply_changes(cfg, {"terrain": {"source": "glo30", "spreading_factor": 10, "bandwidth_khz": 250,
                                             "use_calibration": False}})
    settings.save(cfg)
    back = config.load(cfg.path)
    assert (back.terrain_source, back.spreading_factor, back.bandwidth_khz, back.use_calibration) == ("glo30", 10, 250.0, False)
    with pytest.raises(ValueError):
        settings.apply_changes(cfg, {"terrain": {"bandwidth_khz": 99}})
    with pytest.raises(ValueError):
        settings.apply_changes(cfg, {"terrain": {"source": "srtm"}})


def test_https_uses_a_bundled_certificate_store():
    import certifi

    from notoryx_ops.net import tls_context

    ctx = tls_context()
    assert ctx.verify_mode.name == "CERT_REQUIRED" and ctx.cert_store_stats()["x509_ca"] > 50
    assert certifi.where()


def test_coverage_map_agrees_with_link_profile(tmp_path):
    import base64

    tr = Terrain(Tiles(tmp_path, fetch=fake_fetch([])), RadioParams())
    site = (44.42, -72.62)
    cov = tr.coverage(*site, 10, 15, 1.5, cells=301)
    west, south, east, north = cov["bounds"]
    px = np.frombuffer(base64.b64decode(cov["margin"]), np.uint8).reshape(301, 301)
    for lat, lon in [(44.5, -72.5), (44.48, -72.47), (44.36, -72.7), (44.44, -72.52)]:
        row = round((north - lat) / (north - south) * 300)
        col = round((lon - west) / (east - west) * 300)
        assert px[row, col], (lat, lon)  # inside the circle
        raster = (int(px[row, col]) - 1) / 254 * 90 - 40
        prof = tr.profile(*site, 10, lat, lon, 1.5)["budget"]["margin_db"]
        assert abs(raster - prof) < 4, (lat, lon, raster, prof)

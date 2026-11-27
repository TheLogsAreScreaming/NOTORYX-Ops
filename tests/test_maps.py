import asyncio
import json
import time
import urllib.error

from fastapi.testclient import TestClient

from notoryx_ops import maps
from notoryx_ops.config import Config
from notoryx_ops.server import create_app
from notoryx_ops.station import Station

STYLE = {
    "version": 8,
    "sources": {"openmaptiles": {"type": "vector", "url": "https://tiles.example/planet"},
                "shade": {"type": "raster", "tiles": ["https://tiles.example/shade/{z}/{x}/{y}.png"], "maxzoom": 6}},
    "glyphs": "https://tiles.example/fonts/{fontstack}/{range}.pbf",
    "sprite": "https://tiles.example/sprites/ofm",
    "layers": [{"id": "place", "type": "symbol", "layout": {"text-font": ["Noto Sans Regular"]}}],
}


class FakeServer:
    def __init__(self):
        self.online = True
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if not self.online:
            raise urllib.error.URLError("offline")
        if url == maps.STYLE_URL:
            return json.dumps(STYLE).encode()
        if url == "https://tiles.example/planet":
            return json.dumps({"tiles": ["https://tiles.example/v1/{z}/{x}/{y}.pbf"]}).encode()
        if "/v1/" in url or "/shade/" in url or "/fonts/" in url or "/sprites/" in url:
            return ("tile:" + url).encode()
        raise urllib.error.HTTPError(url, 404, "nope", {}, None)


def test_style_points_at_station_and_tiles_work_offline(tmp_path):
    fake = FakeServer()
    cache = maps.MapCache(tmp_path / "m.sqlite", fetch=fake)
    style = cache.style("http://station:8484")
    assert style["sources"]["openmaptiles"]["tiles"] == ["http://station:8484/maps/t/openmaptiles/{z}/{x}/{y}.pbf"]
    assert style["sources"]["shade"]["tiles"][0].endswith("/maps/t/shade/{z}/{x}/{y}.png")
    assert style["glyphs"].startswith("http://station:8484/maps/fonts/")
    assert cache.tile("openmaptiles", 3, 2, 1) == b"tile:https://tiles.example/v1/3/2/1.pbf"
    fake.online = False
    assert cache.tile("openmaptiles", 3, 2, 1) == b"tile:https://tiles.example/v1/3/2/1.pbf"
    assert cache.style("http://x")["version"] == 8  # style kept too
    try:
        cache.tile("openmaptiles", 3, 2, 2)
        raise AssertionError("should be offline")
    except maps.Offline:
        pass
    assert cache.tile("openmaptiles", 3, 99, 0) is None  # off the edge of the world


def test_tile_counts_and_limits():
    vt = [-72.7, 44.2, -72.5, 44.35]
    assert maps.count_tiles(vt, 0, 0) == 1
    assert maps.count_tiles(vt, 0, 10) < maps.count_tiles(vt, 0, 13)
    for bad in ([1, 2, 3], [10, 0, 5, 1], [0, 0, 1, 90]):
        try:
            maps.check_bbox(bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_pack_download_pins_export_import_and_trim(tmp_path):
    fake = FakeServer()
    cache = maps.MapCache(tmp_path / "m.sqlite", fetch=fake, limit_bytes=1)
    made = cache.add_pack("Town", [-72.6, 44.25, -72.55, 44.28], 0, 6)
    view = asyncio.run(cache.download(made["id"]))
    assert view["state"] == "ready" and view["tiles"] == made["tiles"]
    cache.tile("openmaptiles", 9, 0, 0)  # plain browsing, not pinned
    assert cache.trim() == 1
    assert cache.summary()["pinned"] == made["tiles"]
    out = cache.export_mbtiles(made["id"], tmp_path / "town.mbtiles")
    other = maps.MapCache(tmp_path / "other.sqlite", fetch=FakeServer())
    got = other.import_mbtiles(out)
    assert got["tiles"] == made["tiles"]
    other.fetch.online = False
    assert other.tile("openmaptiles", 0, 0, 0) == b"tile:https://tiles.example/v1/0/0/0.pbf"
    cache.delete_pack(made["id"])
    assert cache.summary()["pinned"] == 0 and cache.summary()["packs"] == []


def test_pack_too_big_is_refused(tmp_path):
    cache = maps.MapCache(tmp_path / "m.sqlite", fetch=FakeServer())
    try:
        cache.add_pack("Planet", [-179, -80, 179, 80], 0, 14)
        raise AssertionError("too big")
    except ValueError as exc:
        assert "tiles" in str(exc)


def test_map_routes(tmp_path):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0)
    fake = FakeServer()
    with TestClient(create_app(cfg, Station(cfg, maps_fetch=fake))) as c:
        s = c.get("/maps/style.json").json()
        assert s["sources"]["openmaptiles"]["tiles"][0].startswith("http://testserver/maps/t/")
        r = c.get("/maps/t/openmaptiles/2/1/1.pbf")
        assert r.status_code == 200 and r.content.startswith(b"tile:")
        assert c.get("/maps/t/openmaptiles/2/9/9.pbf").status_code == 204
        assert c.get("/maps/fonts/Noto Sans Regular/0-255.pbf").status_code == 200
        assert c.get("/maps/sprite/sprite.json").status_code == 200
        assert c.get("/maps/sprite/other.json").status_code == 404
        est = c.get("/api/maps/estimate?west=-72.6&south=44.25&east=-72.55&north=44.28&max_zoom=8").json()
        assert est["tiles"] > 0
        made = c.post("/api/maps/packs", json={"name": "Town", "bbox": [-72.6, 44.25, -72.55, 44.28], "max_zoom": 5}).json()
        for _ in range(50):
            packs = c.get("/api/maps").json()["packs"]
            if packs and packs[0]["state"] == "ready":
                break
            time.sleep(0.1)
        assert packs[0]["state"] == "ready"
        exp = c.get(f"/api/maps/packs/{made['id']}/export.mbtiles")
        assert exp.status_code == 200 and exp.content[:15] == b"SQLite format 3"
        imp = c.post("/api/maps/import?name=Copy", content=exp.content)
        assert imp.status_code == 200 and imp.json()["tiles"] == made["tiles"]
        assert c.post("/api/maps/import", content=b"not a database").status_code == 400
        assert c.delete(f"/api/maps/packs/{made['id']}").json()["ok"]

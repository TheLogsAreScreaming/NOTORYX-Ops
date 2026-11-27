"""Base map tiles kept on the station, so the dashboard map works with no internet.

The station sits between the browser and the tile server. Every tile, font and sprite
the dashboard asks for is fetched once and stored in maps.sqlite. On top of that,
"map packs" download a whole area ahead of time and keep it pinned, and packs can be
exported and imported as MBTiles files to carry to a station that has no internet.

Plain browsing adds to a size capped cache; tiles in a pack are never evicted.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import math
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .net import urlopen

log = logging.getLogger(__name__)

UPSTREAM = "https://tiles.openfreemap.org"
STYLE_URL = UPSTREAM + "/styles/positron"
USER_AGENT = "NOTORYX-Ops map cache"
MAX_PACK_TILES = 25_000           # about a county down to street level
MAX_PACK_ZOOM = 14                # vector tiles stop here; the map overzooms past it
CACHE_LIMIT_BYTES = 500 * 1024 * 1024
FONT_RANGES = [f"{a}-{a + 255}" for a in range(0, 0x3000, 256)]  # Latin, Greek, Cyrillic and punctuation
SAFE = re.compile(r"^[A-Za-z0-9 _.,@-]{1,120}$")

Fetch = Callable[[str], bytes]


def default_fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    with urlopen(req, timeout=20) as r:
        return r.read()


class Offline(Exception):
    """The tile isn't on the station and the tile server can't be reached."""


def tile_range(bbox: list[float], z: int) -> tuple[int, int, int, int]:
    """x0, y0, x1, y1 of the tiles that cover [west, south, east, north] at zoom z."""
    w, s, e, n = bbox
    def tx(lon: float) -> int:
        return min(2**z - 1, max(0, int((lon + 180) / 360 * 2**z)))

    def ty(lat: float) -> int:
        lat = max(-85.0511, min(85.0511, lat))
        r = math.radians(lat)
        return min(2**z - 1, max(0, int((1 - math.asinh(math.tan(r)) / math.pi) / 2 * 2**z)))

    return tx(w), ty(n), tx(e), ty(s)


def count_tiles(bbox: list[float], min_zoom: int, max_zoom: int) -> int:
    total = 0
    for z in range(min_zoom, max_zoom + 1):
        x0, y0, x1, y1 = tile_range(bbox, z)
        total += (x1 - x0 + 1) * (y1 - y0 + 1)
    return total


def check_bbox(bbox: list[float]) -> list[float]:
    if len(bbox) != 4 or not all(isinstance(v, int | float) and math.isfinite(v) for v in bbox):
        raise ValueError("The area needs four numbers: west, south, east, north.")
    w, s, e, n = (float(v) for v in bbox)
    if not (-180 <= w < e <= 180 and -85 <= s < n <= 85):
        raise ValueError("That area isn't on the map. West must be left of east and south below north.")
    return [w, s, e, n]


class MapCache:
    def __init__(self, path: Path, fetch: Fetch | None = None, limit_bytes: int = CACHE_LIMIT_BYTES):
        self.path = path
        self.fetch = fetch or default_fetch
        self.limit = limit_bytes
        self._lock = threading.Lock()
        self._templates: dict[str, str] = {}
        self.jobs: dict[int, dict[str, Any]] = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS tiles (
              source TEXT NOT NULL, z INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
              data BLOB NOT NULL, used REAL NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (source, z, x, y)
            );
            CREATE TABLE IF NOT EXISTS assets (
              key TEXT PRIMARY KEY, data BLOB NOT NULL, ctype TEXT NOT NULL, fetched REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS packs (
              id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, bbox TEXT NOT NULL,
              min_zoom INTEGER NOT NULL, max_zoom INTEGER NOT NULL, tiles INTEGER NOT NULL DEFAULT 0,
              state TEXT NOT NULL DEFAULT 'queued', created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pack_tiles (
              pack INTEGER NOT NULL, source TEXT NOT NULL, z INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
              PRIMARY KEY (pack, source, z, x, y)
            );
            """
        )

    # ---- small helpers -------------------------------------------------------
    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid or 0

    def _asset(self, key: str, url: str, ctype: str, max_age: float = 30 * 86400) -> tuple[bytes, str]:
        row = self._q("SELECT data, ctype, fetched FROM assets WHERE key = ?", (key,))
        if row and time.time() - row[0]["fetched"] < max_age:
            return row[0]["data"], row[0]["ctype"]
        try:
            data = self.fetch(url)
        except (OSError, urllib.error.URLError) as exc:
            if row:  # stale beats nothing when the internet is down
                return row[0]["data"], row[0]["ctype"]
            raise Offline(str(exc)) from exc
        self._x("INSERT OR REPLACE INTO assets (key, data, ctype, fetched) VALUES (?, ?, ?, ?)",
                (key, data, ctype, time.time()))
        return data, ctype

    # ---- style ------------------------------------------------------------------
    def upstream_style(self) -> dict[str, Any]:
        data, _ = self._asset("style", STYLE_URL, "application/json", max_age=86400)
        return json.loads(data)

    def template(self, source: str) -> str:
        """The upstream tile URL for one style source, read from its TileJSON when it has one."""
        if source in self._templates:
            return self._templates[source]
        src = (self.upstream_style().get("sources") or {}).get(source)
        if not src:
            raise KeyError(source)
        tiles = src.get("tiles")
        if not tiles and src.get("url"):
            data, _ = self._asset(f"tilejson:{source}", src["url"], "application/json", max_age=86400)
            tiles = json.loads(data).get("tiles")
        if not tiles:
            raise KeyError(source)
        self._templates[source] = tiles[0]
        return tiles[0]

    def style(self, base: str) -> dict[str, Any]:
        """The base map style with every URL pointing at this station."""
        style = json.loads(json.dumps(self.upstream_style()))
        for name, src in (style.get("sources") or {}).items():
            if src.get("type") not in ("vector", "raster"):
                continue
            ext = "pbf" if src["type"] == "vector" else "png"
            src.pop("url", None)
            src["tiles"] = [f"{base}/maps/t/{name}/{{z}}/{{x}}/{{y}}.{ext}"]
            if src["type"] == "vector":
                src.setdefault("minzoom", 0)
                src["maxzoom"] = MAX_PACK_ZOOM
        style["glyphs"] = f"{base}/maps/fonts/{{fontstack}}/{{range}}.pbf"
        if isinstance(style.get("sprite"), str):
            style["sprite"] = f"{base}/maps/sprite/sprite"
        return style

    def fonts(self) -> list[str]:
        out: set[str] = set()
        for layer in self.upstream_style().get("layers") or []:
            for f in (layer.get("layout") or {}).get("text-font") or []:
                if isinstance(f, str):
                    out.add(f)
        return sorted(out)

    def font(self, stack: str, rng: str) -> bytes:
        if not SAFE.match(stack) or not re.fullmatch(r"\d{1,5}-\d{1,5}", rng):
            raise KeyError(stack)
        url = self.upstream_style()["glyphs"].replace("{fontstack}", urllib.request.quote(stack)).replace("{range}", rng)
        return self._asset(f"font:{stack}:{rng}", url, "application/x-protobuf", max_age=365 * 86400)[0]

    def sprite(self, name: str) -> tuple[bytes, str]:
        if name not in ("sprite.json", "sprite.png", "sprite@2x.json", "sprite@2x.png"):
            raise KeyError(name)
        base = self.upstream_style().get("sprite")
        if not isinstance(base, str):
            raise KeyError(name)
        suffix = name[len("sprite"):]
        ctype = "application/json" if name.endswith(".json") else "image/png"
        return self._asset(f"sprite:{name}", base + suffix, ctype, max_age=30 * 86400)

    # ---- tiles --------------------------------------------------------------------
    def tile(self, source: str, z: int, x: int, y: int, fetch: bool = True) -> bytes | None:
        """A tile from the station, or from the tile server (then kept). None when there's no such tile."""
        if not (0 <= z <= 22 and 0 <= x < 2**z and 0 <= y < 2**z):
            return None
        row = self._q("SELECT data FROM tiles WHERE source = ? AND z = ? AND x = ? AND y = ?", (source, z, x, y))
        if row:
            self._x("UPDATE tiles SET used = ? WHERE source = ? AND z = ? AND x = ? AND y = ?",
                    (time.time(), source, z, x, y))
            return row[0]["data"]
        if not fetch:
            return None
        url = self.template(source).replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
        try:
            data = self.fetch(url)
        except urllib.error.HTTPError as exc:
            if exc.code in (204, 404):
                return None
            raise Offline(str(exc)) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise Offline(str(exc)) from exc
        self._x("INSERT OR IGNORE INTO tiles (source, z, x, y, data, used) VALUES (?, ?, ?, ?, ?, ?)",
                (source, z, x, y, data, time.time()))
        return data

    def trim(self) -> int:
        """Drop the least recently used unpinned tiles until the cache is under its limit."""
        size = self._q("SELECT COALESCE(SUM(LENGTH(data)), 0) n FROM tiles WHERE pinned = 0")[0]["n"]
        dropped = 0
        while size > self.limit:
            rows = self._q("SELECT source, z, x, y, LENGTH(data) n FROM tiles WHERE pinned = 0 ORDER BY used LIMIT 500")
            if not rows:
                break
            for r in rows:
                self._x("DELETE FROM tiles WHERE source = ? AND z = ? AND x = ? AND y = ?", (r["source"], r["z"], r["x"], r["y"]))
                size -= r["n"]
                dropped += 1
        return dropped

    def summary(self) -> dict[str, Any]:
        r = self._q("SELECT COUNT(*) n, COALESCE(SUM(LENGTH(data)), 0) b, COALESCE(SUM(pinned), 0) p FROM tiles")[0]
        return {"tiles": r["n"], "bytes": r["b"], "pinned": r["p"], "limit_bytes": self.limit,
                "packs": [self._pack_view(p) for p in self._q("SELECT * FROM packs ORDER BY created DESC")]}

    def _pack_view(self, p: sqlite3.Row) -> dict[str, Any]:
        job = self.jobs.get(p["id"], {})
        return {"id": p["id"], "name": p["name"], "bbox": json.loads(p["bbox"]), "min_zoom": p["min_zoom"],
                "max_zoom": p["max_zoom"], "tiles": p["tiles"], "state": job.get("state", p["state"]),
                "done": job.get("done"), "total": job.get("total"), "failed": job.get("failed", 0),
                "created": p["created"]}

    # ---- packs -------------------------------------------------------------------
    def add_pack(self, name: str, bbox: list[float], min_zoom: int = 0, max_zoom: int = 13) -> dict[str, Any]:
        bbox = check_bbox(bbox)
        name = " ".join(name.split())[:60]
        if not name:
            raise ValueError("Give the map pack a name.")
        if not (0 <= min_zoom <= max_zoom <= MAX_PACK_ZOOM):
            raise ValueError(f"Zoom goes from 0 to {MAX_PACK_ZOOM}.")
        n = count_tiles(bbox, min_zoom, max_zoom)
        if n > MAX_PACK_TILES:
            raise ValueError(f"That's {n:,} tiles. Make the area smaller or lower the top zoom (limit {MAX_PACK_TILES:,}).")
        pid = self._x("INSERT INTO packs (name, bbox, min_zoom, max_zoom, created) VALUES (?, ?, ?, ?, ?)",
                      (name, json.dumps(bbox), min_zoom, max_zoom, time.time()))
        return {"id": pid, "tiles": n}

    def vector_sources(self) -> list[str]:
        return [k for k, v in (self.upstream_style().get("sources") or {}).items() if v.get("type") == "vector"]

    async def download(self, pid: int, workers: int = 4) -> dict[str, Any]:
        """Fetch every tile of a pack (plus fonts and sprites) and pin them."""
        p = self._q("SELECT * FROM packs WHERE id = ?", (pid,))
        if not p:
            raise KeyError(pid)
        p = p[0]
        bbox = json.loads(p["bbox"])
        sources = await asyncio.to_thread(self.vector_sources)
        todo = [(s, z, x, y) for s in sources for z in range(p["min_zoom"], p["max_zoom"] + 1)
                for x in range(tile_range(bbox, z)[0], tile_range(bbox, z)[2] + 1)
                for y in range(tile_range(bbox, z)[1], tile_range(bbox, z)[3] + 1)]
        job = self.jobs[pid] = {"state": "downloading", "done": 0, "total": len(todo), "failed": 0}
        self._x("UPDATE packs SET state = 'downloading' WHERE id = ?", (pid,))
        queue: asyncio.Queue = asyncio.Queue()
        for t in todo:
            queue.put_nowait(t)

        async def worker():
            while not queue.empty():
                s, z, x, y = queue.get_nowait()
                try:
                    data = await asyncio.to_thread(self.tile, s, z, x, y)
                    if data is not None:
                        self._x("UPDATE tiles SET pinned = 1 WHERE source = ? AND z = ? AND x = ? AND y = ?", (s, z, x, y))
                        self._x("INSERT OR IGNORE INTO pack_tiles (pack, source, z, x, y) VALUES (?, ?, ?, ?, ?)", (pid, s, z, x, y))
                except Offline:
                    job["failed"] += 1
                job["done"] += 1

        await asyncio.gather(*(worker() for _ in range(workers)))
        job["state"] = "finishing"  # fonts and map icons, so labels work offline too
        for stack in await asyncio.to_thread(self.fonts):
            for rng in FONT_RANGES:
                try:
                    await asyncio.to_thread(self.font, stack, rng)
                except (Offline, KeyError):
                    pass
        for name in ("sprite.json", "sprite.png", "sprite@2x.json", "sprite@2x.png"):
            try:
                await asyncio.to_thread(self.sprite, name)
            except (Offline, KeyError):
                pass
        kept = self._q("SELECT COUNT(*) n FROM pack_tiles WHERE pack = ?", (pid,))[0]["n"]
        state = "ready" if job["failed"] == 0 else "partial"
        job["state"] = state
        self._x("UPDATE packs SET state = ?, tiles = ? WHERE id = ?", (state, kept, pid))
        return self._pack_view(self._q("SELECT * FROM packs WHERE id = ?", (pid,))[0])

    def delete_pack(self, pid: int) -> None:
        self._x("DELETE FROM pack_tiles WHERE pack = ?", (pid,))
        self._x("DELETE FROM packs WHERE id = ?", (pid,))
        self.jobs.pop(pid, None)
        self._x("UPDATE tiles SET pinned = 0 WHERE pinned = 1 AND NOT EXISTS (SELECT 1 FROM pack_tiles pt WHERE"
                " pt.source = tiles.source AND pt.z = tiles.z AND pt.x = tiles.x AND pt.y = tiles.y)")

    # ---- MBTiles export / import -------------------------------------------------
    def export_mbtiles(self, pid: int, dest: Path) -> Path:
        p = self._q("SELECT * FROM packs WHERE id = ?", (pid,))
        if not p:
            raise KeyError(pid)
        p = p[0]
        dest.unlink(missing_ok=True)
        out = sqlite3.connect(dest)
        out.executescript("CREATE TABLE metadata (name TEXT, value TEXT);"
                          "CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB);"
                          "CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row);")
        bbox = json.loads(p["bbox"])
        meta = {"name": p["name"], "format": "pbf", "type": "baselayer", "minzoom": str(p["min_zoom"]),
                "maxzoom": str(p["max_zoom"]), "bounds": ",".join(f"{v:.5f}" for v in bbox),
                "attribution": "© OpenMapTiles © OpenStreetMap contributors", "generator": "NOTORYX Ops"}
        out.executemany("INSERT INTO metadata VALUES (?, ?)", list(meta.items()))
        rows = self._q("SELECT t.z, t.x, t.y, t.data FROM pack_tiles pt JOIN tiles t ON t.source = pt.source"
                       " AND t.z = pt.z AND t.x = pt.x AND t.y = pt.y WHERE pt.pack = ?", (pid,))
        out.executemany("INSERT OR IGNORE INTO tiles VALUES (?, ?, ?, ?)",
                        [(r["z"], r["x"], (2 ** r["z"] - 1) - r["y"], r["data"]) for r in rows])
        out.commit()
        out.close()
        return dest

    def import_mbtiles(self, src: Path, name: str | None = None) -> dict[str, Any]:
        """Add an MBTiles file of OpenMapTiles vector tiles as a pinned pack."""
        try:
            inp = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            meta = dict(inp.execute("SELECT name, value FROM metadata").fetchall())
        except sqlite3.DatabaseError as exc:
            raise ValueError("That isn't an MBTiles file.") from exc
        if (meta.get("format") or "pbf") != "pbf":
            inp.close()
            raise ValueError("Only vector map packs (pbf) work here.")
        sources = self.vector_sources() or ["openmaptiles"]
        source = sources[0]
        try:
            bounds = [float(v) for v in (meta.get("bounds") or "-180,-85,180,85").split(",")]
            bbox = check_bbox(bounds)
        except ValueError:
            bbox = [-180.0, -85.0, 180.0, 85.0]
        zs = inp.execute("SELECT MIN(zoom_level), MAX(zoom_level) FROM tiles").fetchone()
        pid = self._x("INSERT INTO packs (name, bbox, min_zoom, max_zoom, state, created) VALUES (?, ?, ?, ?, 'ready', ?)",
                      ((name or meta.get("name") or src.stem)[:60], json.dumps(bbox), zs[0] or 0, zs[1] or 0, time.time()))
        n = 0
        now = time.time()
        for z, x, row, data in inp.execute("SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"):
            y = (2 ** z - 1) - row
            self._x("INSERT OR REPLACE INTO tiles (source, z, x, y, data, used, pinned) VALUES (?, ?, ?, ?, ?, ?, 1)",
                    (source, z, x, y, data, now))
            self._x("INSERT OR IGNORE INTO pack_tiles (pack, source, z, x, y) VALUES (?, ?, ?, ?, ?)", (pid, source, z, x, y))
            n += 1
        inp.close()
        self._x("UPDATE packs SET tiles = ? WHERE id = ?", (n, pid))
        return {"id": pid, "tiles": n}

    def close(self) -> None:
        with self._lock:
            self.db.close()


def is_gzip(data: bytes) -> bool:
    return data[:2] == b"\x1f\x8b"


def gunzip_if_needed(data: bytes) -> bytes:
    return gzip.decompress(data) if is_gzip(data) else data

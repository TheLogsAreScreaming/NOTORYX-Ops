"""Builds src/notoryx_ops/geo/countries.json from Natural Earth (public domain).

Usage: python tools/build_countries.py ne_50m_admin_0_countries.geojson
Source: https://github.com/nvkelso/natural-earth-vector (v5.1.2, geojson/ne_50m_admin_0_countries.geojson)
Positions are rounded to 0.05 degrees (about 5 km): enough to name a country, small enough to ship.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

STEP = 0.05


def ring(coords: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for lon, lat in coords:
        p = [round(round(lon / STEP) * STEP, 2), round(round(lat / STEP) * STEP, 2)]
        if not out or out[-1] != p:
            out.append(p)
    return out if len(out) >= 4 else []


def main(src: str) -> None:
    feats = json.loads(Path(src).read_text())["features"]
    out = []
    for f in feats:
        p = f["properties"]
        cc = p.get("ISO_A2_EH") or p.get("ISO_A2")
        if not cc or cc == "-99":
            continue
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        rings = [r for poly in polys for r in [ring(poly[0])] if r]  # outer rings only
        if not rings:
            continue
        xs = [x for r in rings for x, _ in r]
        ys = [y for r in rings for _, y in r]
        out.append({"cc": cc, "name": p["NAME"], "bbox": [min(xs), min(ys), max(xs), max(ys)], "rings": rings})
    dst = Path(__file__).resolve().parent.parent / "src/notoryx_ops/geo/countries.json"
    dst.write_text(json.dumps({"source": "Natural Earth 1:50m admin 0 countries v5.1.2 (public domain)",
                               "countries": out}, separators=(",", ":")))
    print(f"{len(out)} countries -> {dst} ({dst.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])

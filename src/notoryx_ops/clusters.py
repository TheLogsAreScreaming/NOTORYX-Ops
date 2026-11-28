"""Grid-based server-side clustering of node map markers.

At low zoom levels many nodes overlap on the map.  This module bins nodes into
a geographic grid whose cell size matches the given tile-zoom level, then
returns a mix of individual markers (cells with one node) and cluster markers
(cells with two or more nodes).

The grid uses Web Mercator tile conventions:
  cell width  = 360 / 2**zoom  degrees of longitude
  cell height = 180 / 2**zoom  degrees of latitude  (approximate; good enough)

Nodes whose lat or lon is NULL are returned as unclustered singletons.
"""

from __future__ import annotations

import math
from typing import Any

_MIN_ZOOM = 1
_MAX_ZOOM = 18
_CLUSTER_NODE_LIMIT = 10  # include full node list in a cluster up to this count


def _cell(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    n = 2**zoom
    cx = math.floor((lon + 180) / 360 * n)
    cy = math.floor((lat + 90) / 180 * n)
    return cx, cy


def _cell_center(cx: int, cy: int, zoom: int) -> tuple[float, float]:
    n = 2**zoom
    lon = (cx + 0.5) / n * 360 - 180
    lat = (cy + 0.5) / n * 180 - 90
    return round(lat, 6), round(lon, 6)


def build_clusters(nodes: list[dict[str, Any]], zoom: int) -> list[dict[str, Any]]:
    """Cluster *nodes* at *zoom* and return a list of map features.

    Each feature has:
      type        "cluster" or "node"
      lat / lon   position for the marker
      count       1 for singletons, N for clusters
      nodes       list of up to _CLUSTER_NODE_LIMIT node records (always present for type "node")
    """
    zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, zoom))
    cells: dict[tuple[int, int], list[dict[str, Any]]] = {}
    singletons: list[dict[str, Any]] = []

    for node in nodes:
        lat, lon = node.get("lat"), node.get("lon")
        if lat is None or lon is None:
            singletons.append(node)
            continue
        key = _cell(lat, lon, zoom)
        cells.setdefault(key, []).append(node)

    out: list[dict[str, Any]] = []
    for (cx, cy), cell_nodes in cells.items():
        clat, clon = _cell_center(cx, cy, zoom)
        if len(cell_nodes) == 1:
            n = cell_nodes[0]
            out.append({
                "type": "node",
                "lat": n["lat"],
                "lon": n["lon"],
                "count": 1,
                "nodes": [n],
            })
        else:
            avg_lat = sum(n["lat"] for n in cell_nodes) / len(cell_nodes)
            avg_lon = sum(n["lon"] for n in cell_nodes) / len(cell_nodes)
            out.append({
                "type": "cluster",
                "lat": round(avg_lat, 6),
                "lon": round(avg_lon, 6),
                "count": len(cell_nodes),
                "nodes": cell_nodes[:_CLUSTER_NODE_LIMIT],
            })

    # nodes without positions: return as unclustered
    for node in singletons:
        out.append({"type": "node", "lat": None, "lon": None, "count": 1, "nodes": [node]})

    return out

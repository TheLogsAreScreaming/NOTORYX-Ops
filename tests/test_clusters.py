"""Tests for server-side node marker clustering."""

from __future__ import annotations

from fastapi.testclient import TestClient

from notoryx_ops.clusters import build_clusters
from notoryx_ops.config import Config
from notoryx_ops.server import create_app


def _node(key: str, lat: float | None, lon: float | None) -> dict:
    return {"public_key": key, "name": key[:4], "lat": lat, "lon": lon}


# ---- unit tests for clusters module -----------------------------------------

def test_empty_nodes():
    assert build_clusters([], zoom=10) == []


def test_single_node_is_singleton():
    result = build_clusters([_node("aa", 40.0, -74.0)], zoom=10)
    assert len(result) == 1
    assert result[0]["type"] == "node"
    assert result[0]["count"] == 1


def test_two_nodes_same_cell_become_cluster():
    # At zoom 1 the entire world is 2x2 cells — anything in same hemisphere clusters
    n1 = _node("aa", 40.0, -74.0)
    n2 = _node("bb", 41.0, -73.0)
    result = build_clusters([n1, n2], zoom=1)
    clusters = [r for r in result if r["type"] == "cluster"]
    assert len(clusters) == 1
    assert clusters[0]["count"] == 2


def test_two_nodes_different_cells_at_high_zoom():
    # At zoom 18 the cells are tiny — far-apart nodes stay separate
    n1 = _node("aa", 40.0, -74.0)
    n2 = _node("bb", 41.0, -73.0)
    result = build_clusters([n1, n2], zoom=18)
    assert all(r["type"] == "node" for r in result)
    assert len(result) == 2


def test_node_without_position_returned_as_singleton():
    n = _node("aa", None, None)
    result = build_clusters([n], zoom=10)
    assert len(result) == 1
    assert result[0]["type"] == "node"
    assert result[0]["lat"] is None


def test_cluster_contains_node_list():
    n1 = _node("aa", 40.0, -74.0)
    n2 = _node("bb", 40.1, -74.1)
    result = build_clusters([n1, n2], zoom=1)
    cluster = next(r for r in result if r["type"] == "cluster")
    assert len(cluster["nodes"]) == 2


def test_zoom_clamped():
    n = _node("aa", 40.0, -74.0)
    # Should not raise with out-of-range zoom
    r1 = build_clusters([n], zoom=0)
    r2 = build_clusters([n], zoom=99)
    assert len(r1) == 1
    assert len(r2) == 1


def test_many_nodes_different_cells():
    # Nodes spread across the globe at zoom 1 should not all cluster together
    nodes = [
        _node("aa", 40.0, -74.0),   # NW quadrant
        _node("bb", 40.0, 2.0),     # NE quadrant
        _node("cc", -33.0, 151.0),  # SE quadrant
        _node("dd", -23.0, -46.0),  # SW quadrant
    ]
    result = build_clusters(nodes, zoom=2)
    types = [r["type"] for r in result]
    # At zoom 2 the world is 4x4 cells — widely spread nodes should be separate
    assert all(t == "node" for t in types)
    assert len(result) == 4


def test_cluster_node_count_matches():
    nodes = [_node(f"{i:02x}" * 32, 40.0, -74.0) for i in range(5)]
    result = build_clusters(nodes, zoom=1)
    cluster = next(r for r in result if r["type"] == "cluster")
    assert cluster["count"] == 5


# ---- API tests --------------------------------------------------------------

def make(tmp_path, **kw):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, **kw)
    return TestClient(create_app(cfg))


def test_clusters_endpoint_returns_list(tmp_path):
    with make(tmp_path) as c:
        r = c.get("/api/nodes/clusters")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


def test_clusters_endpoint_zoom_param(tmp_path):
    with make(tmp_path) as c:
        r = c.get("/api/nodes/clusters?zoom=5")
        assert r.status_code == 200


def test_clusters_endpoint_each_has_required_fields(tmp_path):
    with make(tmp_path) as c:
        body = c.get("/api/nodes/clusters?zoom=10").json()
        for item in body:
            assert "type" in item
            assert "count" in item
            assert "nodes" in item
            assert item["type"] in ("node", "cluster")

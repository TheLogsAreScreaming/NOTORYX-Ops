"""Tests for passive path-trace reconstruction from stored packets."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from notoryx_ops.config import Config
from notoryx_ops.pathtrace import observed_paths, resolve_paths
from notoryx_ops.server import create_app
from notoryx_ops.store import Store

KEY_REP = "aa" * 32
KEY_OTHER = "bb" * 32


def _pkt(store: Store, path: list[str], ts: float, h: str) -> None:
    store.add_packet({
        "hash": h,
        "received_at": ts,
        "observer": "o",
        "source": "cc",
        "route": "flood",
        "ptype": "advert",
        "hops": len(path),
        "hash_size": 1,
        "path": path,
        "raw": "00",
    })


# ---- unit tests for pathtrace module ----------------------------------------

def test_observed_paths_empty():
    store = Store(":memory:")
    result = observed_paths(store, KEY_REP, days=7)
    assert result == []


def test_observed_paths_basic():
    store = Store(":memory:")
    now = time.time()
    _pkt(store, ["aa", "bb"], now - 10, "p1")
    _pkt(store, ["aa", "bb"], now - 5, "p2")
    result = observed_paths(store, KEY_REP, days=7)
    assert len(result) == 1
    assert result[0]["count"] == 2
    assert result[0]["path"] == ["aa", "bb"]


def test_observed_paths_excludes_direct(tmp_path):
    """Packets with hops=0 have empty paths and should not appear."""
    store = Store(":memory:")
    now = time.time()
    store.add_packet({
        "hash": "direct1",
        "received_at": now,
        "observer": "o",
        "source": "aa",
        "route": "flood",
        "ptype": "advert",
        "hops": 0,
        "hash_size": 1,
        "path": [],
        "raw": "00",
    })
    result = observed_paths(store, KEY_REP, days=7)
    assert result == []


def test_observed_paths_excludes_irrelevant_node():
    store = Store(":memory:")
    now = time.time()
    _pkt(store, ["cc", "dd"], now, "p1")
    result = observed_paths(store, KEY_REP, days=7)
    assert result == []


def test_observed_paths_counts_unique_paths():
    store = Store(":memory:")
    now = time.time()
    _pkt(store, ["aa", "bb"], now - 30, "p1")
    _pkt(store, ["aa", "bb"], now - 20, "p2")
    _pkt(store, ["aa", "cc"], now - 10, "p3")
    result = observed_paths(store, KEY_REP, days=7)
    assert len(result) == 2
    assert result[0]["count"] == 2


def test_observed_paths_since_filter():
    store = Store(":memory:")
    now = time.time()
    _pkt(store, ["aa", "bb"], now - 3600, "old")
    _pkt(store, ["aa", "bb"], now - 60, "new")
    result_all = observed_paths(store, KEY_REP, days=7)
    result_narrow = observed_paths(store, KEY_REP, days=0.0005)  # ~43 seconds
    assert result_all[0]["count"] == 2
    assert result_narrow[0]["count"] == 1


def test_resolve_paths_adds_hops():
    store = Store(":memory:")
    now = time.time()
    store.ins(
        "INSERT INTO nodes(public_key, prefix, name, node_type, lat, lon, first_seen, last_seen)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (KEY_REP, "aa", "Rep A", "repeater", None, None, now, now),
    )
    paths = [{"path": ["aa", "bb"], "count": 1, "last_seen": time.time()}]
    result = resolve_paths(store, paths)
    assert len(result) == 1
    hops = result[0]["hops"]
    assert len(hops) == 2
    assert hops[0]["prefix"] == "aa"
    aa_matches = hops[0]["matches"]
    assert any(m["public_key"] == KEY_REP for m in aa_matches)
    assert hops[1]["prefix"] == "bb"
    assert hops[1]["matches"] == []


def test_observed_paths_sorted_by_count():
    store = Store(":memory:")
    now = time.time()
    _pkt(store, ["aa", "cc"], now - 50, "p1")
    _pkt(store, ["aa", "bb"], now - 40, "p2")
    _pkt(store, ["aa", "bb"], now - 30, "p3")
    _pkt(store, ["aa", "bb"], now - 20, "p4")
    result = observed_paths(store, KEY_REP, days=7)
    assert result[0]["path"] == ["aa", "bb"]
    assert result[0]["count"] == 3


# ---- API tests --------------------------------------------------------------

def make(tmp_path, **kw):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, **kw)
    return TestClient(create_app(cfg))


def test_fleet_path_trace_404_unknown_rid(tmp_path):
    with make(tmp_path) as c:
        r = c.get("/api/fleet/999/path-trace")
        assert r.status_code == 404


def test_fleet_path_trace_returns_list(tmp_path):
    with make(tmp_path) as c:
        st = c.app.state.station
        rid = st.fleet.add(KEY_REP, "Rep A", "")
        r = c.get(f"/api/fleet/{rid}/path-trace")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


def test_fleet_path_trace_with_data(tmp_path):
    with make(tmp_path) as c:
        st = c.app.state.station
        rid = st.fleet.add(KEY_REP, "Rep A", "")
        now = time.time()
        _pkt(st.store, ["aa", "bb"], now - 60, "pt1")
        _pkt(st.store, ["aa", "bb"], now - 30, "pt2")
        r = c.get(f"/api/fleet/{rid}/path-trace")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["count"] == 2
        assert len(body[0]["hops"]) == 2

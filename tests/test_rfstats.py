"""Tests for per-node RF stats (RSSI/SNR since boot)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from notoryx_ops.config import Config
from notoryx_ops.rfstats import all_rf_stats, node_rf_stats
from notoryx_ops.server import create_app
from notoryx_ops.store import Store

KEY_A = "aa" * 32
KEY_B = "bb" * 32


def _pkt(store: Store, node_key: str, snr: float, rssi: float | None, ts: float, h: str) -> None:
    store.add_packet({
        "hash": h,
        "received_at": ts,
        "observer": "o",
        "source": node_key[:2],
        "route": "flood",
        "ptype": "advert",
        "hops": 0,
        "hash_size": 1,
        "path": [],
        "raw": "00",
        "node_key": node_key,
        "snr": snr,
        "rssi": rssi,
    })


# ---- unit tests for rfstats module ------------------------------------------

def test_node_rf_stats_returns_none_when_no_packets():
    store = Store(":memory:")
    assert node_rf_stats(store, KEY_A, since=0.0) is None


def test_node_rf_stats_basic():
    store = Store(":memory:")
    now = time.time()
    _pkt(store, KEY_A, snr=5.0, rssi=-80.0, ts=now - 10, h="p1")
    _pkt(store, KEY_A, snr=7.0, rssi=-70.0, ts=now - 5, h="p2")
    r = node_rf_stats(store, KEY_A, since=0.0)
    assert r is not None
    assert r["packet_count"] == 2
    assert r["snr"]["min"] == 5.0
    assert r["snr"]["max"] == 7.0
    assert r["snr"]["avg"] == 6.0
    assert r["snr"]["latest"] == 7.0
    assert r["rssi"]["min"] == -80.0
    assert r["rssi"]["max"] == -70.0
    assert r["rssi"]["avg"] == -75.0


def test_node_rf_stats_excludes_other_nodes():
    store = Store(":memory:")
    now = time.time()
    _pkt(store, KEY_A, snr=5.0, rssi=-80.0, ts=now, h="p1")
    _pkt(store, KEY_B, snr=9.0, rssi=-60.0, ts=now, h="p2")
    r = node_rf_stats(store, KEY_A, since=0.0)
    assert r is not None
    assert r["packet_count"] == 1
    assert r["snr"]["avg"] == 5.0


def test_node_rf_stats_since_filter():
    store = Store(":memory:")
    _pkt(store, KEY_A, snr=1.0, rssi=None, ts=100.0, h="old")
    _pkt(store, KEY_A, snr=9.0, rssi=None, ts=200.0, h="new")
    r = node_rf_stats(store, KEY_A, since=150.0)
    assert r is not None
    assert r["packet_count"] == 1
    assert r["snr"]["avg"] == 9.0


def test_node_rf_stats_no_rssi():
    store = Store(":memory:")
    _pkt(store, KEY_A, snr=4.0, rssi=None, ts=time.time(), h="p1")
    r = node_rf_stats(store, KEY_A, since=0.0)
    assert r is not None
    assert r["rssi"] is None
    assert r["snr"]["avg"] == 4.0


def test_all_rf_stats_empty():
    store = Store(":memory:")
    assert all_rf_stats(store, since=0.0) == []


def test_all_rf_stats_groups_by_node():
    store = Store(":memory:")
    now = time.time()
    _pkt(store, KEY_A, snr=5.0, rssi=-80.0, ts=now - 20, h="a1")
    _pkt(store, KEY_A, snr=7.0, rssi=-75.0, ts=now - 10, h="a2")
    _pkt(store, KEY_B, snr=9.0, rssi=-60.0, ts=now - 5, h="b1")
    result = all_rf_stats(store, since=0.0)
    assert len(result) == 2
    nk_map = {r["node_key"]: r for r in result}
    assert nk_map[KEY_A]["packet_count"] == 2
    assert nk_map[KEY_B]["packet_count"] == 1
    assert nk_map[KEY_B]["snr"]["avg"] == 9.0


def test_all_rf_stats_sorted_by_last_seen():
    store = Store(":memory:")
    _pkt(store, KEY_A, snr=5.0, rssi=None, ts=100.0, h="a1")
    _pkt(store, KEY_B, snr=9.0, rssi=None, ts=200.0, h="b1")
    result = all_rf_stats(store, since=0.0)
    assert result[0]["node_key"] == KEY_B


# ---- API tests --------------------------------------------------------------

def make(tmp_path, **kw):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, **kw)
    return TestClient(create_app(cfg))


def test_rf_stats_all_endpoint_empty(tmp_path):
    with make(tmp_path) as c:
        r = c.get("/api/rf-stats")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


def test_node_rf_stats_endpoint_404(tmp_path):
    with make(tmp_path) as c:
        r = c.get(f"/api/nodes/{KEY_A}/rf-stats")
        assert r.status_code == 404


def test_node_rf_stats_endpoint_400_bad_key(tmp_path):
    with make(tmp_path) as c:
        r = c.get("/api/nodes/zzzz/rf-stats")
        assert r.status_code == 400


def test_node_rf_stats_endpoint_returns_stats(tmp_path):
    store = Store(tmp_path / "t.db")
    _pkt(store, KEY_A, snr=6.0, rssi=-75.0, ts=1.0, h="p1")
    with make(tmp_path) as c:
        st = c.app.state.station
        st.store.add_packet({
            "hash": "rf1",
            "received_at": time.time(),
            "observer": "o",
            "source": KEY_A[:2],
            "route": "flood",
            "ptype": "advert",
            "hops": 0,
            "hash_size": 1,
            "path": [],
            "raw": "00",
            "node_key": KEY_A,
            "snr": 6.0,
            "rssi": -75.0,
        })
        r = c.get(f"/api/nodes/{KEY_A}/rf-stats")
        assert r.status_code == 200
        body = r.json()
        assert body["packet_count"] >= 1
        assert "snr" in body
        assert "rssi" in body

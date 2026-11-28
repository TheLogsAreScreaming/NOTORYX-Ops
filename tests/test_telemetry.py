"""Tests for sensor telemetry caching and API."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from notoryx_ops.config import Config
from notoryx_ops.server import _telemetry_summary, create_app
from notoryx_ops.store import Store


SAMPLE_LPP = [
    {"channel": 1, "type": "temperature", "value": 22.5},
    {"channel": 2, "type": "humidity", "value": 61.0},
    {"channel": 3, "type": "voltage", "value": 3.72},
    {"channel": 4, "type": "concentration", "value": 412.0},
]

KEY_PRE = "aabbccddeeff"
FULL_KEY = KEY_PRE + "00" * 26  # 64 hex chars


def make(tmp_path, **kw):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, **kw)
    return TestClient(create_app(cfg))


# ---- store layer ----------------------------------------------------------

def test_upsert_and_get_telemetry(tmp_path):
    store = Store(tmp_path / "t.db")
    now = time.time()
    store.upsert_telemetry(KEY_PRE, SAMPLE_LPP, now)
    r = store.get_telemetry(FULL_KEY)
    assert r is not None
    assert r["pubkey_pre"] == KEY_PRE
    assert r["lpp"] == SAMPLE_LPP
    assert r["received_at"] == pytest.approx(now)


def test_upsert_telemetry_overwrites(tmp_path):
    store = Store(tmp_path / "t.db")
    store.upsert_telemetry(KEY_PRE, SAMPLE_LPP, 1_000.0)
    new_lpp = [{"channel": 1, "type": "temperature", "value": 25.0}]
    store.upsert_telemetry(KEY_PRE, new_lpp, 2_000.0)
    r = store.get_telemetry(FULL_KEY)
    assert r["lpp"] == new_lpp
    assert r["received_at"] == pytest.approx(2_000.0)


def test_all_telemetry_returns_all_rows(tmp_path):
    store = Store(tmp_path / "t.db")
    store.upsert_telemetry("aabbccddeeff", SAMPLE_LPP, 1_000.0)
    store.upsert_telemetry("112233445566", [{"channel": 1, "type": "humidity", "value": 50.0}], 2_000.0)
    all_t = store.all_telemetry()
    assert len(all_t) == 2
    assert "aabbccddeeff" in all_t


def test_get_telemetry_returns_none_for_unknown(tmp_path):
    store = Store(tmp_path / "t.db")
    assert store.get_telemetry(FULL_KEY) is None


# ---- telemetry_summary helper -------------------------------------------

def test_telemetry_summary_extracts_known_types():
    s = _telemetry_summary(SAMPLE_LPP)
    assert s["temperature"] == 22.5
    assert s["humidity"] == 61.0
    assert s["voltage"] == 3.72
    assert s["concentration"] == 412.0


def test_telemetry_summary_ignores_unknown_types():
    lpp = [{"channel": 1, "type": "gyrometer", "value": {"acc_x": 0.1, "acc_y": 0.0, "acc_z": 9.8}}]
    assert _telemetry_summary(lpp) == {}


def test_telemetry_summary_handles_empty():
    assert _telemetry_summary([]) == {}
    assert _telemetry_summary(None) == {}


# ---- API layer -----------------------------------------------------------

def test_node_telemetry_endpoint(tmp_path):
    with make(tmp_path) as c:
        st = c.app.state.station
        st.ingest.handle_telemetry(KEY_PRE, SAMPLE_LPP)
        r = c.get(f"/api/nodes/{FULL_KEY}/telemetry")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pubkey_pre"] == KEY_PRE
        assert body["summary"]["temperature"] == 22.5


def test_node_telemetry_404_for_unknown(tmp_path):
    with make(tmp_path) as c:
        r = c.get(f"/api/nodes/{FULL_KEY}/telemetry")
        assert r.status_code == 404


def test_node_telemetry_400_for_bad_key(tmp_path):
    with make(tmp_path) as c:
        r = c.get("/api/nodes/zzzz/telemetry")
        assert r.status_code == 400


def test_nodes_list_includes_telemetry(tmp_path):
    with make(tmp_path) as c:
        st = c.app.state.station
        node_key = st.radio.net.nodes[0]["key"].hex()
        pre = node_key[:12]
        st.ingest.handle_telemetry(pre, SAMPLE_LPP)
        nodes = c.get("/api/nodes").json()
        matched = [n for n in nodes if n["public_key"] == node_key]
        assert matched, "demo node not in list"
        assert matched[0]["telemetry"]["temperature"] == 22.5


def test_ingest_handle_telemetry_publishes_event(tmp_path):
    store = Store(tmp_path / "t.db")
    from notoryx_ops.ingest import Hub, Ingest
    hub = Hub()
    ingest = Ingest(store, [], hub)
    q = hub.subscribe()
    ingest.handle_telemetry(KEY_PRE, SAMPLE_LPP)
    ev = q.get_nowait()
    assert ev["type"] == "telemetry"
    assert ev["pubkey_pre"] == KEY_PRE
    assert ev["lpp"] == SAMPLE_LPP

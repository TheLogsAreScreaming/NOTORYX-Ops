"""Tests for the who-repeats-me panel."""

from __future__ import annotations

import struct
import time

from fastapi.testclient import TestClient

from notoryx_ops import decoder
from notoryx_ops.config import Config
from notoryx_ops.server import create_app
from notoryx_ops.store import Store
from notoryx_ops.who_repeats import repeaters_panel

STATION_PRE = "aa"
KEY_A = "aa" * 32  # sender

RELAY_B = "bb"
RELAY_C = "cc"


def _store(tmp_path) -> Store:
    return Store(tmp_path / "w.db")


def _upsert_node(store: Store, key: str, name: str = "Node") -> None:
    store.upsert_node(key, name, "repeater", None, None, int(time.time()), time.time(), None, None)


def _add_packet(store: Store, node_key: str, path: list[str], received_at: float | None = None) -> None:
    received_at = received_at or time.time()
    app = decoder.encode_advert_app_data(2, "X", None, None)
    raw = decoder.encode_packet(1, 0x04, [bytes.fromhex(h) for h in path],
                                bytes.fromhex(node_key[:2] * 32) + struct.pack("<I", int(time.time()))
                                + bytes(64) + app[:1])
    pkt = decoder.decode(raw)
    store.add_packet({
        "hash": pkt.packet_hash + node_key[:4] + "".join(path),
        "received_at": received_at,
        "observer": "test",
        "source": "radio",
        "route": "flood",
        "ptype": "advert",
        "hops": len(path),
        "hash_size": 1,
        "path": path,
        "snr": None,
        "rssi": None,
        "raw": pkt.raw,
        "channel_hash": None,
        "src_hash": None,
        "dest_hash": None,
        "node_key": node_key,
        "summary": "test",
    })


# ---- unit tests for repeaters_panel ----------------------------------------

def test_no_station_prefix_returns_empty(tmp_path):
    store = _store(tmp_path)
    _add_packet(store, KEY_A, [STATION_PRE, RELAY_B])
    assert repeaters_panel(store, None) == []


def test_empty_store_returns_empty(tmp_path):
    store = _store(tmp_path)
    assert repeaters_panel(store, STATION_PRE) == []


def test_no_matching_paths_returns_empty(tmp_path):
    store = _store(tmp_path)
    _add_packet(store, KEY_A, [RELAY_B, RELAY_C])  # no station prefix
    assert repeaters_panel(store, STATION_PRE) == []


def test_single_relay_found(tmp_path):
    store = _store(tmp_path)
    _add_packet(store, KEY_A, [STATION_PRE, RELAY_B])
    result = repeaters_panel(store, STATION_PRE)
    assert len(result) == 1
    assert result[0]["prefix"] == RELAY_B
    assert result[0]["appearances"] == 1


def test_multiple_appearances_counted(tmp_path):
    store = _store(tmp_path)
    for _ in range(5):
        _add_packet(store, KEY_A, [STATION_PRE, RELAY_B])
    result = repeaters_panel(store, STATION_PRE)
    assert len(result) == 1
    assert result[0]["appearances"] == 5


def test_sorted_by_appearances_descending(tmp_path):
    store = _store(tmp_path)
    _add_packet(store, KEY_A, [STATION_PRE, RELAY_B])
    for _ in range(3):
        _add_packet(store, KEY_A, [STATION_PRE, RELAY_C])
    result = repeaters_panel(store, STATION_PRE)
    assert result[0]["prefix"] == RELAY_C  # 3 appearances
    assert result[1]["prefix"] == RELAY_B  # 1 appearance


def test_window_excludes_old_packets(tmp_path):
    store = _store(tmp_path)
    old = time.time() - 8 * 86400  # 8 days ago
    _add_packet(store, KEY_A, [STATION_PRE, RELAY_B], received_at=old)
    result = repeaters_panel(store, STATION_PRE, window_days=7)
    assert result == []


def test_own_prefix_not_in_results(tmp_path):
    store = _store(tmp_path)
    _add_packet(store, KEY_A, [STATION_PRE, RELAY_B, STATION_PRE])
    result = repeaters_panel(store, STATION_PRE)
    prefixes = {r["prefix"] for r in result}
    assert STATION_PRE not in prefixes


# ---- API tests --------------------------------------------------------------

def make(tmp_path, **kw):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, **kw)
    return TestClient(create_app(cfg))


def test_who_repeats_endpoint_returns_json(tmp_path):
    with make(tmp_path) as c:
        r = c.get("/api/reach/who-repeats-me")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "relayers" in body
        assert "window_days" in body
        assert "at" in body


def test_who_repeats_window_param(tmp_path):
    with make(tmp_path) as c:
        r = c.get("/api/reach/who-repeats-me?window_days=14")
        assert r.status_code == 200
        assert r.json()["window_days"] == 14


def test_who_repeats_relayer_has_node_field(tmp_path):
    with make(tmp_path) as c:
        body = c.get("/api/reach/who-repeats-me").json()
        for relayer in body["relayers"]:
            assert "prefix" in relayer
            assert "appearances" in relayer
            assert "node" in relayer

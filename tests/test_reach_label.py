"""Tests for the per-node reachability label."""

from __future__ import annotations

import struct
import time

from fastapi.testclient import TestClient

from notoryx_ops import decoder
from notoryx_ops.config import Config
from notoryx_ops.reach_label import fleet_reach, node_reach
from notoryx_ops.server import create_app
from notoryx_ops.store import Store

KEY_A = "aa" * 32
KEY_B = "bb" * 32
STATION_KEY = "cc" * 32  # station prefix = "cc"
STATION_PRE = "cc"


def _store(tmp_path) -> Store:
    return Store(tmp_path / "r.db")


def _upsert_node(store: Store, key: str) -> None:
    store.upsert_node(key, "Node", "repeater", None, None, int(time.time()), time.time(), None, None)


def _add_packet(store: Store, node_key: str, hops: int, path: list[str]) -> None:
    app = decoder.encode_advert_app_data(2, "X", None, None)
    raw = decoder.encode_packet(1, 0x04, [bytes.fromhex(h) for h in path],
                                bytes.fromhex(node_key[:2] * 32) + struct.pack("<I", int(time.time())) + bytes(64) + app[:1])
    pkt = decoder.decode(raw)
    store.add_packet({
        "hash": pkt.packet_hash + node_key[:4],  # make unique
        "received_at": time.time(),
        "observer": "test",
        "source": "radio",
        "route": "flood",
        "ptype": "advert",
        "hops": hops,
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


# ---- unit tests for reach_label -----------------------------------------

def test_unknown_node_returns_unknown(tmp_path):
    store = _store(tmp_path)
    assert node_reach(store, KEY_A, STATION_PRE) == "unknown"


def test_no_packets_gives_no_answer(tmp_path):
    store = _store(tmp_path)
    _upsert_node(store, KEY_A)
    assert node_reach(store, KEY_A, STATION_PRE) == "no_answer"


def test_direct_packet_gives_two_way(tmp_path):
    store = _store(tmp_path)
    _upsert_node(store, KEY_A)
    _add_packet(store, KEY_A, hops=0, path=[])
    assert node_reach(store, KEY_A, STATION_PRE) == "two_way"


def test_relay_only_gives_no_answer(tmp_path):
    store = _store(tmp_path)
    _upsert_node(store, KEY_A)
    _add_packet(store, KEY_A, hops=2, path=["11", "22"])
    assert node_reach(store, KEY_A, STATION_PRE) == "no_answer"


def test_station_prefix_in_path_gives_heard_me(tmp_path):
    store = _store(tmp_path)
    _upsert_node(store, KEY_A)
    _add_packet(store, KEY_A, hops=1, path=[STATION_PRE])
    assert node_reach(store, KEY_A, STATION_PRE) == "heard_me"


def test_no_station_prefix_skips_heard_me(tmp_path):
    store = _store(tmp_path)
    _upsert_node(store, KEY_A)
    _add_packet(store, KEY_A, hops=1, path=[STATION_PRE])
    assert node_reach(store, KEY_A, None) == "no_answer"


def test_fleet_reach_covers_all_nodes(tmp_path):
    store = _store(tmp_path)
    _upsert_node(store, KEY_A)
    _upsert_node(store, KEY_B)
    _add_packet(store, KEY_A, hops=0, path=[])
    labels = fleet_reach(store, STATION_PRE)
    assert labels[KEY_A] == "two_way"
    assert labels[KEY_B] == "no_answer"


# ---- API tests ----------------------------------------------------------

def make(tmp_path, **kw):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, **kw)
    return TestClient(create_app(cfg))


def test_nodes_list_includes_reach(tmp_path):
    with make(tmp_path) as c:
        nodes = c.get("/api/nodes").json()
        assert all("reach" in n for n in nodes)
        assert all(n["reach"] in ("two_way", "heard_me", "no_answer", "unknown") for n in nodes)


def test_node_reach_endpoint(tmp_path):
    with make(tmp_path) as c:
        # demo station has nodes; pick any
        nodes = c.get("/api/nodes").json()
        key = nodes[0]["public_key"]
        r = c.get(f"/api/nodes/{key}/reach")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["public_key"] == key
        assert body["reach"] in ("two_way", "heard_me", "no_answer", "unknown")


def test_node_reach_bad_key_returns_400(tmp_path):
    with make(tmp_path) as c:
        assert c.get("/api/nodes/zzzzzz/reach").status_code == 400

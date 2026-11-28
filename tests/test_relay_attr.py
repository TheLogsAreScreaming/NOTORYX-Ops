"""Tests for relay attribution on channel messages."""

from __future__ import annotations

import struct
import time

from fastapi.testclient import TestClient

from notoryx_ops import decoder
from notoryx_ops.config import Config
from notoryx_ops.relay_attr import _fmt_label, enrich_messages
from notoryx_ops.server import create_app
from notoryx_ops.store import Store

STATION_PRE = "aa"
SENDER_PRE = "bb"
RELAY_PRE = "cc"
SENDER_KEY = SENDER_PRE * 32


def _store(tmp_path) -> Store:
    return Store(tmp_path / "r.db")


def _add_message_and_packet(store: Store, path: list[str], hops: int,
                             sender: str = SENDER_KEY, received_at: float | None = None) -> str:
    received_at = received_at or time.time()
    app = decoder.encode_advert_app_data(2, "X", None, None)
    raw = decoder.encode_packet(1, 0x04, [bytes.fromhex(h) for h in path],
                                bytes.fromhex(sender[:2] * 32) + struct.pack("<I", int(time.time()))
                                + bytes(64) + app[:1])
    pkt = decoder.decode(raw)
    store.add_packet({
        "hash": pkt.packet_hash + sender[:4] + "".join(path),
        "received_at": received_at,
        "observer": "test",
        "source": "radio",
        "route": "flood",
        "ptype": "group_text",
        "hops": hops,
        "hash_size": 1,
        "path": path,
        "snr": None,
        "rssi": None,
        "raw": pkt.raw,
        "channel_hash": None,
        "src_hash": None,
        "dest_hash": None,
        "node_key": sender,
        "summary": "test",
    })
    phash = pkt.packet_hash + sender[:4] + "".join(path)
    store.add_message(phash, received_at, "public", sender[:4], "hello", None, hops)
    return phash


# ---- unit tests for _fmt_label ---------------------------------------------

def test_label_direct_received():
    assert _fmt_label(0, [], None, SENDER_PRE) == "direct"


def test_label_outgoing_zero_hops():
    assert _fmt_label(0, [], STATION_PRE, STATION_PRE) == "↻ 0"


def test_label_outgoing_relayed():
    assert _fmt_label(2, [RELAY_PRE, "dd"], STATION_PRE, STATION_PRE) == "↻ 2"


def test_label_received_relayed():
    label = _fmt_label(1, [RELAY_PRE], STATION_PRE, SENDER_PRE)
    assert label == f"via {RELAY_PRE}"


def test_label_received_multi_hop():
    label = _fmt_label(2, [RELAY_PRE, "dd"], STATION_PRE, SENDER_PRE)
    assert label == f"via {RELAY_PRE} → dd"


# ---- unit tests for enrich_messages ----------------------------------------

def test_enrich_adds_relay_field(tmp_path):
    store = _store(tmp_path)
    _add_message_and_packet(store, [RELAY_PRE], hops=1)
    msgs = store.q("SELECT * FROM messages")
    enriched = enrich_messages(store, msgs, STATION_PRE)
    assert all("relay" in m for m in enriched)


def test_enrich_path_resolved(tmp_path):
    store = _store(tmp_path)
    _add_message_and_packet(store, [RELAY_PRE], hops=1)
    msgs = store.q("SELECT * FROM messages")
    enriched = enrich_messages(store, msgs, STATION_PRE)
    assert enriched[0]["relay"]["path"] == [RELAY_PRE]
    assert enriched[0]["relay"]["hops"] == 1


def test_enrich_empty_list(tmp_path):
    store = _store(tmp_path)
    assert enrich_messages(store, [], STATION_PRE) == []


def test_enrich_missing_packet_gives_empty_path(tmp_path):
    store = _store(tmp_path)
    store.add_message("deadbeef", time.time(), "public", "aa", "hi", None, 0)
    msgs = store.q("SELECT * FROM messages")
    enriched = enrich_messages(store, msgs, STATION_PRE)
    assert enriched[0]["relay"]["path"] == []


# ---- API tests -------------------------------------------------------------

def make(tmp_path, **kw):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, **kw)
    return TestClient(create_app(cfg))


def test_messages_endpoint_has_relay_field(tmp_path):
    with make(tmp_path) as c:
        store = c.app.state.station.store
        _add_message_and_packet(store, [RELAY_PRE], hops=1)
        msgs = c.get("/api/messages").json()
        assert all("relay" in m for m in msgs)


def test_messages_relay_label_present(tmp_path):
    with make(tmp_path) as c:
        store = c.app.state.station.store
        _add_message_and_packet(store, [RELAY_PRE], hops=1)
        msgs = c.get("/api/messages").json()
        for m in msgs:
            assert "label" in m["relay"]

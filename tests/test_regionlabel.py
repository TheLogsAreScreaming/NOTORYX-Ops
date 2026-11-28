"""Tests for regionlabel — country annotation on channel messages."""
from __future__ import annotations

import time

import pytest

from notoryx_ops.regionlabel import annotate_messages
from notoryx_ops.store import Store


@pytest.fixture()
def store():
    return Store(":memory:")


def _add_node(store, public_key: str, lat: float | None, lon: float | None):
    now = time.time()
    store.db.execute(
        "INSERT OR REPLACE INTO nodes"
        "(public_key, prefix, name, node_type, lat, lon, first_seen, last_seen)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (public_key, public_key[:2].lower(), "node-" + public_key[:4], "CLIENT", lat, lon, now, now),
    )
    store.db.commit()


def _msg(sender: str, text: str = "hi") -> dict:
    return {"sender": sender, "text": text, "received_at": time.time()}


# ---------------------------------------------------------------------------
# basic annotation
# ---------------------------------------------------------------------------


def test_empty_list_returns_empty(store):
    assert annotate_messages(store, []) == []


def test_sender_with_known_location_gets_region(store):
    # Use a well-known location that reliably maps to a country
    _add_node(store, "aabbcc" + "0" * 58, lat=51.5, lon=-0.1)  # London, UK
    msgs = [_msg("aabb")]
    result = annotate_messages(store, msgs)
    assert result[0]["region"] is not None
    assert isinstance(result[0]["region"], str)
    assert len(result[0]["region"]) > 0


def test_sender_without_location_gets_none(store):
    # Node exists but has no lat/lon
    _add_node(store, "ccddee" + "0" * 58, lat=None, lon=None)
    msgs = [_msg("ccdd")]
    result = annotate_messages(store, msgs)
    assert result[0]["region"] is None


def test_sender_not_in_nodes_gets_none(store):
    msgs = [_msg("ffgg")]
    result = annotate_messages(store, msgs)
    assert result[0]["region"] is None


def test_empty_sender_gets_none(store):
    msgs = [{"sender": "", "text": "hi", "received_at": time.time()}]
    result = annotate_messages(store, msgs)
    assert result[0]["region"] is None


def test_none_sender_gets_none(store):
    msgs = [{"sender": None, "text": "hi", "received_at": time.time()}]
    result = annotate_messages(store, msgs)
    assert result[0]["region"] is None


def test_short_sender_gets_none(store):
    msgs = [_msg("a")]
    result = annotate_messages(store, msgs)
    assert result[0]["region"] is None


def test_region_cached_across_messages(store):
    _add_node(store, "aabbcc" + "0" * 58, lat=51.5, lon=-0.1)
    msgs = [_msg("aabb"), _msg("aabb"), _msg("aabb")]
    result = annotate_messages(store, msgs)
    # all three should have the same region
    regions = {r["region"] for r in result}
    assert len(regions) == 1


def test_multiple_senders(store):
    _add_node(store, "1122cc" + "0" * 58, lat=51.5, lon=-0.1)   # UK
    _add_node(store, "3344cc" + "0" * 58, lat=35.6, lon=139.7)  # Japan
    msgs = [_msg("1122"), _msg("3344"), _msg("5566")]
    result = annotate_messages(store, msgs)
    assert result[0]["region"] is not None
    assert result[1]["region"] is not None
    assert result[2]["region"] is None
    assert result[0]["region"] != result[1]["region"]


def test_messages_mutated_in_place(store):
    _add_node(store, "aabbcc" + "0" * 58, lat=51.5, lon=-0.1)
    orig = [_msg("aabb")]
    result = annotate_messages(store, orig)
    assert result is orig  # same list object returned


def test_region_field_added_to_all_messages(store):
    _add_node(store, "aabbcc" + "0" * 58, lat=51.5, lon=-0.1)
    msgs = [_msg("aabb"), _msg("zzzz")]
    result = annotate_messages(store, msgs)
    for r in result:
        assert "region" in r

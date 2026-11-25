import struct
import time

import pytest

from notoryx_ops import analytics, decoder
from notoryx_ops.alerts import Alerts, battery_falling, version_tuple
from notoryx_ops.channels import encrypt_group_text, hashtag_key, parse_channel
from notoryx_ops.config import Config
from notoryx_ops.fleet import Fleet, parse_get_reply, status_row
from notoryx_ops.ingest import Hub, Ingest
from notoryx_ops.mqtt_ingest import parse_message
from notoryx_ops.store import Store


def key(n: int) -> bytes:
    return bytes([n]) + bytes(31)


def advert(k: bytes, name: str, node_type=2, lat=44.0, lon=-72.0, path=()):
    app = decoder.encode_advert_app_data(node_type, name, lat, lon)
    return decoder.encode_packet(1, 0x04, list(path), k + struct.pack("<I", 1) + bytes(64) + app)


@pytest.fixture
def env(tmp_path):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path)
    store = Store(":memory:")
    hub = Hub()
    ing = Ingest(store, [parse_channel("public"), parse_channel("#testing")], hub)
    return cfg, store, hub, ing


def test_ingest_advert_creates_node_and_observer(env):
    _, store, _, ing = env
    ev = ing.handle(advert(key(0x4F), "Ridge"), "Obs A", "radio", snr=6.5, rssi=-80)
    assert ev["ptype"] == "advert" and ev["repeat"] is False
    n = store.nodes()[0]
    assert (n["name"], n["prefix"], n["node_type"], n["best_snr"]) == ("Ridge", "4f", "repeater", 6.5)
    assert n["direct_observers"] == ["Obs A"]
    assert store.one("SELECT packets FROM observers WHERE name='Obs A'")["packets"] == 1
    again = ing.handle(advert(key(0x4F), "Ridge", path=[b"\x01"]), "Obs B", "mqtt:x")
    assert again["repeat"] is True
    assert store.nodes()[0]["adverts"] == 2


def test_ingest_decrypts_channel_once(env):
    _, store, hub, ing = env
    q = hub.subscribe()
    raw = decoder.encode_packet(1, 0x05, [], encrypt_group_text(hashtag_key("testing"), 5, "Kit", "copy?"))
    ing.handle(raw, "Obs A", "radio")
    ing.handle(raw, "Obs B", "radio")
    msgs = store.q("SELECT * FROM messages")
    assert len(msgs) == 1 and msgs[0]["text"] == "copy?" and msgs[0]["channel"] == "#testing"
    events = [q.get_nowait()["type"] for _ in range(q.qsize())]
    assert events.count("message") == 1 and events.count("packet") == 2


def test_ingest_counts_bad_packets(env):
    _, store, _, ing = env
    assert ing.handle("zz", "Obs", "radio") is None
    assert ing.counts["bad"] == 1
    assert store.one("SELECT COUNT(*) n FROM packets")["n"] == 0


def test_mqtt_message_shapes():
    p = parse_message("meshcore/BOS/" + "ab" * 32 + "/packets",
                      b'{"origin":"Roof obs","SNR":"7.25","RSSI":"-91","raw":"1100"}')
    assert p == {"raw": "1100", "observer": "Roof obs", "observer_key": "ab" * 32, "snr": 7.25, "rssi": -91.0}
    assert parse_message("x/y", b"1100")["raw"] == "1100"
    assert parse_message("x/y", b'{"direction":"tx","raw":"1100"}') is None
    assert parse_message("x/y", b"not hex") is None
    assert parse_message("x/y", b'{"status":"online"}') is None


def test_prefix_usage_and_clash_alert(env):
    cfg, store, hub, ing = env
    ing.handle(advert(key(0x4F), "Ridge"), "Obs", "radio")
    ing.handle(advert(bytes([0x4F, 1]) + bytes(30), "Hill"), "Obs", "radio")
    ing.handle(advert(key(0x10), "Kit", node_type=1), "Obs", "radio")
    use = {p["prefix"]: p for p in analytics.prefix_usage(store)}
    assert use["4f"]["repeaters"] == 2 and use["10"]["others"] == 1
    assert analytics.overview(store)["prefix_clashes"] == 1
    Alerts(cfg, store, hub).evaluate()
    a = store.open_alert("prefix_clash", "4f")
    assert a and "Ridge" in a["message"] and "Hill" in a["message"]


def test_fleet_alerts(env):
    cfg, store, hub, ing = env
    cfg.expected_firmware = "1.11.0"
    fleet = Fleet(cfg, store, radio=None, alerts=Alerts(cfg, store, hub), hub=hub)
    rid = fleet.add(key(0x20).hex(), "Tower", "pw")
    now = time.time()
    store.add_status(rid, {"polled_at": now - 5 * 3600, "ok": 1, "battery_mv": 3500, "firmware": "v1.9.1"})
    fleet.alerts.evaluate(now)
    kinds = {a["kind"] for a in store.q("SELECT kind FROM alerts WHERE resolved_at IS NULL")}
    assert {"quiet", "low_battery", "firmware"} <= kinds
    store.add_status(rid, {"polled_at": now, "ok": 1, "battery_mv": 4100, "firmware": "v1.11.0"})
    fleet.alerts.evaluate(now)
    kinds = {a["kind"] for a in store.q("SELECT kind FROM alerts WHERE resolved_at IS NULL")}
    assert not kinds & {"quiet", "low_battery", "firmware"}
    # raising the same alert twice keeps one open copy
    assert store.raise_alert("x", "s", "info", "m") and store.raise_alert("x", "s", "info", "m") is None


def test_fleet_overview_hides_passwords(env):
    cfg, store, hub, _ = env
    fleet = Fleet(cfg, store, radio=None, alerts=Alerts(cfg, store, hub), hub=hub)
    fleet.add(key(0x21).hex(), "Roof", "secret")
    row = fleet.overview()[0]
    assert "password" not in row and row["has_password"] is True
    with pytest.raises(ValueError):
        fleet.add("abc", "Bad")


def test_bulk_setting_rules():
    Fleet.check_setting("flood.max", "8")
    for k, v in (("name", "x"), ("flood.max", "8; reboot"), ("repeat", "maybe")):
        with pytest.raises(ValueError):
            Fleet.check_setting(k, v)


def test_small_helpers():
    assert version_tuple("MeshCore v1.11.0 (Build: 2026)") == (1, 11, 0)
    assert version_tuple("garbage") is None
    assert parse_get_reply("> 12") == "12" and parse_get_reply("-> on") == "on"
    assert status_row({"bat": 4000, "noise_floor": -110})["battery_mv"] == 4000
    t = 1000.0
    assert battery_falling([(t, 4000), (t + 3600, 3900), (t + 7200, 3820)])
    assert not battery_falling([(t, 4000), (t + 3600, 3990), (t + 7200, 3995)])


def test_hex_grid_roundtrip():
    q, r = analytics.hex_of(44.26, -72.58, 1500, 44.3)
    poly = analytics.hex_polygon(q, r, 1500, 44.3)
    assert len(poly) == 7 and poly[0] == poly[-1]
    lons = [p[0] for p in poly]
    lats = [p[1] for p in poly]
    assert min(lons) < -72.58 < max(lons) and min(lats) < 44.26 < max(lats)


def test_coverage_and_links(env):
    _, store, _, ing = env
    ing.handle(advert(key(0x4F), "Ridge", lat=44.2, lon=-72.5), "Obs", "radio", snr=5)
    ing.handle(advert(key(0x31), "Hill", lat=44.3, lon=-72.6), "Obs", "radio", snr=-3)
    ing.handle(advert(key(0x55), "Far kit", node_type=1, lat=45.5, lon=-72.6), "Obs", "radio", snr=1)
    cov = analytics.coverage(store)
    assert len(cov["features"]) == 3
    assert [g["name"] for g in cov["gaps"]] == ["Far kit"]
    ing.handle(decoder.encode_packet(1, 0x02, [b"\x4f", b"\x31"], b"\x01\x02" + bytes(18)), "Obs", "radio")
    ls = analytics.links(store)
    assert len(ls) == 1 and ls[0]["count"] == 1


def test_heatmap_shape(env):
    _, store, _, ing = env
    ing.handle(advert(key(0x4F), "Ridge"), "Obs", "radio")
    grid = analytics.heatmap(store)
    assert len(grid) == 7 and all(len(r) == 24 for r in grid) and sum(map(sum, grid)) == 1


def test_prune_keeps_recent(env):
    _, store, _, ing = env
    ing.handle(advert(key(0x4F), "Old"), "Obs", "radio", received_at=time.time() - 30 * 86400)
    ing.handle(advert(key(0x50), "New"), "Obs", "radio")
    store.prune(14, 365)
    assert store.one("SELECT COUNT(*) n FROM packets")["n"] == 1


def test_shared_prefix_does_not_hide_a_quiet_repeater(env):
    cfg, store, hub, ing = env
    fleet = Fleet(cfg, store, radio=None, alerts=Alerts(cfg, store, hub), hub=hub)
    old = time.time() - 6 * 3600
    ing.handle(advert(key(0x4F), "Ridge"), "Obs", "radio", received_at=old)
    fleet.add(key(0x4F).hex(), "Ridge")
    # a direct message from some other node whose hash is also 4F, and a relay through "4F"
    ing.handle(decoder.encode_packet(2, 0x02, [b"\x4f"], b"\x01\x4f" + bytes(18)), "Obs", "radio")
    fleet.alerts.evaluate()
    assert store.open_alert("quiet", key(0x4F).hex())


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE nodes (public_key TEXT PRIMARY KEY, prefix TEXT NOT NULL, name TEXT, node_type TEXT,"
               " lat REAL, lon REAL, first_seen REAL NOT NULL, last_seen REAL NOT NULL, advert_ts INTEGER,"
               " adverts INTEGER NOT NULL DEFAULT 0, best_snr REAL, direct_observers TEXT NOT NULL DEFAULT '[]')")
    db.execute("INSERT INTO nodes VALUES ('ab', 'ab', 'x', 'repeater', NULL, NULL, 1, 5, 0, 1, NULL, '[]')")
    db.commit()
    db.close()
    st = Store(path)
    assert st.one("SELECT last_advert FROM nodes")["last_advert"] == 5


def test_store_rejects_and_clears_positions_off_the_globe(tmp_path):
    path = tmp_path / "ops.sqlite"
    s = Store(path)
    now = time.time()
    s.upsert_node("aa" * 32, "Bad", "unknown", 2019.17, -1981.93, now, now, None)
    assert s.nodes()[0]["lat"] is None
    # A row stored by an older version is cleaned on the next start.
    s.db.execute("UPDATE nodes SET lat = 2019.17, lon = -1981.93")
    s.upsert_node("bb" * 32, "Good", "repeater", 41.33, -73.09, now, now, None)
    s.db.close()
    s2 = Store(path)
    by = {n["name"]: n for n in s2.nodes()}
    assert by["Bad"]["lat"] is None and by["Bad"]["lon"] is None
    assert (by["Good"]["lat"], by["Good"]["lon"]) == (41.33, -73.09)


def test_watch_only_repeater_is_never_polled(tmp_path):
    import json
    import time

    from notoryx_ops import config
    from notoryx_ops.alerts import Alerts
    from notoryx_ops.fleet import Fleet
    from notoryx_ops.ingest import Hub
    from notoryx_ops.store import Store

    cfg = config.load(tmp_path / "config.toml")
    store = Store(tmp_path / "ops.sqlite")
    hub = Hub()
    fleet = Fleet(cfg, store, None, Alerts(cfg, store, hub), hub)
    key = "ab" + "0" * 62
    rid = fleet.add(key, "Not mine", password="secret", watch_only=True)
    rep = store.one("SELECT * FROM repeaters WHERE id = ?", (rid,))
    assert rep["watch_only"] == 1 and rep["password"] == ""  # a watched repeater never keeps a password
    now = time.time()
    for i, path in enumerate([["11", "ab", "22"], ["ab", "33"], ["44", "55"]]):
        store.x("INSERT INTO packets(hash, received_at, observer, source, route, ptype, hops, hash_size, path, raw)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)", (f"h{i}", now - 60, "o", "radio", "flood", "TXT", len(path), 1, json.dumps(path), ""))
    (ov,) = fleet.overview()
    assert ov["relay"]["packets_24h"] == 2
    assert {n["prefix"] for n in ov["relay"]["neighbours"]} == {"11", "22", "33"}

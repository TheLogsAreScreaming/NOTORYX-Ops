"""Stale nodes, the ignore list, and alert suppression for ignored nodes."""

import sqlite3
import struct
import time

import pytest
from fastapi.testclient import TestClient

from notoryx_ops import analytics, config, decoder, settings
from notoryx_ops.alerts import Alerts
from notoryx_ops.channels import parse_channel
from notoryx_ops.config import Config
from notoryx_ops.fleet import Fleet
from notoryx_ops.ingest import Hub, Ingest
from notoryx_ops.nodestate import STALE_CHOICES_HOURS, is_stale, known_time
from notoryx_ops.server import create_app
from notoryx_ops.store import SCHEMA_VERSION, Store


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
    ing = Ingest(store, [parse_channel("public")], hub)
    return cfg, store, hub, ing


# ---- stale rule ---------------------------------------------------------------

NOW = 1_763_000_000.0  # November 2025


def test_stale_rule():
    now = NOW
    assert is_stale(now - 25 * 3600, now, 24)
    assert not is_stale(now - 23 * 3600, now, 24)
    assert not is_stale(now - 24 * 3600, now, 24)  # exactly at the limit is still fresh
    assert is_stale(now - 3601, now, 1)


def test_never_heard_is_never_stale():
    assert not is_stale(None, NOW, 1)
    assert not is_stale(0, NOW, 1)


def test_off_means_never_stale():
    assert not is_stale(NOW - 30 * 86400, NOW, 0)


def test_epoch_time_is_unknown_not_stale():
    assert known_time(0, NOW) is None
    assert known_time(0.0, NOW) is None
    assert not is_stale(0, NOW, 1)
    assert not is_stale(1.0, NOW, 1)  # one second after the epoch


def test_pre_2020_time_is_unknown_not_stale():
    t = 1_546_300_800.0  # 2019-01-01
    assert known_time(t, NOW) is None
    assert not is_stale(t, NOW, 1)


def test_future_time_is_unknown_not_stale():
    t = NOW + 2 * 86400
    assert known_time(t, NOW) is None
    assert not is_stale(t, NOW, 1)
    assert known_time(NOW + 3600, NOW) == NOW + 3600  # a slightly fast clock is still believed


def test_normal_time_is_known():
    t = NOW - 3 * 86400
    assert known_time(t, NOW) == t
    assert is_stale(t, NOW, 24)
    assert not is_stale(t, NOW, 168)


def test_stale_setting_round_trip_and_validation(tmp_path):
    cfg = config.load(tmp_path / "config.toml")
    assert cfg.stale_after_hours == 24  # the default
    assert settings.public_view(cfg)["station"]["stale_after_hours"] == 24
    settings.apply_changes(cfg, {"station": {"stale_after_hours": 72}})
    settings.save(cfg)
    assert config.load(tmp_path / "config.toml").stale_after_hours == 72
    settings.apply_changes(cfg, {"station": {"stale_after_hours": 0}})
    assert cfg.stale_after_hours == 0
    with pytest.raises(ValueError):
        settings.apply_changes(cfg, {"station": {"stale_after_hours": 5}})
    assert set(settings.public_view(cfg)["stale_choices_hours"]) == set(STALE_CHOICES_HOURS)


def test_odd_value_in_file_falls_back_to_default(tmp_path):
    p = tmp_path / "config.toml"
    config.write_default(p)
    p.write_text(p.read_text().replace("stale_after_hours = 24", "stale_after_hours = 5"))
    assert config.load(p).stale_after_hours == 24


# ---- ignore store ---------------------------------------------------------------

def test_ignore_store(env):
    _, store, _, ing = env
    ing.handle(advert(key(0x4F), "Ridge"), "Obs", "radio")
    k = key(0x4F).hex()
    assert store.ignore(k) is True
    assert store.ignore(k.upper()) is False  # already there, case-insensitive
    assert store.is_ignored(k) and k in store.ignored_keys()
    rows = store.ignored()
    assert rows[0]["public_key"] == k and rows[0]["name"] == "Ridge"
    assert store.nodes() == [] and len(store.nodes(include_ignored=True)) == 1
    assert store.unignore(k) is True and store.unignore(k) is False
    assert not store.is_ignored(k) and len(store.nodes()) == 1


def test_ignore_list_survives_reopen(tmp_path):
    path = tmp_path / "ops.sqlite"
    Store(path).ignore("ab" * 32, "Noisy")
    again = Store(path)
    assert again.is_ignored("ab" * 32) and again.ignored()[0]["name"] == "Noisy"


def test_ignored_packets_are_stored_but_hidden(env):
    _, store, hub, ing = env
    q = hub.subscribe()
    store.ignore(key(0x4F).hex())
    ev = ing.handle(advert(key(0x4F), "Ridge"), "Obs", "radio")
    assert ev["ignored"] is True and q.empty()  # not sent to the live map or feed
    assert store.one("SELECT COUNT(*) n FROM packets")["n"] == 1  # still stored
    assert store.recent_packets() == []
    assert len(store.recent_packets(hide_ignored=False)) == 1
    ing.handle(advert(key(0x10), "Kit", node_type=1), "Obs", "radio")
    assert not q.empty()


def test_analytics_leave_out_ignored(env):
    _, store, _, ing = env
    ing.handle(advert(key(0x4F), "Ridge"), "Obs", "radio")
    ing.handle(advert(bytes([0x4F, 1]) + bytes(30), "Hill"), "Obs", "radio")
    ing.handle(advert(key(0x10), "Kit", node_type=1), "Obs", "radio")
    assert analytics.overview(store)["nodes_24h"] == 3
    store.ignore(key(0x4F).hex())
    ov = analytics.overview(store)
    assert ov["nodes_24h"] == 2 and ov["prefix_clashes"] == 0 and ov["packets_24h"] == 2
    keys = {a["public_key"] for a in analytics.top_nodes(store)["adverts"]}
    assert key(0x4F).hex() not in keys
    assert sum(sum(r) for r in analytics.heatmap(store)) == 2
    assert all(f["properties"]["samples"] <= 2 for f in analytics.coverage(store)["features"])


def test_schema_upgrade_from_version_1(tmp_path):
    path = tmp_path / "v1.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    db.commit()
    db.close()
    st = Store(path)
    assert st.schema_version() == SCHEMA_VERSION
    assert st.ignore("cd" * 32)
    assert Store(":memory:").schema_version() == SCHEMA_VERSION


# ---- alerts ---------------------------------------------------------------------

def test_ignored_repeater_raises_no_alerts(env):
    cfg, store, hub, _ = env
    cfg.expected_firmware = "1.11.0"
    alerts = Alerts(cfg, store, hub)
    fleet = Fleet(cfg, store, radio=None, alerts=alerts, hub=hub)
    rid = fleet.add(key(0x20).hex(), "Tower", "pw")
    now = time.time()
    store.add_status(rid, {"polled_at": now - 5 * 3600, "ok": 1, "battery_mv": 3500, "firmware": "v1.9.1"})
    store.ignore(key(0x20).hex())
    alerts.evaluate(now)
    assert store.q("SELECT * FROM alerts") == []


def test_ignoring_clears_open_alerts(env):
    cfg, store, hub, _ = env
    alerts = Alerts(cfg, store, hub)
    fleet = Fleet(cfg, store, radio=None, alerts=alerts, hub=hub)
    rid = fleet.add(key(0x20).hex(), "Tower", "pw")
    store.add_status(rid, {"polled_at": time.time() - 5 * 3600, "ok": 1, "battery_mv": 3500})
    alerts.evaluate()
    assert store.open_alert("quiet", key(0x20).hex())
    store.ignore(key(0x20).hex())
    assert alerts.clear_for_node(key(0x20).hex()) == 2
    assert store.q("SELECT * FROM alerts WHERE resolved_at IS NULL") == []
    alerts.evaluate()
    assert store.q("SELECT * FROM alerts WHERE resolved_at IS NULL") == []


def test_ignored_repeater_leaves_prefix_clash_check(env):
    cfg, store, hub, ing = env
    ing.handle(advert(key(0x4F), "Ridge"), "Obs", "radio")
    ing.handle(advert(bytes([0x4F, 1]) + bytes(30), "Hill"), "Obs", "radio")
    store.ignore((bytes([0x4F, 1]) + bytes(30)).hex())
    Alerts(cfg, store, hub).evaluate()
    assert store.open_alert("prefix_clash", "4f") is None


def test_ignored_observer_raises_no_quiet_alert(env):
    cfg, store, hub, _ = env
    old = time.time() - 3600
    store.touch_observer("Far", "mqtt:x", old, public_key="ef" * 32)
    store.x("UPDATE observers SET first_seen = ?", (old - 3600,))
    store.ignore("ef" * 32)
    Alerts(cfg, store, hub).evaluate()
    assert store.open_alert("observer_quiet", "Far") is None


# ---- API ------------------------------------------------------------------------

def make(tmp_path, **kw):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, **kw)
    return TestClient(create_app(cfg))


def test_ignore_api(tmp_path):
    with make(tmp_path) as c:
        nodes = c.get("/api/nodes").json()
        assert all("stale" in n and n["ignored"] is False for n in nodes)
        n = nodes[0]
        k = n["public_key"]
        assert c.post("/api/ignored", json={"public_key": k}).json()["added"] is True
        assert c.post("/api/ignored", json={"public_key": k}).json()["added"] is False
        assert k not in {x["public_key"] for x in c.get("/api/nodes").json()}
        every = {x["public_key"]: x for x in c.get("/api/nodes?include_ignored=true").json()}
        assert every[k]["ignored"] is True
        listed = c.get("/api/ignored").json()
        assert listed[0]["public_key"] == k and listed[0]["name"] == n["name"]
        assert all(p["node_key"] != k for p in c.get("/api/packets?limit=2000").json())
        assert all(r["node_key"] != k for r in c.get("/api/replay?hours=24").json())
        assert c.delete(f"/api/ignored/{k}").json()["ok"]
        assert c.delete(f"/api/ignored/{k}").status_code == 404
        assert c.delete("/api/ignored/xyz").status_code == 400
        assert c.post("/api/ignored", json={"public_key": "nothex"}).status_code == 422
        assert k in {x["public_key"] for x in c.get("/api/nodes").json()}


def test_ignore_api_needs_admin_token(tmp_path):
    with make(tmp_path, admin_token="s3cret-token") as c:
        k = "ab" * 32
        assert c.post("/api/ignored", json={"public_key": k}).status_code == 401
        auth = {"Authorization": "Bearer s3cret-token"}
        assert c.post("/api/ignored", json={"public_key": k}, headers=auth).status_code == 200
        assert c.delete(f"/api/ignored/{k}").status_code == 401
        assert c.delete(f"/api/ignored/{k}", headers=auth).status_code == 200
        assert c.get("/api/ignored").status_code == 200  # reading never needs it


def test_stale_flag_follows_setting(tmp_path):
    with make(tmp_path) as c:
        st = c.app.state.station
        st.store.x("UPDATE nodes SET last_seen = ? WHERE rowid = (SELECT MIN(rowid) FROM nodes)",
                   (time.time() - 2 * 86400,))
        assert any(n["stale"] for n in c.get("/api/nodes").json())
        assert c.get("/api/station").json()["stale_after_hours"] == 24
        assert c.put("/api/settings", json={"station": {"stale_after_hours": 168}}).status_code == 200
        assert not any(n["stale"] for n in c.get("/api/nodes").json())
        assert c.put("/api/settings", json={"station": {"stale_after_hours": 2}}).status_code == 400
        assert all("stale" in o for o in c.get("/api/observers").json())


def test_epoch_last_seen_is_not_flagged_stale(tmp_path):
    with make(tmp_path) as c:
        st = c.app.state.station
        st.store.x("UPDATE nodes SET last_seen = 0")
        assert not any(n["stale"] for n in c.get("/api/nodes").json())

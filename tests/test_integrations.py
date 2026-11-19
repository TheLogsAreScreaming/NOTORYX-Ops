import json
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from notoryx_ops import config, integrations, settings
from notoryx_ops.auth import check_password, hash_password
from notoryx_ops.config import Config
from notoryx_ops.server import create_app
from notoryx_ops.store import Store


def make(tmp_path, **kw):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, **kw)
    return cfg, TestClient(create_app(cfg))


# ---- accounts ---------------------------------------------------------------

def test_password_hashing():
    h = hash_password("correct horse battery")
    assert h.startswith("pbkdf2_sha256$") and check_password("correct horse battery", h)
    assert not check_password("wrong", h) and not check_password("x", "garbage")


def test_accounts_lock_the_dashboard_and_roles_apply(tmp_path):
    _cfg, c = make(tmp_path)
    with c:
        assert c.get("/api/auth/me").json()["accounts_on"] is False
        assert c.get("/api/nodes").status_code == 200
        # First admin: allowed from the station computer while there are no accounts.
        r = c.post("/api/auth/setup", json={"username": "boss", "password": "long enough pw"})
        assert r.status_code == 200 and "notoryx_ops_session" in r.cookies
        assert c.post("/api/auth/setup", json={"username": "again", "password": "long enough pw"}).status_code == 409
        me = c.get("/api/auth/me").json()
        assert me["accounts_on"] and me["user"]["role"] == "admin" and me["is_admin"]
        assert c.post("/api/users", json={"username": "watcher", "password": "watcher pass 1", "role": "viewer"}).status_code == 200
        assert c.post("/api/users", json={"username": "op", "password": "operator pass", "role": "operator"}).status_code == 200
        assert c.post("/api/users", json={"username": "bad name!", "password": "operator pass"}).status_code == 400
        users = c.get("/api/users").json()
        assert {u["username"] for u in users} == {"boss", "watcher", "op"} and all("pw_hash" not in u for u in users)

        c.post("/api/auth/logout")
        c.cookies.clear()
        assert c.get("/api/nodes").status_code == 401
        assert c.get("/metrics").status_code == 401
        assert c.get("/api/auth/me").status_code == 200  # sign-in stays reachable

        assert c.post("/api/auth/login", json={"username": "watcher", "password": "nope"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "watcher", "password": "watcher pass 1"}).status_code == 200
        assert c.get("/api/nodes").status_code == 200
        assert c.post("/api/fleet", json={"public_key": "ab" * 32, "name": "X"}).status_code == 403
        assert c.put("/api/settings", json={}).status_code == 403

        c.cookies.clear()
        c.post("/api/auth/login", json={"username": "op", "password": "operator pass"})
        assert c.post("/api/fleet", json={"public_key": "ab" * 32, "name": "X"}).status_code == 200
        assert c.put("/api/settings", json={}).status_code == 403
        assert c.get("/api/users").status_code == 403

        c.cookies.clear()
        c.post("/api/auth/login", json={"username": "boss", "password": "long enough pw"})
        boss = next(u for u in c.get("/api/users").json() if u["username"] == "boss")
        assert c.patch(f"/api/users/{boss['id']}", json={"role": "viewer"}).status_code == 400  # last admin
        assert c.delete(f"/api/users/{boss['id']}").status_code == 400
        op = next(u for u in c.get("/api/users").json() if u["username"] == "op")
        assert c.patch(f"/api/users/{op['id']}", json={"disabled": True}).status_code == 200
        c.cookies.clear()
        assert c.post("/api/auth/login", json={"username": "op", "password": "operator pass"}).status_code == 401


def test_admin_token_still_works_with_accounts(tmp_path):
    _cfg, c = make(tmp_path, admin_token="token-for-scripts")
    with c:
        hdr = {"Authorization": "Bearer token-for-scripts"}
        assert c.post("/api/auth/setup", json={"username": "boss", "password": "long enough pw"}).status_code == 401
        assert c.post("/api/auth/setup", json={"username": "boss", "password": "long enough pw"}, headers=hdr).status_code == 200
        c.cookies.clear()
        assert c.get("/api/nodes").status_code == 401
        assert c.get("/api/nodes", headers=hdr).status_code == 200
        assert c.get("/api/users", headers=hdr).status_code == 200


def test_sign_in_rate_limit(tmp_path):
    _cfg, c = make(tmp_path)
    with c:
        c.post("/api/auth/setup", json={"username": "boss", "password": "long enough pw"})
        codes = [c.post("/api/auth/login", json={"username": "boss", "password": "wrong"}).status_code for _ in range(9)]
        assert codes[:8] == [401] * 8 and codes[8] == 429


def test_websocket_needs_sign_in_when_accounts_on(tmp_path):
    from starlette.websockets import WebSocketDisconnect

    _cfg, c = make(tmp_path)
    with c:
        c.post("/api/auth/setup", json={"username": "boss", "password": "long enough pw"})
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"]
        c.cookies.clear()
        with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ws") as ws:
            ws.receive_json()


# ---- exports, metrics, public map ------------------------------------------------

def test_exports(tmp_path):
    _cfg, c = make(tmp_path)
    with c:
        r = c.get("/api/export/nodes.geojson")
        assert r.headers["content-type"].startswith("application/geo+json") and "attachment" in r.headers["content-disposition"]
        fc = r.json()
        assert fc["features"] and all(f["geometry"]["type"] == "Point" for f in fc["features"])
        assert c.get("/api/export/links.geojson").json()["type"] == "FeatureCollection"
        gpx = ET.fromstring(c.get("/api/export/nodes.gpx").text)
        wpts = gpx.findall("{http://www.topografix.com/GPX/1/1}wpt")
        assert len(wpts) == len(fc["features"]) and wpts[0].get("lat")


def test_metrics_format(tmp_path):
    _cfg, c = make(tmp_path)
    with c:
        text = c.get("/metrics").text
        assert "# TYPE notoryx_ops_up gauge" in text and "notoryx_ops_up 1" in text
        assert 'notoryx_ops_packets_by_type_24h{type="advert"}' in text
        assert text.count("# HELP notoryx_ops_packets_by_type_24h ") == 1


def test_metrics_token_with_accounts(tmp_path):
    _cfg, c = make(tmp_path, metrics_token="scrape-token-123")
    with c:
        c.post("/api/auth/setup", json={"username": "boss", "password": "long enough pw"})
        c.cookies.clear()
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer scrape-token-123"}).status_code == 200
        assert c.get("/api/nodes", headers={"Authorization": "Bearer scrape-token-123"}).status_code == 401


def test_public_map_rounds_positions_and_hides_companions(tmp_path):
    cfg, c = make(tmp_path)
    with c:
        assert c.get("/api/public/map").status_code == 404
        cfg.public_map = True
        c.post("/api/auth/setup", json={"username": "boss", "password": "long enough pw"})
        c.cookies.clear()
        pub = c.get("/api/public/map").json()  # open without sign-in
        assert pub["nodes"] and {n["type"] for n in pub["nodes"]} <= {"repeater", "room"}
        assert all(round(n["lat"], 2) == n["lat"] for n in pub["nodes"])
        assert all(len(n["id"]) == 8 for n in pub["nodes"])
        cfg.public_show_companions = True
        assert len(c.get("/api/public/map").json()["nodes"]) >= len(pub["nodes"])


# ---- Home Assistant ----------------------------------------------------------------

class FakeMqtt:
    def __init__(self):
        self.sent = []

    def publish(self, topic, payload, retain=False):
        self.sent.append((topic, payload, retain))


def test_home_assistant_discovery(tmp_path):
    # Turned off so the background task never tries a real broker during the test.
    cfg, c = make(tmp_path, ha_enabled=False, ha_host="broker.local")
    with c:
        st = c.app.state.station
        st.ha.client = FakeMqtt()
        n = st.ha.publish_once()
        topics = {t for t, _, _ in st.ha.client.sent}
        node = st.ha.node_id
        assert f"homeassistant/binary_sensor/{node}/radio/config" in topics
        assert f"homeassistant/sensor/{node}/unique_24h/config" in topics
        assert any("/rep_" in t and t.endswith("_battery/config") for t in topics)
        conf = json.loads(next(p for t, p, _ in st.ha.client.sent if t.endswith("/unique_24h/config")))
        assert conf["unique_id"] == f"{node}_unique_24h" and conf["device"]["identifiers"] == [node]
        assert n >= 6
        before = len(st.ha.client.sent)
        st.ha.publish_once()
        configs = [t for t, _, _ in st.ha.client.sent[before:] if t.endswith("/config")]
        assert not configs  # discovery is sent once per connection
        st.ha.client = None
        with pytest.raises(RuntimeError):
            st.ha.publish_once()


# ---- PostgreSQL ------------------------------------------------------------------------

class FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql):
        self.log.append(("ddl", sql))

    def executemany(self, sql, rows):
        self.log.append(("rows", sql, list(rows)))


class FakeConn:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        self.log.append(("commit",))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_postgres_archive_copies_new_rows_once(tmp_path):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path)
    store = Store(":memory:")
    store.add_packet({"hash": "aa", "received_at": 1.0, "observer": "o", "source": "radio", "route": "flood",
                      "ptype": "advert", "hops": 0, "hash_size": 1, "path": [], "raw": "00"})
    store.ins("INSERT INTO alerts(created_at, kind, subject, severity, message) VALUES (1,'k','s','warning','m')")
    log = []
    arc = integrations.PostgresArchive(cfg, store, connect=lambda: FakeConn(log))
    assert arc.run_once() == 2
    inserted = [e for e in log if e[0] == "rows"]
    assert any("ops_packets" in e[1] and "ON CONFLICT (id)" in e[1] for e in inserted)
    assert any(e[0] == "ddl" and "CREATE TABLE IF NOT EXISTS ops_packets" in e[1] for e in log)
    log.clear()
    assert arc.run_once() == 0
    store.add_packet({"hash": "bb", "received_at": 2.0, "observer": "o", "source": "radio", "route": "flood",
                      "ptype": "advert", "hops": 0, "hash_size": 1, "path": [], "raw": "00"})
    assert arc.run_once() == 1


def test_integration_settings_round_trip_and_secrets(tmp_path):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path)
    settings.apply_changes(cfg, {
        "integrations": {"public_map": True, "public_precision": 3, "metrics_token": "scrape-token-123"},
        "homeassistant": {"enabled": True, "host": "ha.local", "password": "pw"},
        "postgres": {"enabled": True, "dsn": "postgresql://u:p@db/ops"},
    })
    settings.save(cfg)
    back = config.load(cfg.path)
    assert back.public_map and back.public_precision == 3 and back.ha_host == "ha.local" and back.pg_dsn.endswith("/ops")
    view = settings.public_view(back)
    text = json.dumps(view)
    assert "scrape-token-123" not in text and "postgresql://" not in text and '"pw"' not in text
    assert view["postgres"]["dsn_set"] and view["homeassistant"]["password_set"]
    with pytest.raises(ValueError):
        settings.apply_changes(cfg, {"postgres": {"dsn": "mysql://x"}})
    with pytest.raises(ValueError):
        settings.apply_changes(cfg, {"homeassistant": {"enabled": True, "host": ""}})

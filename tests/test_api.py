from fastapi.testclient import TestClient

from notoryx_ops.config import Config
from notoryx_ops.server import create_app


def make(tmp_path, **kw):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, **kw)
    return TestClient(create_app(cfg))


def test_demo_station_serves_data(tmp_path):
    with make(tmp_path) as c:
        info = c.get("/api/station").json()
        assert info["demo"] is True and info["radio"]["state"] == "connected"
        assert c.get("/api/overview").json()["packets_24h"] > 100
        pk = c.get("/api/packets?limit=5").json()
        assert len(pk) == 5
        detail = c.get(f"/api/packets/{pk[0]['hash']}").json()
        assert detail["receptions"] and detail["decoded"]["payload_type"]
        assert len(c.get("/api/nodes").json()) >= 10
        assert c.get("/api/messages").json()
        assert c.get("/api/channels").json()["channels"][0]["name"] == "Public"
        assert len(c.get("/api/observers").json()) == 3
        assert len(c.get("/api/analytics/prefixes").json()) == 256
        assert c.get("/api/coverage").json()["features"]
        assert c.get("/api/replay?hours=1").status_code == 200
        assert len(c.get("/api/analytics/heatmap").json()) == 7
        fleet = c.get("/api/fleet").json()
        assert len(fleet) == 4 and all("password" not in r for r in fleet)


def test_decode_endpoint(tmp_path):
    with make(tmp_path) as c:
        assert c.post("/api/decode", json={"raw": "11017f00000000"}).json()["decoded"]["path"] == ["7f"]
        assert c.post("/api/decode", json={"raw": "zz"}).status_code == 400


def test_fleet_changes_and_bulk(tmp_path):
    with make(tmp_path) as c:
        r = c.post("/api/fleet", json={"public_key": "ab" * 32, "name": "Mine", "password": "x"})
        assert r.status_code == 200
        rid = r.json()["id"]
        assert c.post("/api/fleet", json={"public_key": "ab" * 32, "name": "Again"}).status_code == 409
        assert c.patch(f"/api/fleet/{rid}", json={"enabled": False}).json()["ok"]
        assert c.delete(f"/api/fleet/{rid}").json()["ok"]
        ids = [f["id"] for f in c.get("/api/fleet").json() if "Ridge" not in f["name"]][:2]
        prev = c.post("/api/fleet/bulk/preview", json={"ids": ids, "key": "flood.max", "value": "6"}).json()
        assert all(p.get("current") == "12" and p["changes"] for p in prev)
        assert c.post("/api/fleet/bulk/apply", json={"ids": ids, "key": "flood.max", "value": "6"}).status_code == 400
        done = c.post("/api/fleet/bulk/apply", json={"ids": ids, "key": "flood.max", "value": "6", "confirm": True}).json()
        assert all(d["ok"] for d in done)
        assert c.post("/api/fleet/bulk/preview", json={"ids": ids, "key": "name", "value": "x"}).status_code == 400


def test_admin_token_required_when_set(tmp_path):
    with make(tmp_path, admin_token="s3cret") as c:
        body = {"public_key": "cd" * 32, "name": "Mine"}
        assert c.post("/api/fleet", json=body).status_code == 401
        ok = c.post("/api/fleet", json=body, headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200
        assert c.get("/api/fleet").status_code == 200  # reading never needs it


def test_poll_and_alerts(tmp_path):
    with make(tmp_path) as c:
        assert c.post("/api/fleet/poll").json()["started"] is True
        assert c.post("/api/alerts/check").json()["ok"]
        alerts = c.get("/api/alerts?open=true").json()
        assert any(a["kind"] == "prefix_clash" for a in alerts)


def test_websocket_gets_live_events(tmp_path):
    with make(tmp_path) as c, c.websocket_connect("/ws") as ws:
        ev = ws.receive_json()
        assert ev["type"] in ("packet", "message", "ping", "fleet", "fleet_status", "alert")


def test_pages_are_never_served_stale(tmp_path):
    from fastapi.testclient import TestClient

    from notoryx_ops import config
    from notoryx_ops.server import create_app

    cfg = config.load(tmp_path / "config.toml")
    cfg.demo = True
    with TestClient(create_app(cfg)) as c:
        page = c.get("/settings")
        assert page.headers.get("cache-control") == "no-cache"
        assert "cache-control" not in c.get("/api/station").headers


def test_send_channel_message(tmp_path):
    from fastapi.testclient import TestClient

    from notoryx_ops import config
    from notoryx_ops.server import create_app

    cfg = config.load(tmp_path / "config.toml")
    cfg.demo = True
    with TestClient(create_app(cfg)) as c:
        r = c.post("/api/channels/send", json={"channel": "Public", "text": "hello  mesh"})
        assert r.status_code == 200, r.text
        assert c.post("/api/channels/send", json={"channel": "Public", "text": "again"}).status_code == 429
        assert c.post("/api/channels/send", json={"channel": "Public", "text": "x" * 141}).status_code == 400
        assert c.post("/api/channels/send", json={"channel": "#nope", "text": "hi"}).status_code == 404

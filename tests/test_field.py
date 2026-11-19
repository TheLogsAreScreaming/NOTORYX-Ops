import time

import pytest
from fastapi.testclient import TestClient

from notoryx_ops import field
from notoryx_ops.config import Config
from notoryx_ops.server import create_app
from notoryx_ops.store import Store


def make(tmp_path, **kw):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, **kw)
    return cfg, TestClient(create_app(cfg))


def test_report_text_round_trip():
    t = field.format_report("flood", 44.260123, -72.575, "Water  over the road")
    assert t == "⚑ flood 44.26012,-72.57500 Water over the road"
    r = field.parse_report(t)
    assert r == {"clear": False, "kind": "flood", "lat": 44.26012, "lon": -72.575, "note": "Water over the road"}
    c = field.parse_report(field.format_report("flood", 44.26, -72.57, clear=True))
    assert c["clear"] and c["kind"] == "flood"
    assert field.parse_report("⚑ dragons 44.2,-72.5") is None
    assert field.parse_report("just chatting about a flood 44.2,-72.5") is None
    assert field.parse_report("⚑ tree 95.0,-72.5") is None
    with pytest.raises(ValueError):
        field.format_report("tree", 44.2, -72.5, "x" * 81)
    assert field.parse_position("⚑ pos 44.1,-72.2") == (44.1, -72.2)


def test_incoming_reports_dedupe_and_clear():
    f = field.Field(Store(":memory:"))
    now = time.time()
    assert f.on_message("Public", "Alice", "⚑ tree 44.20000,-72.50000 Big oak", now) == "report"
    assert f.on_message("Public", "Alice", "⚑ tree 44.20000,-72.50000 Big oak", now + 5) is None  # heard again
    assert len(f.reports()) == 1
    assert f.on_message("Public", "Bob", "⚑ clear tree 44.20050,-72.50050", now + 60) == "clear"  # within 150 m
    assert f.reports()[0]["cleared_by"] == "Bob"
    assert f.on_message("Public", "Bob", "⚑ clear tree 44.3,-72.5", now + 70) is None


def test_tracks_from_adverts_and_messages():
    s = Store(":memory:")
    f = field.Field(s)
    key = "ab" * 32
    s.upsert_node(key, "Walker", "companion", 44.1, -72.1, 1, time.time(), None)
    now = time.time()
    f.on_advert(key, 44.1, -72.1, now - 300)
    f.on_advert(key, 44.10001, -72.1, now - 200)  # moved under 25 m: no new point
    f.on_advert(key, 44.11, -72.1, now - 100)
    f.on_message("team-x", "Walker", "⚑ pos 44.12,-72.1", now)
    tr = f.tracks(1)
    assert len(tr) == 1 and len(tr[0]["points"]) == 3 and tr[0]["name"] == "Walker"


def test_survey_add_and_import():
    f = field.Field(Store(":memory:"))
    res = f.add_surveys([
        {"client_id": "a1", "lat": 44.2, "lon": -72.5, "heard": True, "snr": 4.5},
        {"client_id": "a1", "lat": 44.2, "lon": -72.5},  # duplicate upload
        {"lat": "bad", "lon": 1},
        {"lat": 44.3, "lon": -72.4, "heard": "no"},
    ], "crew")
    assert res == {"added": 2, "skipped": 2}
    csv_pts = field.parse_survey_csv("Latitude,Longitude,SNR,Time,Note\n44.1,-72.1,-3.5,2026-11-01T10:00:00Z,ridge\n,,,,\n")
    assert len(csv_pts) == 1 and csv_pts[0]["snr"] == "-3.5" and csv_pts[0]["taken_at"] > 1.7e9
    gpx = ('<gpx xmlns="http://www.topografix.com/GPX/1/1"><wpt lat="44.1" lon="-72.1"><time>2026-11-01T10:00:00Z</time>'
           "<name>snr=6.25 summit</name></wpt><trk><trkseg><trkpt lat=\"44.2\" lon=\"-72.2\"/></trkseg></trk></gpx>")
    gp = field.parse_survey_gpx(gpx)
    assert len(gp) == 2 and gp[0]["snr"] == "6.25"
    with pytest.raises(ValueError):
        field.parse_survey_gpx("<gpx")


def test_field_api_reports_over_the_demo_radio(tmp_path):
    _cfg, c = make(tmp_path)
    with c:
        assert len(c.get("/api/field/reports").json()) == 2  # demo seeds two
        r = c.post("/api/field/reports", json={"kind": "power", "lat": 44.25, "lon": -72.6, "note": "Line on the road", "channel": "Public"})
        assert r.status_code == 200 and r.json()["text"].startswith("⚑ power 44.25000,-72.60000")
        reps = c.get("/api/field/reports").json()
        mine = next(x for x in reps if x["id"] == r.json()["id"])
        assert mine["sent"] == 1 and mine["source"] == "station"
        assert len(reps) == 3  # the echo heard back was not added twice
        assert c.post("/api/field/reports", json={"kind": "power", "lat": 44.25, "lon": -72.6, "channel": "Nope"}).status_code == 400
        assert c.post(f"/api/field/reports/{mine['id']}/clear", json={"channel": "Public"}).json()["ok"]
        cleared = next(x for x in c.get("/api/field/reports").json() if x["id"] == mine["id"])
        assert cleared["cleared_by"] == "station"
        assert c.post(f"/api/field/reports/{mine['id']}/clear", json={"channel": "Public"}).status_code == 404
        assert c.get("/api/export/reports.geojson").json()["features"]


def test_field_api_teams_and_surveys(tmp_path):
    cfg, c = make(tmp_path)
    with c:
        t = c.post("/api/field/teams", json={"name": "North Crew", "add_to_radio": True}).json()
        assert t["channel"] == "team-north-crew" and len(t["channel_key"]) == 32 and t["radio_slot"] == 2
        assert any(k.startswith("team-north-crew=") for k in cfg.channel_keys)
        assert c.post("/api/field/teams", json={"name": "North Crew"}).status_code == 400
        nodes = c.get("/api/nodes").json()
        comp = [n["public_key"] for n in nodes if n["node_type"] == "companion"][:2]
        assert c.put(f"/api/field/teams/{t['id']}/members", json={"members": [{"public_key": k} for k in comp]}).json()["ok"]
        teams = c.get("/api/field/teams").json()
        assert len(teams[0]["members"]) == 2
        assert isinstance(c.get(f"/api/field/tracks?team_id={t['id']}&hours=48").json(), list)
        # A message on the team channel is decrypted now that the station has its key.
        assert c.post("/api/field/reports", json={"kind": "ok", "lat": 44.2, "lon": -72.5, "channel": "team-north-crew"}).status_code == 200
        assert c.delete(f"/api/field/teams/{t['id']}").json()["ok"]
        assert not any(k.startswith("team-north-crew=") for k in cfg.channel_keys)

        assert c.post("/api/field/surveys", json={"points": [{"lat": 44.2, "lon": -72.5, "heard": 1, "snr": 2}]}).json()["added"] == 1
        imp = c.post("/api/field/surveys/import", json={"format": "csv", "text": "lat,lon,heard\n44.3,-72.4,no\n"}).json()
        assert imp["added"] == 1
        assert c.post("/api/field/surveys/import", json={"format": "csv", "text": "a,b\n1,2\n"}).status_code == 400
        assert len(c.get("/api/field/surveys").json()) == 2


def test_team_keys_hidden_from_viewers(tmp_path):
    _cfg, c = make(tmp_path)
    with c:
        c.post("/api/field/teams", json={"name": "South"})
        c.post("/api/auth/setup", json={"username": "boss", "password": "long enough pw"})
        c.post("/api/users", json={"username": "look", "password": "viewer password", "role": "viewer"})
        assert c.get("/api/field/teams").json()[0]["channel_key"]
        c.cookies.clear()
        c.post("/api/auth/login", json={"username": "look", "password": "viewer password"})
        assert c.get("/api/field/teams").json()[0]["channel_key"] is None
        assert c.post("/api/field/surveys", json={"points": [{"lat": 44.2, "lon": -72.5}]}).json()["added"] == 1
        assert c.post("/api/field/reports", json={"kind": "tree", "lat": 44.2, "lon": -72.5}).status_code == 403

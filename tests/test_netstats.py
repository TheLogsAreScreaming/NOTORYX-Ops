import json
import time

from fastapi.testclient import TestClient

from notoryx_ops import netstats, settings
from notoryx_ops.config import Config
from notoryx_ops.server import create_app
from notoryx_ops.store import Store


def test_country_lookup():
    assert netstats.country_of(41.33, -73.09) == "US"  # Connecticut
    assert netstats.country_of(52.52, 13.40) == "DE"  # Berlin
    assert netstats.country_of(-33.87, 151.21) == "AU"  # Sydney, on the coast
    assert netstats.country_of(45.42, -75.69) == "CA"  # Ottawa
    assert netstats.country_of(0, 0) is None  # no-fix placeholder
    assert netstats.country_of(30.0, -40.0) is None  # mid Atlantic
    assert netstats.country_of(None, 5) is None
    assert netstats.country_of(2019.17, -1981.93) is None  # broken advert position


def _store(tmp_path):
    s = Store(tmp_path / "s.db")
    now = time.time()
    s.upsert_node("aa" * 32, "Rep CT", "repeater", 41.33, -73.09, 1, now - 100, 5.0)
    s.upsert_node("bb" * 32, "Rep DE", "repeater", 52.52, 13.40, 1, now - 50, 5.0)
    s.upsert_node("cc" * 32, "Phone", "companion", 41.30, -73.10, 1, now - 10, 5.0)
    s.upsert_node("dd" * 32, "Old rep", "repeater", 45.42, -75.69, 1, now - 30 * 86400, 5.0)
    s.upsert_node("ee" * 32, "Hidden rep", "repeater", 48.85, 2.35, 1, now - 5, 5.0)
    s.x("INSERT INTO ignored_nodes(public_key, name, added_at) VALUES (?, ?, ?)", ("ee" * 32, "Hidden rep", now))
    return s


def test_snapshot_counts_without_names_or_positions(tmp_path):
    s = _store(tmp_path)
    snap = netstats.snapshot(s, Config(path=tmp_path / "c.toml", data_dir=tmp_path))
    assert snap["counts"] == {"repeaters": 2, "nodes": 3, "countries": 2, "links": 0}
    assert snap["countries"] == ["DE", "US"]
    assert sorted(snap["repeaters"]) == [["aaaaaaaa", "US"], ["bbbbbbbb", "DE"]]
    assert snap["newest_repeater"]["id"] == "bbbbbbbb" and snap["newest_repeater"]["country"] == "DE"
    text = json.dumps(snap)
    assert "Rep" not in text and "Phone" not in text and "41.33" not in text and "ee" * 4 not in text
    pub = netstats.public_counts(snap)
    assert "repeaters" in pub and pub["repeaters"] == 2 and "id" not in pub["newest_repeater"]


def test_push_sends_snapshot_and_reports_errors(tmp_path):
    s = _store(tmp_path)
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, stats_push_enabled=True,
                 stats_push_url="https://stats.example/v1/push", stats_push_token="t" * 32)
    sent = []
    push = netstats.StatsPush(cfg, s, send=lambda body: sent.append(json.loads(body)) or 200)
    assert push.ready
    assert push.push_once()["repeaters"] == 2 and sent[0]["schema"] == 1
    bad = netstats.StatsPush(cfg, s, send=lambda body: 401)
    try:
        bad.push_once()
        raise AssertionError("expected an error")
    except RuntimeError as exc:
        assert "401" in str(exc)


def test_settings_validate_stats_push(tmp_path):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path)
    try:
        settings.apply_changes(cfg, {"integrations": {"stats_push": True, "stats_push_url": "https://x.example/v1/push"}})
        raise AssertionError("expected an error")
    except ValueError as exc:
        assert "token" in str(exc)
    for bad in ({"stats_push_url": "http://x.example"}, {"stats_push_token": "short"}):
        try:
            settings.apply_changes(cfg, {"integrations": bad})
            raise AssertionError("expected an error")
        except ValueError:
            pass
    settings.apply_changes(cfg, {"integrations": {"stats_push": True, "stats_push_url": "https://x.example/v1/push",
                                                  "stats_push_token": "k" * 40}})
    assert cfg.stats_push_enabled and cfg.stats_push_token == "k" * 40
    view = settings.public_view(cfg)
    assert "k" * 40 not in json.dumps(view) and view["integrations"]["stats_push_token_set"]
    settings.save(cfg)
    text = (tmp_path / "c.toml").read_text()
    assert 'stats_push = true' in text and "k" * 40 in text


def test_public_stats_route(tmp_path):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0)
    c = TestClient(create_app(cfg))
    with c:
        assert c.get("/api/public/stats").status_code == 404
        cfg.public_map = True
        body = c.get("/api/public/stats").json()
        assert body["window_days"] == 7 and body["repeaters"] >= 1 and "nodes" in body
        assert c.post("/api/integrations/stats/push").status_code == 400

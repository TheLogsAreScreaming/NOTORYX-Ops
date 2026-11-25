import sys

import pytest
from fastapi.testclient import TestClient

from notoryx_ops import config, settings
from notoryx_ops.server import create_app

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def load(tmp_path):
    return config.load(tmp_path / "config.toml")


def test_round_trip_through_file(tmp_path):
    cfg = load(tmp_path)
    settings.apply_changes(cfg, {
        "station": {"name": 'My "Ops"', "port": 9000},
        "radio": {"transport": "tcp", "tcp_host": "10.0.0.5", "tcp_port": 5000, "observer_name": "Roof"},
        "channels": ["public", "#hikers", "Team=" + "ab" * 16],
        "fleet": {"poll_minutes": 45, "quiet_hours": 2.5},
        "alerts": {"discord_webhook": "https://discord.com/api/webhooks/1/x", "webhook_url": ""},
        "mqtt": [{"name": "Home", "host": "10.0.0.2", "port": 1883, "password": "pw", "topics": ["meshcore/#"]}],
    })
    settings.save(cfg)
    tomllib.loads(cfg.path.read_text())  # valid TOML
    again = load(tmp_path)
    assert again.name == 'My "Ops"' and again.port == 9000
    assert again.radio_transport == "tcp" and again.tcp_host == "10.0.0.5"
    assert again.channel_keys == ["public", "#hikers", "Team=" + "ab" * 16]
    assert again.poll_minutes == 45 and again.quiet_hours == 2.5
    assert again.discord_webhook.startswith("https://")
    assert again.mqtt[0].password == "pw" and again.mqtt[0].topics == ["meshcore/#"]
    assert again.data_dir == cfg.data_dir
    assert oct(cfg.path.stat().st_mode)[-3:] == "600"


def test_secrets_never_in_public_view(tmp_path):
    cfg = load(tmp_path)
    cfg.admin_token, cfg.discord_webhook, cfg.webhook_secret = "tok" * 5, "https://x/y", "s"
    from notoryx_ops.config import MqttSource

    cfg.mqtt = [MqttSource(name="B", host="h", password="secretpw")]
    view = settings.public_view(cfg)
    text = str(view)
    assert "tok" * 5 not in text and "https://x/y" not in text and "secretpw" not in text
    assert view["station"]["admin_token_set"] and view["alerts"]["discord_webhook_set"] and view["mqtt"][0]["password_set"]


def test_secrets_kept_unless_changed(tmp_path):
    cfg = load(tmp_path)
    cfg.discord_webhook = "https://keep/me"
    from notoryx_ops.config import MqttSource

    cfg.mqtt = [MqttSource(name="B", host="h", password="pw1")]
    settings.apply_changes(cfg, {"alerts": {"discord_webhook": None, "webhook_url": ""},
                                 "mqtt": [{"name": "B", "host": "h2", "password": None}]})
    assert cfg.discord_webhook == "https://keep/me" and cfg.mqtt[0].password == "pw1" and cfg.mqtt[0].host == "h2"
    settings.apply_changes(cfg, {"alerts": {"discord_webhook": ""}})
    assert cfg.discord_webhook == ""


@pytest.mark.parametrize("bad", [
    {"radio": {"transport": "carrier pigeon"}},
    {"radio": {"transport": "tcp", "tcp_host": ""}},
    {"station": {"port": 80}},
    {"station": {"host": "8.8.8.8"}},
    {"station": {"admin_token": "short"}},
    {"channels": ["Team=00"]},
    {"fleet": {"poll_minutes": 1}},
    {"alerts": {"webhook_url": "ftp://x"}},
    {"mqtt": [{"name": "A", "host": "h"}, {"name": "A", "host": "h"}]},
    {"station": {"name": "bad\nname"}},
])
def test_bad_values_change_nothing(tmp_path, bad):
    cfg = load(tmp_path)
    before = settings.to_toml(cfg)
    with pytest.raises(ValueError):
        settings.apply_changes(cfg, bad)
    assert settings.to_toml(cfg) == before


def test_restart_needed_only_for_host_or_port(tmp_path):
    cfg = load(tmp_path)
    assert settings.apply_changes(cfg, {"fleet": {"poll_minutes": 60}}) == []
    assert settings.apply_changes(cfg, {"station": {"port": 9100}}) == ["port"]


def test_settings_api(tmp_path):
    cfg = config.load(tmp_path / "config.toml")
    cfg.demo = True
    with TestClient(create_app(cfg)) as c:
        got = c.get("/api/settings").json()
        assert got["radio"]["transport"] == "none" and "admin_token" not in got["station"]
        r = c.put("/api/settings", json={"channels": ["public", "#ops"], "fleet": {"poll_minutes": 20}})
        assert r.status_code == 200 and r.json()["restart_needed"] == []
        assert [ch["name"] for ch in c.get("/api/station").json()["channels"]] == ["Public", "#ops"]
        assert c.put("/api/settings", json={"fleet": {"poll_minutes": 0}}).status_code == 400
        assert c.post("/api/alerts/test").status_code == 400
        assert isinstance(c.get("/api/settings/serial-ports").json(), list)
        # once a token is set, changes need it
        assert c.put("/api/settings", json={"station": {"admin_token": "a-long-token-123"}}).status_code == 200
        assert c.put("/api/settings", json={"fleet": {"poll_minutes": 25}}).status_code == 401
        ok = c.put("/api/settings", json={"fleet": {"poll_minutes": 25}}, headers={"Authorization": "Bearer a-long-token-123"})
        assert ok.status_code == 200
    assert config.load(tmp_path / "config.toml").poll_minutes == 25


def test_share_settings_round_trip(tmp_path):
    cfg = load(tmp_path)
    settings.apply_changes(cfg, {"share": {"enabled": True, "port": 5005}})
    settings.save(cfg)
    again = load(tmp_path)
    assert again.share_enabled and again.share_port == 5005
    with pytest.raises(ValueError):
        settings.apply_changes(cfg, {"share": {"port": 80}})

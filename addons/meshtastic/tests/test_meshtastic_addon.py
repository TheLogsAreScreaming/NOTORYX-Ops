# SPDX-License-Identifier: GPL-3.0-only
import asyncio
import time

import pytest
from fake_radio import LOCKED, ME, OLD, RELAY_A, RELAY_B, FakeIface
from fastapi.testclient import TestClient
from notoryx_ops.config import Config
from notoryx_ops.server import create_app
from notoryx_ops.station import Station

from notoryx_ops_meshtastic import MeshtasticAddon
from notoryx_ops_meshtastic.admin import AdminError, RemoteAdmin, coerce, firmware_ok, redact
from notoryx_ops_meshtastic.bridge import Bridge, RateLimit, clip, parse_pairs

# ---- admin layer ----------------------------------------------------------------


def test_admin_reads_plans_and_writes():
    f = FakeIface()
    a = RemoteAdmin(f, timeout=2)
    assert a.metadata(RELAY_A)["firmware_version"] == "2.7.15"
    lora = a.get(RELAY_A, "lora")
    assert lora["hop_limit"] == 3 and lora["region"] == "US"
    plan = a.plan(lora, "lora", {"hop_limit": "5"})
    assert plan == [{"field": "hop_limit", "before": 3, "after": 5}]
    assert a.set(RELAY_A, "lora", lora, {"hop_limit": 5}) == plan
    assert f.configs[RELAY_A].lora.hop_limit == 5
    assert f.configs[RELAY_A].lora.region == 1  # untouched fields survive the write
    # The second request carries the session passkey the node handed back.
    assert f.sent_admin[-1][1].session_passkey == b"pk"


def test_secrets_never_leave_and_never_get_written():
    f = FakeIface()
    a = RemoteAdmin(f, timeout=2)
    net = a.get(RELAY_A, "network")
    assert redact("network", net)["wifi_psk"] == "(hidden)"
    sec = a.get(RELAY_A, "security")
    assert redact("security", sec)["private_key"] == "(hidden)"
    # Writing another network field keeps the node's Wi-Fi password as it was.
    a.set(RELAY_A, "network", net, {"ntp_server": "pool.example"})
    assert f.configs[RELAY_A].network.wifi_psk == "secret-wifi"
    for section, field in [("network", "wifi_psk"), ("security", "private_key"), ("security", "admin_key"), ("lora", "region")]:
        with pytest.raises(AdminError):
            coerce(section, field, "x")


def test_coerce_types_and_errors():
    assert coerce("lora", "hop_limit", "4") == 4
    assert coerce("device", "role", "CLIENT") == "CLIENT"
    assert coerce("position", "gps_enabled", "yes") is True
    with pytest.raises(AdminError):
        coerce("device", "role", "WIZARD")
    with pytest.raises(AdminError):
        coerce("lora", "hop_limit", "-1")
    with pytest.raises(AdminError):
        coerce("lora", "nope", 1)
    with pytest.raises(AdminError):
        coerce("nosuch", "x", 1)


def test_refusals_explain_themselves():
    a = RemoteAdmin(FakeIface(), timeout=2)
    with pytest.raises(AdminError, match="Admin keys"):
        a.get(LOCKED, "lora")


def test_firmware_check():
    assert firmware_ok("2.5.0") and firmware_ok("2.7.15.abc")
    assert not firmware_ok("2.4.9") and not firmware_ok("") and not firmware_ok(None)


# ---- bridge -----------------------------------------------------------------------


def test_pairs_and_helpers():
    assert parse_pairs(["0=Public", " 1 = #ops "]) == [(0, "Public"), (1, "#ops")]
    with pytest.raises(ValueError):
        parse_pairs(["Public"])
    with pytest.raises(ValueError):
        parse_pairs(["0=Public", "0=#ops"])
    assert len(clip("é" * 200, 120).encode()) <= 120
    rl = RateLimit(2)
    assert rl.allow(100) and rl.allow(101) and not rl.allow(102) and rl.allow(161.5)


class FakeHub:
    def subscribe(self):
        return asyncio.Queue()

    def unsubscribe(self, q):
        pass


class FakeStation:
    def __init__(self):
        self.hub = FakeHub()
        self.sent = []
        st = self

        class Radio:
            async def send_channel(self, key, text):
                st.sent.append((key, text))

        self.radio = Radio()

    def channel(self, name):
        from notoryx_ops.channels import parse_channel

        return parse_channel("public" if name.lower() == "public" else name)


class FakeLink:
    def __init__(self, iface):
        self.iface = iface

    def my_num(self):
        return ME

    def send_text(self, text, idx):
        self.iface.sendText(text, channelIndex=idx)


async def test_bridge_both_ways_with_loop_and_rate_protection():
    f = FakeIface()
    st = FakeStation()
    settings = {"bridge_enabled": True, "bridge_pairs": ["0=Public"], "bridge_per_minute": 2}
    b = Bridge(settings, st, FakeLink(f))
    pkt = {"from": RELAY_A, "to": 0xFFFFFFFF, "channel": 0, "decoded": {"text": "hello from the ridge"}}
    assert await b.from_meshtastic(pkt)
    assert st.sent[-1][1] == "[MT] RR: hello from the ridge"
    # Tagged text, direct messages, other channels and our own packets never cross.
    assert not await b.from_meshtastic({**pkt, "decoded": {"text": "[MC] X: hi"}})
    assert not await b.from_meshtastic({**pkt, "to": RELAY_B})
    assert not await b.from_meshtastic({**pkt, "channel": 1})
    assert not await b.from_meshtastic({**pkt, "from": ME})
    # MeshCore -> Meshtastic
    assert await b.from_meshcore("Public", "Hiker", "on the summit")
    assert f.sent_text[-1] == (0, "[MC] Hiker: on the summit")
    assert not await b.from_meshcore("Public", "Station", "[MT] RR: hello")
    assert not await b.from_meshcore("#other", "Hiker", "hi")
    # Rate limit: 2 a minute each way.
    assert await b.from_meshtastic(pkt)
    assert not await b.from_meshtastic(pkt)
    assert b.stats["dropped_rate"] == 1 and b.stats["to_meshcore"] == 2
    settings["bridge_enabled"] = False
    assert not await b.from_meshcore("Public", "Hiker", "x")


# ---- the add-on inside a station --------------------------------------------------------


def make(tmp_path, transport="serial"):
    fake = FakeIface()
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0)
    cfg.addons = {"meshtastic": {"transport": transport, "stagger_s": 0, "admin_timeout_s": 2}}
    st = Station(cfg, addon_factories=[("meshtastic", lambda ctx: MeshtasticAddon(ctx, iface_factory=lambda s: fake))])
    return cfg, fake, TestClient(create_app(cfg, st))


def wait_connected(c):
    for _ in range(50):
        if c.get("/api/addons/meshtastic/status").json()["radio"]["state"] == "connected":
            return
        time.sleep(0.05)
    raise AssertionError("never connected")


def test_api_status_nodes_and_admin(tmp_path):
    _cfg, fake, c = make(tmp_path)
    with c:
        wait_connected(c)
        s = c.get("/api/addons/meshtastic/status").json()
        assert s["radio"]["me"]["public_key"] and "lora" in s["sections"]
        assert c.get("/api/addons").json()[0]["license"] == "GPL-3.0-only"
        assert "Meshtastic" in c.get("/addons/meshtastic/").text
        nodes = c.get("/api/addons/meshtastic/nodes").json()
        assert [n["num"] for n in nodes][:2] == [LOCKED, RELAY_A] and all(n["num"] != ME for n in nodes)
        assert [ch["name"] for ch in c.get("/api/addons/meshtastic/channels").json()] == ["Primary", "ops"]

        assert c.get(f"/api/addons/meshtastic/nodes/{RELAY_A}/metadata").json()["remote_admin_ok"]
        net = c.get(f"/api/addons/meshtastic/nodes/{RELAY_A}/config/network").json()
        assert net["wifi_psk"] == "(hidden)"
        assert c.get(f"/api/addons/meshtastic/nodes/{OLD}/config/lora").status_code == 409
        assert c.get(f"/api/addons/meshtastic/nodes/{LOCKED}/config/lora").status_code == 502
        assert c.get(f"/api/addons/meshtastic/nodes/{RELAY_A}/config/bogus").status_code == 400

        url = f"/api/addons/meshtastic/nodes/{RELAY_A}/config/lora"
        prev = c.post(url, json={"changes": {"hop_limit": 6}}).json()
        assert prev == {"applied": False, "changes": [{"field": "hop_limit", "before": 3, "after": 6}]}
        assert fake.configs[RELAY_A].lora.hop_limit == 3  # preview changes nothing
        assert c.post(url, json={"changes": {"hop_limit": 6}, "confirm": True}).json()["applied"]
        assert fake.configs[RELAY_A].lora.hop_limit == 6
        assert c.post(url, json={"changes": {"region": "EU_868"}, "confirm": True}).status_code == 400

        assert c.post(f"/api/addons/meshtastic/nodes/{RELAY_A}/owner", json={"long_name": "Ridge"}).status_code == 400
        assert c.post(f"/api/addons/meshtastic/nodes/{RELAY_A}/owner", json={"long_name": "Ridge", "confirm": True}).json()["ok"]
        assert c.post(f"/api/addons/meshtastic/nodes/{RELAY_A}/favorite", json={"target": RELAY_B}).json()["ok"]
        assert fake.favorites[RELAY_A] == {RELAY_B}
        assert c.post(f"/api/addons/meshtastic/nodes/{RELAY_A}/reboot", json={}).status_code == 400
        assert c.post(f"/api/addons/meshtastic/nodes/{RELAY_A}/reboot", json={"confirm": True}).json()["ok"]


def poll(c, job):
    for _ in range(100):
        j = c.get(f"/api/addons/meshtastic/jobs/{job['id']}").json()
        if j["state"] in ("done", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError("job never finished")


def test_templates_check_and_apply(tmp_path):
    _cfg, fake, c = make(tmp_path)
    with c:
        wait_connected(c)
        base = "/api/addons/meshtastic/templates"
        tid = c.post(base, json={"name": "Relays", "section": "lora", "fields": {"hop_limit": 4}}).json()["id"]
        assert c.post(base, json={"name": "Relays", "section": "lora", "fields": {"hop_limit": 4}}).status_code == 400
        assert c.post(base, json={"name": "Bad", "section": "lora", "fields": {"region": "EU_868"}}).status_code == 400
        fake.configs[RELAY_B].lora.hop_limit = 4
        job = poll(c, c.post(f"{base}/{tid}/check", json={"nodes": [RELAY_A, RELAY_B, LOCKED]}).json())
        res = {r["node"]: r for r in job["results"]}
        assert not res[RELAY_A]["compliant"] and res[RELAY_B]["compliant"] and not res[LOCKED]["ok"]
        assert c.post(f"{base}/{tid}/apply", json={"nodes": [RELAY_A]}).status_code == 400
        job = poll(c, c.post(f"{base}/{tid}/apply", json={"nodes": [RELAY_A, RELAY_B], "confirm": True}).json())
        assert job["state"] == "done" and fake.configs[RELAY_A].lora.hop_limit == 4
        changed = {r["node"]: r["changed"] for r in job["results"]}
        assert changed[RELAY_B] == []  # already matched, nothing sent


def test_settings_and_bridge_validation(tmp_path):
    cfg, _fake, c = make(tmp_path, transport="none")
    with c:
        s = c.get("/api/addons/meshtastic/status").json()
        assert s["radio"]["state"] == "off"
        assert c.put("/api/addons/meshtastic/settings", json={"transport": "carrier-pigeon"}).status_code == 400
        assert c.put("/api/addons/meshtastic/settings", json={"bridge_pairs": ["0=Nowhere"]}).status_code == 400
        assert c.put("/api/addons/meshtastic/settings", json={"bridge_enabled": True, "bridge_pairs": ["0=Public"]}).json()["ok"]
        assert c.get(f"/api/addons/meshtastic/nodes/{RELAY_A}/config/lora").status_code == 503
    from notoryx_ops import config

    back = config.load(cfg.path)
    assert back.addons["meshtastic"]["bridge_pairs"] == ["0=Public"] and back.addons["meshtastic"]["bridge_enabled"] is True


def test_admin_needs_operator_when_accounts_on(tmp_path):
    _cfg, _fake, c = make(tmp_path)
    with c:
        c.post("/api/auth/setup", json={"username": "boss", "password": "long enough pw"})
        c.post("/api/users", json={"username": "look", "password": "viewer password", "role": "viewer"})
        c.cookies.clear()
        assert c.get("/api/addons/meshtastic/nodes").status_code == 401
        c.post("/api/auth/login", json={"username": "look", "password": "viewer password"})
        assert c.get("/api/addons/meshtastic/nodes").status_code == 200
        assert c.get(f"/api/addons/meshtastic/nodes/{RELAY_A}/config/lora").status_code == 403
        assert c.put("/api/addons/meshtastic/settings", json={"bridge_enabled": True}).status_code == 403

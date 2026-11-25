import pytest

from notoryx_ops import fleet as fleet_mod
from notoryx_ops.alerts import Alerts
from notoryx_ops.config import Config
from notoryx_ops.fleet import Fleet
from notoryx_ops.ingest import Hub
from notoryx_ops.radio import RadioError
from notoryx_ops.store import Store

KEY = "ab" * 32
STATUS = {"bat": 4172, "noise_floor": -101, "uptime": 100}


class FakeRadio:
    def __init__(self, misses: int):
        self.misses = misses
        self.calls: list[str] = []

    async def login(self, key, password):
        self.calls.append("login")

    async def status(self, key):
        self.calls.append("status")
        if self.misses:
            self.misses -= 1
            raise RadioError("No status reply.")
        return STATUS

    async def command(self, key, cmd, timeout=15):
        return "1.0"

    async def neighbours(self, key):
        return []


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet_mod, "AFTER_LOGIN_PAUSE_S", 0)
    monkeypatch.setattr(fleet_mod, "STATUS_RETRY_PAUSE_S", 0)

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(fleet_mod.asyncio, "sleep", no_sleep)

    def _make(misses):
        cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path)
        store, hub, radio = Store(":memory:"), Hub(), FakeRadio(misses)
        f = Fleet(cfg, store, radio=radio, alerts=Alerts(cfg, store, hub), hub=hub)
        rid = f.add(KEY, "Test repeater", "pw")
        return f, radio, store.repeaters(include_password=True)[0] | {"id": rid}

    return _make


async def test_one_missed_status_reply_is_retried(make):
    f, radio, rep = make(misses=1)
    row = await f.poll_one(rep)
    assert row["ok"] == 1 and row["battery_mv"] == 4172
    assert radio.calls == ["login", "status", "status"]


async def test_two_missed_replies_fail_the_poll(make):
    f, radio, rep = make(misses=2)
    row = await f.poll_one(rep)
    assert row["ok"] == 0 and row["error"] == "No status reply."
    assert radio.calls == ["login", "status", "status"]


async def test_answered_first_time_asks_once(make):
    f, radio, rep = make(misses=0)
    row = await f.poll_one(rep)
    assert row["ok"] == 1 and radio.calls == ["login", "status"]

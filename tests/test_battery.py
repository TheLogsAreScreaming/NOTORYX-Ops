"""Tests for battery voltage to percentage conversion."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from notoryx_ops.battery import annotate, mv_to_pct
from notoryx_ops.config import Config
from notoryx_ops.server import create_app

# ---- unit tests for mv_to_pct ---------------------------------------------

def test_full_charge():
    assert mv_to_pct(4200) == 100


def test_dead_battery():
    assert mv_to_pct(3000) == 0


def test_half_charge():
    # (3600 - 3000) / 1200 * 100 = 50
    assert mv_to_pct(3600) == 50


def test_clamped_above_max():
    assert mv_to_pct(5000) == 100


def test_clamped_below_min():
    assert mv_to_pct(1000) == 0


def test_none_returns_none():
    assert mv_to_pct(None) is None


def test_typical_voltage():
    # 3900 mV → (900/1200)*100 = 75%
    assert mv_to_pct(3900) == 75


# ---- annotate function tests -----------------------------------------------

def test_annotate_adds_pct_field():
    row = {"battery_mv": 3600}
    result = annotate(row)
    assert result["battery_pct"] == 50


def test_annotate_none_mv():
    row = {"battery_mv": None}
    result = annotate(row)
    assert result["battery_pct"] is None


def test_annotate_missing_mv():
    row = {"other": "value"}
    result = annotate(row)
    assert "battery_pct" not in result


# ---- API tests -------------------------------------------------------------

def make(tmp_path, **kw):
    cfg = Config(path=tmp_path / "c.toml", data_dir=tmp_path, demo=True, stagger_seconds=0, **kw)
    return TestClient(create_app(cfg))


def test_fleet_history_includes_battery_pct(tmp_path):
    with make(tmp_path) as c:
        st = c.app.state.station
        rid = st.fleet.add("ab" * 32, "Tower", "pw")
        st.store.add_status(rid, {"polled_at": time.time(), "ok": 1, "battery_mv": 3900})
        rows = c.get(f"/api/fleet/{rid}/history").json()
        assert rows, "expected at least one history row"
        assert rows[0]["battery_pct"] == 75

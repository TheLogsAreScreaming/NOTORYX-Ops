"""Tests for contactwarn — contact-list capacity warning."""
from __future__ import annotations

import time

from notoryx_ops import contactwarn
from notoryx_ops.contactwarn import MAX_CONTACTS, WARN_THRESHOLD, contact_warning


def setup_function():
    contactwarn.reset_warn_clock()


# ---------------------------------------------------------------------------
# basic percentage computation
# ---------------------------------------------------------------------------


def test_zero_contacts():
    r = contact_warning(0)
    assert r["contact_count"] == 0
    assert r["contact_max"] == MAX_CONTACTS
    assert r["usage_pct"] == 0.0
    assert r["near_full"] is False
    assert r["warn"] is False


def test_half_full():
    r = contact_warning(MAX_CONTACTS // 2)
    assert r["near_full"] is False
    assert r["warn"] is False


def test_exactly_at_threshold():
    count = int(MAX_CONTACTS * WARN_THRESHOLD)
    r = contact_warning(count)
    assert r["near_full"] is True
    assert r["warn"] is True


def test_above_threshold():
    r = contact_warning(MAX_CONTACTS)
    assert r["near_full"] is True
    assert r["warn"] is True
    assert r["usage_pct"] == 100.0


def test_just_below_threshold():
    count = int(MAX_CONTACTS * WARN_THRESHOLD) - 1
    r = contact_warning(count)
    assert r["near_full"] is False
    assert r["warn"] is False


# ---------------------------------------------------------------------------
# cooldown: warn fires at most once per hour
# ---------------------------------------------------------------------------


def test_warn_fires_once_then_suppressed():
    count = MAX_CONTACTS  # 100 % full
    first = contact_warning(count)
    assert first["warn"] is True

    # second call immediately after — cooldown active
    second = contact_warning(count)
    assert second["warn"] is False
    assert second["near_full"] is True  # still near full, just suppressed


def test_warn_fires_again_after_cooldown(monkeypatch):
    count = MAX_CONTACTS
    contact_warning(count)  # first warning recorded

    # simulate that the last warning happened more than a cooldown ago
    old_warn_time = time.time() - contactwarn._COOLDOWN - 1
    monkeypatch.setattr(contactwarn, "_last_warned", old_warn_time)

    second = contact_warning(count)
    assert second["warn"] is True


def test_no_warn_below_threshold_even_after_cooldown():
    count = int(MAX_CONTACTS * WARN_THRESHOLD) - 1
    r = contact_warning(count)
    assert r["warn"] is False

    # second call much later — still below threshold, so never warns
    contactwarn._last_warned = 0.0
    r2 = contact_warning(count)
    assert r2["warn"] is False


# ---------------------------------------------------------------------------
# response shape
# ---------------------------------------------------------------------------


def test_response_has_all_fields():
    r = contact_warning(50)
    assert set(r.keys()) == {"contact_count", "contact_max", "usage_pct", "near_full", "warn"}


def test_usage_pct_rounded():
    r = contact_warning(33)
    assert r["usage_pct"] == round(33 / MAX_CONTACTS * 100, 1)

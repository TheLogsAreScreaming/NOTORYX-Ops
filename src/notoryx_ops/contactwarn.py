"""Contact-list capacity warning.

MeshCore radios hold at most MAX_CONTACTS contacts.  When the list is
more than WARN_THRESHOLD full (>90 %) the UI should show a banner.  To
avoid flooding the log, the warning is emitted at most once per COOLDOWN
seconds.
"""
from __future__ import annotations

import time

MAX_CONTACTS: int = 100
WARN_THRESHOLD: float = 0.90
_COOLDOWN: float = 3600.0  # one hour

_last_warned: float = 0.0


def reset_warn_clock() -> None:
    """Reset the cooldown — used in tests."""
    global _last_warned
    _last_warned = 0.0


def contact_warning(contact_count: int) -> dict:
    """Return a warning dict when the contact list is >90 % full.

    At most one warning is returned per hour (the ``warn`` flag goes True
    at most once per ``_COOLDOWN`` seconds regardless of how often this
    function is called).
    """
    global _last_warned
    pct = contact_count / MAX_CONTACTS
    near_full = pct >= WARN_THRESHOLD
    now = time.time()
    warn = near_full and (now - _last_warned) >= _COOLDOWN
    if warn:
        _last_warned = now
    return {
        "contact_count": contact_count,
        "contact_max": MAX_CONTACTS,
        "usage_pct": round(pct * 100, 1),
        "near_full": near_full,
        "warn": warn,
    }

"""When a heard node counts as stale."""

from __future__ import annotations

# The choices offered in Settings, in hours. 0 = never mark nodes stale.
STALE_CHOICES_HOURS: tuple[float, ...] = (0, 1, 6, 24, 72, 168)
DEFAULT_STALE_HOURS: float = 24

# Times before this (2020-01-01 UTC) come from a clock that was never set, such as 0 / the epoch.
MIN_KNOWN_TIME: float = 1577836800
# Times further ahead than this are from a clock that runs fast, not real.
MAX_FUTURE_SECONDS: float = 86400


def known_time(t: float | None, now: float) -> float | None:
    """t when it is a believable heard time, else None (unset, epoch, pre-2020 or over a day ahead)."""
    if not t or t < MIN_KNOWN_TIME or t > now + MAX_FUTURE_SECONDS:
        return None
    return t


def is_stale(last_heard: float | None, now: float, after_hours: float) -> bool:
    """True when a node was last heard more than after_hours ago.

    A node with no believable last-heard time (see known_time) is never stale,
    and after_hours 0 turns the rule off.
    """
    last_heard = known_time(last_heard, now)
    if not after_hours or last_heard is None:
        return False
    return now - last_heard > after_hours * 3600

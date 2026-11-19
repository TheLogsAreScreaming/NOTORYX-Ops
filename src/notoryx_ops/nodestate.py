"""When a heard node counts as stale."""

from __future__ import annotations

# The choices offered in Settings, in hours. 0 = never mark nodes stale.
STALE_CHOICES_HOURS: tuple[float, ...] = (0, 1, 6, 24, 72, 168)
DEFAULT_STALE_HOURS: float = 24


def is_stale(last_heard: float | None, now: float, after_hours: float) -> bool:
    """True when a node was last heard more than after_hours ago.

    A node with no last-heard time is never stale, and after_hours 0 turns the rule off.
    """
    if not after_hours or not last_heard:
        return False
    return now - last_heard > after_hours * 3600

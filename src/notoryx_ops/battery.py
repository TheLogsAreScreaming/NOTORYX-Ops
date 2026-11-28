"""Battery voltage to percentage conversion for LiPo/Li-ion cells.

Formula: clamp((V - 3.0) / (4.2 - 3.0) * 100, 0, 100)
Input is millivolts (as stored in the repeater_status table).
"""

from __future__ import annotations

from typing import Any

_V_MIN = 3000   # 3.0 V → 0 %
_V_MAX = 4200   # 4.2 V → 100 %
_V_RANGE = _V_MAX - _V_MIN  # 1200 mV


def mv_to_pct(battery_mv: int | float | None) -> int | None:
    """Return battery percentage (0-100) or None if voltage is unavailable."""
    if battery_mv is None:
        return None
    pct = (battery_mv - _V_MIN) / _V_RANGE * 100
    return max(0, min(100, round(pct)))


def annotate(row: dict[str, Any]) -> dict[str, Any]:
    """Add ``battery_pct`` to a dict that may contain ``battery_mv``. Mutates and returns the row."""
    if "battery_mv" in row:
        row["battery_pct"] = mv_to_pct(row["battery_mv"])
    return row

"""Annotate channel messages with the sender's geographic region (country).

The sender field in a message is a 2-char hex prefix.  We look up the
matching node's lat/lon and use the existing country polygon data to name
the country.  Results are cached by node prefix to avoid repeated queries.

The annotation is applied at API response time only; no schema changes.
"""

from __future__ import annotations

from .netstats import country_of


def _sender_region(store, sender: str, _cache: dict) -> str | None:
    """Return the country name for a sender prefix, or None."""
    if not sender or len(sender) < 2:
        return None
    pre = sender[:2].lower()
    if pre in _cache:
        return _cache[pre]
    row = store.one(
        "SELECT lat, lon FROM nodes WHERE public_key LIKE ? AND lat IS NOT NULL LIMIT 1",
        (pre + "%",),
    )
    result = country_of(row["lat"], row["lon"]) if row else None
    _cache[pre] = result
    return result


def annotate_messages(store, messages: list[dict]) -> list[dict]:
    """Add a ``region`` field to each message where the sender's location is known."""
    if not messages:
        return messages
    cache: dict[str, str | None] = {}
    for msg in messages:
        msg["region"] = _sender_region(store, msg.get("sender") or "", cache)
    return messages

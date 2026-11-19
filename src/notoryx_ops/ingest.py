"""Turns raw packets from any source into stored rows and live dashboard events."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from . import decoder
from .channels import Channel, decrypt_group_text
from .store import Store

log = logging.getLogger(__name__)


class Hub:
    """Fan-out of live events to connected dashboards."""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a slow browser misses some events rather than stalling the station


def summarize(pkt: decoder.Packet, message=None) -> str:
    if pkt.advert:
        a = pkt.advert
        return f"{a.name or a.public_key[:8]} ({a.node_type})"
    if message is not None:
        return f"{message.channel} · {message.sender or '?'}"
    if pkt.payload_type in ("group_text", "group_data"):
        return f"channel {pkt.channel_hash} · encrypted"
    if pkt.payload_type in ("text", "request", "response", "path"):
        return f"{pkt.src_hash} > {pkt.dest_hash} · encrypted"
    if pkt.payload_type == "trace":
        route = pkt.extra.get("trace_route") or []
        return " > ".join(route) or "trace"
    if pkt.payload_type == "ack":
        return f"crc {pkt.ack_crc}"
    if pkt.payload_type == "anon_request":
        return f"login or anon request to {pkt.dest_hash}"
    return pkt.payload_type


class Ingest:
    def __init__(self, store: Store, channels: list[Channel], hub: Hub):
        self.store = store
        self.channels = channels
        self.hub = hub
        self.counts = {"ok": 0, "bad": 0}

    def handle(
        self,
        raw: bytes | str,
        observer: str,
        source: str,
        snr: float | None = None,
        rssi: float | None = None,
        received_at: float | None = None,
        observer_key: str | None = None,
    ) -> dict[str, Any] | None:
        now = received_at or time.time()
        try:
            pkt = decoder.decode(raw)
        except decoder.DecodeError as exc:
            self.counts["bad"] += 1
            log.debug("skipping undecodable packet from %s: %s", observer, exc)
            return None
        self.counts["ok"] += 1

        repeat = self.store.seen_hash(pkt.packet_hash) > 0
        message = None
        if pkt.payload_type == "group_text":
            message = decrypt_group_text(bytes.fromhex(pkt.payload), self.channels)

        node_key = pkt.advert.public_key if pkt.advert else None
        row = {
            "hash": pkt.packet_hash,
            "received_at": now,
            "observer": observer,
            "source": source,
            "route": pkt.route,
            "ptype": pkt.payload_type,
            "hops": pkt.hops,
            "hash_size": pkt.hash_size,
            "path": pkt.path if pkt.payload_type != "trace" else pkt.extra.get("trace_route", []),
            "snr": snr,
            "rssi": rssi,
            "raw": pkt.raw,
            "channel_hash": pkt.channel_hash,
            "src_hash": pkt.src_hash,
            "dest_hash": pkt.dest_hash,
            "node_key": node_key,
            "summary": summarize(pkt, message),
        }
        row["id"] = self.store.add_packet(row)
        self.store.touch_observer(observer, source, now, observer_key)

        if pkt.advert:
            a = pkt.advert
            direct = observer if pkt.hops == 0 else None
            self.store.upsert_node(
                a.public_key, a.name, a.node_type, a.lat, a.lon, a.timestamp, now, snr if direct else None, direct
            )
        elif pkt.src_hash:
            self.store.touch_prefix(pkt.src_hash, now)
        if pkt.payload_type != "trace":
            for hop in pkt.path:
                self.store.touch_prefix(hop, now)

        if message is not None:
            fresh = self.store.add_message(pkt.packet_hash, now, message.channel, message.sender,
                                           message.text, message.timestamp, pkt.hops)
            if fresh:
                self.hub.publish({"type": "message", "channel": message.channel, "sender": message.sender,
                                  "text": message.text, "received_at": now, "hops": pkt.hops})

        event = {"type": "packet", "repeat": repeat, **row}
        if pkt.advert:
            event["advert"] = {"name": pkt.advert.name, "node_type": pkt.advert.node_type,
                               "lat": pkt.advert.lat, "lon": pkt.advert.lon}
        if self.store.is_ignored(node_key):
            event["ignored"] = True  # stored, but kept off the live map and packet feed
        else:
            self.hub.publish(event)
        return event

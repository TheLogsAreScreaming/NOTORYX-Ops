# SPDX-License-Identifier: GPL-3.0-only
"""Channel message bridge between Meshtastic and MeshCore. Off unless turned on.

Each pair links one Meshtastic channel (by index on the station's Meshtastic radio) with one
MeshCore channel (by name on the station). Only channel messages cross, never direct
messages. Bridged text carries a tag ([MT] or [MC]) and the bridge never forwards tagged
text, so a message can't loop. A per-direction limit protects airtime on both meshes.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from typing import Any

log = logging.getLogger(__name__)
TAG_FROM_MT = "[MT]"
TAG_FROM_MC = "[MC]"
TAGS = (TAG_FROM_MT, TAG_FROM_MC)
MT_MAX_BYTES = 200
MC_MAX_BYTES = 120
PAIR_RE = re.compile(r"^\s*(\d)\s*=\s*(.+?)\s*$")


def parse_pairs(specs: list[str]) -> list[tuple[int, str]]:
    out = []
    for s in specs or []:
        m = PAIR_RE.match(str(s))
        if not m:
            raise ValueError(f"Bridge pairs look like '0=Public' (Meshtastic channel index = MeshCore channel): {s!r}")
        out.append((int(m.group(1)), m.group(2)))
    if len({p[0] for p in out}) != len(out) or len({p[1].lower() for p in out}) != len(out):
        raise ValueError("Each channel can be in only one bridge pair.")
    return out


def clip(text: str, max_bytes: int) -> str:
    b = text.encode()
    if len(b) <= max_bytes:
        return text
    return b[: max_bytes - 3].decode("utf-8", "ignore") + "..."


class RateLimit:
    def __init__(self, per_minute: int):
        self.per_minute = max(1, per_minute)
        self.sent: deque[float] = deque()

    def allow(self, now: float | None = None) -> bool:
        now = now or time.time()
        while self.sent and self.sent[0] < now - 60:
            self.sent.popleft()
        if len(self.sent) >= self.per_minute:
            return False
        self.sent.append(now)
        return True


class Bridge:
    def __init__(self, settings: dict[str, Any], station: Any, link: Any):
        self.settings = settings
        self.station = station
        self.link = link
        self.stats = {"to_meshcore": 0, "to_meshtastic": 0, "dropped_rate": 0, "dropped_error": 0}
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None
        self._queue: asyncio.Queue | None = None
        self.reset_limits()

    def reset_limits(self) -> None:
        n = int(self.settings.get("bridge_per_minute") or 6)
        self._to_mc = RateLimit(n)
        self._to_mt = RateLimit(n)

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("bridge_enabled"))

    def pairs(self) -> list[tuple[int, str]]:
        try:
            return parse_pairs(self.settings.get("bridge_pairs") or [])
        except ValueError:
            return []

    def start(self) -> None:
        if self._task:
            return
        self._queue = self.station.hub.subscribe()
        self._task = asyncio.create_task(self._watch_meshcore(), name="bridge")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._queue is not None:
            self.station.hub.unsubscribe(self._queue)
            self._queue = None

    def info(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "pairs": [{"meshtastic_index": i, "meshcore_channel": c} for i, c in self.pairs()],
                "per_minute": int(self.settings.get("bridge_per_minute") or 6), "stats": dict(self.stats),
                "last_error": self.last_error}

    # ---- MeshCore -> Meshtastic -------------------------------------------------------
    async def _watch_meshcore(self) -> None:
        while True:
            ev = await self._queue.get()
            if ev.get("type") == "message":
                try:
                    await self.from_meshcore(ev.get("channel") or "", ev.get("sender"), ev.get("text") or "")
                except Exception as exc:  # noqa: BLE001
                    self.stats["dropped_error"] += 1
                    self.last_error = str(exc)

    async def from_meshcore(self, channel: str, sender: str | None, text: str) -> bool:
        if not self.enabled or not text or text.startswith(TAGS):
            return False
        target = next((i for i, c in self.pairs() if c.lower() == channel.lower()), None)
        if target is None:
            return False
        if not self._to_mt.allow():
            self.stats["dropped_rate"] += 1
            return False
        msg = clip(f"{TAG_FROM_MC} {sender or '?'}: {text}", MT_MAX_BYTES)
        await asyncio.to_thread(self.link.send_text, msg, target)
        self.stats["to_meshtastic"] += 1
        return True

    # ---- Meshtastic -> MeshCore -------------------------------------------------------
    def on_meshtastic_text(self, packet: dict[str, Any]) -> None:
        """Called on the event loop for every text packet the Meshtastic radio receives."""
        task = asyncio.ensure_future(self.from_meshtastic(packet))
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Future) -> None:
        exc = task.exception() if not task.cancelled() else None
        if exc:
            self.stats["dropped_error"] += 1
            self.last_error = str(exc)
            log.warning("bridge to MeshCore failed: %s", exc)

    async def from_meshtastic(self, packet: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        text = ((packet.get("decoded") or {}).get("text") or "").strip()
        if not text or text.startswith(TAGS):
            return False
        if packet.get("to") not in (None, 0xFFFFFFFF, "^all"):
            return False  # direct messages never cross
        if packet.get("from") == self.link.my_num():
            return False
        idx = int(packet.get("channel") or 0)
        target = next((c for i, c in self.pairs() if i == idx), None)
        if target is None:
            return False
        if not self._to_mc.allow():
            self.stats["dropped_rate"] += 1
            return False
        who = self._name(packet.get("from"))
        ch = self.station.channel(target)
        await self.station.radio.send_channel(ch.key, clip(f"{TAG_FROM_MT} {who}: {text}", MC_MAX_BYTES))
        self.stats["to_meshcore"] += 1
        return True

    def _name(self, num: Any) -> str:
        try:
            u = (self.link.iface.nodesByNum or {}).get(num, {}).get("user", {})
            return u.get("shortName") or u.get("longName") or f"!{int(num):08x}"
        except Exception:  # noqa: BLE001
            return "?"

"""Repeaters you own: scheduled status polls, history and bulk settings.

Every request goes over the air, so polls are spaced out (stagger_seconds between
repeaters, poll_minutes between rounds) and the costlier checks run less often.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from .alerts import Alerts
from .config import Config
from .ingest import Hub
from .radio import RadioError, RadioLink
from .store import Store

log = logging.getLogger(__name__)

FIRMWARE_EVERY_S = 24 * 3600
NEIGHBOURS_EVERY_S = 6 * 3600

# Settings that can be pushed to several repeaters at once. Names follow the repeater CLI.
BULK_SETTINGS: dict[str, str] = {
    "advert.interval": r"^\d{1,4}$",
    "flood.advert.interval": r"^\d{1,3}$",
    "flood.max": r"^\d{1,2}$",
    "txdelay": r"^\d+(\.\d+)?$",
    "direct.txdelay": r"^\d+(\.\d+)?$",
    "rxdelay": r"^\d+(\.\d+)?$",
    "af": r"^\d+(\.\d+)?$",
    "repeat": r"^(on|off)$",
    "allow.read.only": r"^(on|off)$",
}

KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def status_row(st: dict[str, Any]) -> dict[str, Any]:
    """Map a status reply onto a history row."""
    return {
        "ok": 1,
        "battery_mv": st.get("bat"),
        "noise_floor": st.get("noise_floor"),
        "last_rssi": st.get("last_rssi"),
        "last_snr": st.get("last_snr"),
        "uptime_s": st.get("uptime"),
        "airtime_s": st.get("airtime"),
        "rx_airtime_s": st.get("rx_airtime"),
        "tx_queue": st.get("tx_queue_len"),
        "recv": st.get("nb_recv"),
        "sent": st.get("nb_sent"),
        "flood_dups": st.get("flood_dups"),
        "direct_dups": st.get("direct_dups"),
        "recv_errors": st.get("recv_errors"),
    }


def parse_get_reply(text: str) -> str:
    """Repeaters answer 'get x' with '> value' or '-> value'."""
    t = text.strip()
    for p in ("->", ">"):
        if t.startswith(p):
            return t[len(p):].strip()
    return t


class Fleet:
    def __init__(self, cfg: Config, store: Store, radio: RadioLink, alerts: Alerts, hub: Hub):
        self.cfg, self.store, self.radio, self.alerts, self.hub = cfg, store, radio, alerts, hub
        self.polling = False
        self.last_round: float | None = None
        self.next_round: float | None = None
        self._kick = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    def info(self) -> dict[str, Any]:
        return {"polling": self.polling, "last_round": self.last_round, "next_round": self.next_round,
                "poll_minutes": self.cfg.poll_minutes, "stagger_seconds": self.cfg.stagger_seconds}

    # ---- repeaters list ---------------------------------------------------
    def add(self, public_key: str, name: str, password: str = "", notes: str = "") -> int:
        if not KEY_RE.match(public_key):
            raise ValueError("Public key must be 64 hex characters.")
        rid = self.store.ins(
            "INSERT INTO repeaters(public_key, name, password, notes, added_at) VALUES (?,?,?,?,?)",
            (public_key.lower(), name.strip() or public_key[:8], password, notes, time.time()),
        )
        self.store.audit("repeater_add", {"public_key": public_key, "name": name})
        return rid

    def update(self, rid: int, **fields: Any) -> None:
        allowed = {k: v for k, v in fields.items() if k in ("name", "password", "notes", "enabled") and v is not None}
        if not allowed:
            return
        sets = ", ".join(f"{k} = ?" for k in allowed)
        self.store.x(f"UPDATE repeaters SET {sets} WHERE id = ?", [*allowed.values(), rid])
        self.store.audit("repeater_update", {"id": rid, "fields": sorted(k for k in allowed if k != "password")})

    def remove(self, rid: int) -> None:
        self.store.x("DELETE FROM repeaters WHERE id = ?", (rid,))
        self.store.audit("repeater_remove", {"id": rid})

    def overview(self) -> list[dict[str, Any]]:
        out = []
        now = time.time()
        for r in self.store.repeaters():
            last = self.store.latest_status(r["id"])
            ok = self.store.latest_status(r["id"], ok_only=True)
            node = self.store.one("SELECT name, lat, lon, last_advert, prefix FROM nodes WHERE public_key = ?",
                                  (r["public_key"],))
            fw = self.store.one("SELECT firmware FROM repeater_status WHERE repeater_id = ? AND firmware IS NOT NULL"
                                " ORDER BY polled_at DESC LIMIT 1", (r["id"],))
            alerts = self.store.q("SELECT kind, severity, message FROM alerts WHERE subject = ? AND resolved_at IS NULL",
                                  (r["public_key"],))
            out.append({**r, "last_poll": last, "last_ok": ok, "node": node,
                        "firmware": (fw or {}).get("firmware"), "alerts": alerts,
                        "last_heard": max([t for t in ((node or {}).get("last_advert"), (ok or {}).get("polled_at")) if t] or [0]) or None,
                        "now": now})
        return out

    # ---- polling ----------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="fleet")

    def poll_now(self) -> bool:
        if self.polling:
            return False
        self._kick.set()
        return True

    async def _loop(self) -> None:
        await asyncio.sleep(20)
        while True:
            try:
                if self.radio.connected and any(r["enabled"] for r in self.store.repeaters()):
                    await self.poll_round()
                self.alerts.evaluate()
                self.store.prune(self.cfg.packet_retention_days, self.cfg.fleet_history_days)
                if getattr(self, "field", None):
                    self.field.prune(self.cfg.packet_retention_days)
            except Exception:  # noqa: BLE001
                log.exception("fleet round failed")
            self.next_round = time.time() + self.cfg.poll_minutes * 60
            try:
                await asyncio.wait_for(self._kick.wait(), timeout=self.cfg.poll_minutes * 60)
            except asyncio.TimeoutError:
                pass
            self._kick.clear()

    async def poll_round(self) -> None:
        async with self._lock:
            self.polling = True
            self.hub.publish({"type": "fleet", "polling": True})
            try:
                reps = [r for r in self.store.repeaters(include_password=True) if r["enabled"]]
                for n, rep in enumerate(reps):
                    if n:
                        await asyncio.sleep(self.cfg.stagger_seconds)
                    await self.poll_one(rep)
                    self.alerts.evaluate()
                self.last_round = time.time()
            finally:
                self.polling = False
                self.hub.publish({"type": "fleet", "polling": False})

    async def poll_one(self, rep: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        key = rep["public_key"]
        row: dict[str, Any]
        try:
            if rep.get("password"):
                await self.radio.login(key, rep["password"])
            st = await self.radio.status(key)
            row = {"polled_at": now, **status_row(st)}
            last_fw = self.store.one("SELECT polled_at FROM repeater_status WHERE repeater_id = ? AND firmware IS NOT NULL"
                                     " ORDER BY polled_at DESC LIMIT 1", (rep["id"],))
            if rep.get("password") and (not last_fw or now - last_fw["polled_at"] > FIRMWARE_EVERY_S):
                await asyncio.sleep(2)
                try:
                    row["firmware"] = (await self.radio.command(key, "ver"))[:80]
                except RadioError:
                    pass
            last_nb = self.store.one("SELECT polled_at FROM repeater_status WHERE repeater_id = ? AND neighbours IS NOT NULL"
                                     " ORDER BY polled_at DESC LIMIT 1", (rep["id"],))
            if not last_nb or now - last_nb["polled_at"] > NEIGHBOURS_EVERY_S:
                await asyncio.sleep(2)
                try:
                    row["neighbours"] = await self.radio.neighbours(key)
                except RadioError:
                    pass
        except RadioError as exc:
            row = {"polled_at": now, "ok": 0, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001
            log.exception("poll of %s failed", rep["name"])
            row = {"polled_at": now, "ok": 0, "error": str(exc) or exc.__class__.__name__}
        self.store.add_status(rep["id"], row)
        self.hub.publish({"type": "fleet_status", "repeater_id": rep["id"], "ok": bool(row["ok"])})
        return row

    # ---- bulk settings ----------------------------------------------------
    @staticmethod
    def check_setting(key: str, value: str) -> None:
        pattern = BULK_SETTINGS.get(key)
        if pattern is None:
            raise ValueError(f"'{key}' can't be changed in bulk.")
        if not re.match(pattern, value.strip()):
            raise ValueError(f"'{value}' isn't a valid value for {key}.")

    async def bulk_preview(self, ids: list[int], key: str, value: str) -> list[dict[str, Any]]:
        """Read the current value from each repeater so the change can be shown as a diff."""
        self.check_setting(key, value)
        reps = {r["id"]: r for r in self.store.repeaters(include_password=True)}
        out = []
        for n, rid in enumerate(ids):
            rep = reps.get(rid)
            if rep is None:
                continue
            if n:
                await asyncio.sleep(self.cfg.stagger_seconds / 2)
            item = {"id": rid, "name": rep["name"], "key": key, "new": value.strip()}
            try:
                if not rep["password"]:
                    raise RadioError("No admin password saved for this repeater.")
                await self.radio.login(rep["public_key"], rep["password"])
                item["current"] = parse_get_reply(await self.radio.command(rep["public_key"], f"get {key}"))
                item["changes"] = item["current"] != item["new"]
            except RadioError as exc:
                item["error"] = str(exc)
            out.append(item)
        return out

    async def bulk_apply(self, ids: list[int], key: str, value: str, confirm: bool) -> list[dict[str, Any]]:
        if not confirm:
            raise ValueError("Bulk changes need confirm = true after you review the preview.")
        self.check_setting(key, value)
        reps = {r["id"]: r for r in self.store.repeaters(include_password=True)}
        out = []
        for n, rid in enumerate(ids):
            rep = reps.get(rid)
            if rep is None:
                continue
            if n:
                await asyncio.sleep(self.cfg.stagger_seconds / 2)
            item = {"id": rid, "name": rep["name"]}
            try:
                if not rep["password"]:
                    raise RadioError("No admin password saved for this repeater.")
                await self.radio.login(rep["public_key"], rep["password"])
                item["reply"] = await self.radio.command(rep["public_key"], f"set {key} {value.strip()}")
                item["ok"] = "err" not in item["reply"].lower()
            except RadioError as exc:
                item.update(ok=False, error=str(exc))
            out.append(item)
        self.store.audit("bulk_set", {"key": key, "value": value, "results": out})
        return out

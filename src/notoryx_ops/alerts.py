"""Alert rules and delivery to Discord or a webhook."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
import urllib.request
from typing import Any

from .config import Config
from .ingest import Hub
from .store import Store

log = logging.getLogger(__name__)


def version_tuple(v: str | None) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", v or "")
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def battery_falling(points: list[tuple[float, int]], window_s: float = 86400, drop_mv: int = 150) -> bool:
    """True when battery fell by drop_mv or more across the last window, with no recovery at the end."""
    if len(points) < 3:
        return False
    end_t = points[-1][0]
    recent = [p for p in points if p[0] >= end_t - window_s]
    if len(recent) < 3:
        return False
    first, last = recent[0][1], recent[-1][1]
    return first - last >= drop_mv and recent[-1][1] <= recent[-2][1]


class Alerts:
    def __init__(self, cfg: Config, store: Store, hub: Hub):
        self.cfg, self.store, self.hub = cfg, store, hub

    def _raise(self, kind: str, subject: str, severity: str, message: str) -> None:
        aid = self.store.raise_alert(kind, subject, severity, message)
        if aid:
            self.hub.publish({"type": "alert", "id": aid, "kind": kind, "subject": subject,
                              "severity": severity, "message": message})
            try:
                asyncio.get_running_loop().create_task(self.deliver(aid))
            except RuntimeError:
                pass  # no event loop (tests, command line); the alert stays undelivered

    def _clear(self, kind: str, subject: str) -> None:
        if self.store.resolve_alert(kind, subject):
            self.hub.publish({"type": "alert_resolved", "kind": kind, "subject": subject})

    def evaluate(self, now: float | None = None) -> None:
        now = now or time.time()
        cfg = self.cfg
        for rep in self.store.repeaters():
            if not rep["enabled"]:
                continue
            subj, name = rep["public_key"], rep["name"]
            node = self.store.one("SELECT last_seen FROM nodes WHERE public_key = ?", (subj,))
            ok = self.store.latest_status(rep["id"], ok_only=True)
            heard = max([t for t in ((node or {}).get("last_seen"), (ok or {}).get("polled_at")) if t] or [0])
            if heard == 0:
                pass  # never heard yet, nothing to compare
            elif now - heard > cfg.quiet_hours * 3600:
                hrs = round((now - heard) / 3600, 1)
                self._raise("quiet", subj, "danger", f"{name} hasn't been heard for {hrs} h")
            else:
                self._clear("quiet", subj)

            if ok and ok.get("battery_mv"):
                v = ok["battery_mv"] / 1000
                if v < cfg.low_battery_volts:
                    self._raise("low_battery", subj, "warning", f"{name} battery is {v:.2f} V")
                elif v >= cfg.low_battery_volts + 0.05:
                    self._clear("low_battery", subj)
                pts = [(r["polled_at"], r["battery_mv"]) for r in
                       self.store.status_history(rep["id"], now - 2 * 86400) if r["ok"] and r["battery_mv"]]
                if battery_falling(pts):
                    self._raise("battery_falling", subj, "warning", f"{name} battery keeps falling ({v:.2f} V)")
                else:
                    self._clear("battery_falling", subj)

            want = version_tuple(cfg.expected_firmware)
            fw = self.store.one(
                "SELECT firmware FROM repeater_status WHERE repeater_id = ? AND firmware IS NOT NULL"
                " ORDER BY polled_at DESC LIMIT 1", (rep["id"],))
            have = version_tuple((fw or {}).get("firmware"))
            if want and have and have < want:
                self._raise("firmware", subj, "info", f"{name} runs firmware {fw['firmware']}, expected {cfg.expected_firmware}")
            elif want and have:
                self._clear("firmware", subj)

        self.check_prefix_clashes(now)
        self.check_observers(now)

    def check_prefix_clashes(self, now: float) -> None:
        rows = self.store.q(
            "SELECT prefix, COUNT(*) AS n, GROUP_CONCAT(COALESCE(name, substr(public_key,1,8)), ', ') AS names"
            " FROM nodes WHERE node_type IN ('repeater','room') AND last_seen > ? GROUP BY prefix",
            (now - 7 * 86400,),
        )
        for r in rows:
            if r["n"] > 1:
                self._raise("prefix_clash", r["prefix"], "warning",
                            f"Prefix {r['prefix'].upper()} is used by {r['n']} repeaters: {r['names']}")
            else:
                self._clear("prefix_clash", r["prefix"])

    def check_observers(self, now: float) -> None:
        for o in self.store.q("SELECT * FROM observers"):
            if now - o["last_seen"] > 30 * 60 and now - o["first_seen"] > 30 * 60:
                self._raise("observer_quiet", o["name"], "warning", f"Observer {o['name']} has sent nothing for 30 min")
            else:
                self._clear("observer_quiet", o["name"])

    async def deliver(self, alert_id: int) -> None:
        a = self.store.one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
        if not a or not (self.cfg.discord_webhook or self.cfg.webhook_url):
            return
        ok = True
        if self.cfg.discord_webhook:
            icon = {"danger": "🔴", "warning": "🟠"}.get(a["severity"], "🔵")
            ok &= await asyncio.to_thread(
                post_json, self.cfg.discord_webhook, {"content": f"{icon} {self.cfg.name}: {a['message']}"}, None)
        if self.cfg.webhook_url:
            body = {"event": "alert", "station": self.cfg.name, **{k: a[k] for k in
                    ("id", "kind", "subject", "severity", "message", "created_at")}}
            ok &= await asyncio.to_thread(post_json, self.cfg.webhook_url, body, self.cfg.webhook_secret or None)
        if ok:
            self.store.x("UPDATE alerts SET delivered = 1 WHERE id = ?", (alert_id,))


def post_json(url: str, body: dict[str, Any], secret: str | None) -> bool:
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "notoryx-ops"}
    if secret:
        headers["X-Notoryx-Signature"] = "sha256=" + hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data, headers, method="POST"), timeout=10) as r:
            return 200 <= r.status < 300
    except Exception as exc:  # noqa: BLE001
        log.warning("alert delivery failed: %s", exc)
        return False

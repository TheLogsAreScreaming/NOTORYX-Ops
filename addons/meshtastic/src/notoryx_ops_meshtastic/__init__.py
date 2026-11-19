# SPDX-License-Identifier: GPL-3.0-only
"""Meshtastic add-on for NOTORYX Ops.

Adds the station's own Meshtastic radio, remote administration of Meshtastic nodes over the
air (PKI admin, firmware 2.5+), settings templates with a compliance check, favourites between
relays, and an optional channel bridge to MeshCore.

This package is GPL-3.0 because it uses the Meshtastic Python library. NOTORYX Ops itself is
MIT and works without it. Meshtastic® is a registered trademark of Meshtastic LLC; this
add-on is not affiliated with or endorsed by Meshtastic LLC.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

__version__ = "0.1.1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS mt_templates (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL COLLATE NOCASE,
  section TEXT NOT NULL,
  fields TEXT NOT NULL,
  created_at REAL NOT NULL
);
"""


class Jobs:
    """Long over-the-air work (checks and changes across several nodes), run one at a time."""

    def __init__(self):
        self.jobs: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def start(self, kind: str, total: int, work) -> dict[str, Any]:
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "kind": kind, "state": "queued", "done": 0, "total": total, "results": [],
               "started_at": time.time(), "finished_at": None}
        self.jobs[jid] = job
        while len(self.jobs) > 20:
            self.jobs.pop(next(iter(self.jobs)))

        async def run():
            async with self._lock:
                job["state"] = "running"
                try:
                    await work(job)
                    job["state"] = "done"
                except Exception as exc:  # noqa: BLE001
                    job["state"], job["error"] = "failed", str(exc)
                job["finished_at"] = time.time()

        asyncio.ensure_future(run())
        return job


class MeshtasticAddon:
    id = "meshtastic"
    name = "Meshtastic"
    version = __version__
    license = "GPL-3.0-only"

    def __init__(self, ctx, iface_factory=None):
        from .bridge import Bridge
        from .link import MeshtasticLink

        self.ctx = ctx
        self.station = ctx.station
        self.settings = ctx.settings
        self.settings.setdefault("transport", "none")
        self.store = self.station.store
        self.store.db.executescript(SCHEMA)
        self.link = MeshtasticLink(self.settings, on_text=self._on_text, iface_factory=iface_factory)
        self.bridge = Bridge(self.settings, self.station, self.link)
        self.jobs = Jobs()
        self.page_dir = str(Path(__file__).parent / "page")
        from .api import router

        self.router = router(self)

    def _on_text(self, packet: dict[str, Any]) -> None:
        self.bridge.on_meshtastic_text(packet)

    def start(self) -> None:
        self.link.start()
        self.bridge.start()

    def stop(self) -> None:
        self.bridge.stop()
        self.link.stop()

    def restart_link(self) -> None:
        self.link.stop()
        self.link.start()

    def info(self) -> dict[str, Any]:
        return {"radio": self.link.info(), "bridge": self.bridge.info()}

    def admin(self):
        from .admin import RemoteAdmin

        if not self.link.connected:
            raise RuntimeError("The station's Meshtastic radio isn't connected.")
        return RemoteAdmin(self.link.iface, timeout=float(self.settings.get("admin_timeout_s") or 45))

    # ---- templates -----------------------------------------------------------
    def templates(self) -> list[dict[str, Any]]:
        rows = self.store.q("SELECT * FROM mt_templates ORDER BY name")
        for r in rows:
            r["fields"] = json.loads(r["fields"])
        return rows

    def template(self, tid: int) -> dict[str, Any]:
        r = self.store.one("SELECT * FROM mt_templates WHERE id = ?", (tid,))
        if not r:
            raise LookupError("No such template.")
        r["fields"] = json.loads(r["fields"])
        return r

    def add_template(self, name: str, section: str, fields: dict[str, Any]) -> int:
        from .admin import coerce

        clean = {k: coerce(section, k, v) for k, v in fields.items()}
        if not clean:
            raise ValueError("A template needs at least one setting.")
        if self.store.one("SELECT 1 x FROM mt_templates WHERE name = ?", (name,)):
            raise ValueError("A template with that name already exists.")
        return self.store.ins("INSERT INTO mt_templates(name, section, fields, created_at) VALUES (?,?,?,?)",
                              (name, section, json.dumps(clean), time.time()))


def addon(ctx):
    """Entry point for NOTORYX Ops (group notoryx_ops.addons)."""
    return MeshtasticAddon(ctx)

# SPDX-License-Identifier: GPL-3.0-only
"""HTTP API for the Meshtastic add-on, served under /api/addons/meshtastic."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .admin import SECTIONS, AdminError, firmware_ok, redact
from .bridge import parse_pairs
from .link import TRANSPORTS

MESHTASTIC_UUID = "6ba1b218-15a8-461f-9fa8-5dcae273eafd"


async def scan_ble(timeout: float = 6) -> list[dict[str, Any]]:
    """Nearby Bluetooth Meshtastic radios, strongest first."""
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    out = []
    for dev, adv in found.values():
        name = adv.local_name or dev.name or ""
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if MESHTASTIC_UUID in uuids or "meshtastic" in name.lower():
            out.append({"address": dev.address, "name": name or dev.address, "rssi": adv.rssi})
    return sorted(out, key=lambda d: -(d["rssi"] or -999))


def usb_ports() -> list[dict[str, Any]]:
    """Serial ports that could be a radio."""
    from serial.tools import list_ports

    return [{"address": p.device, "name": " · ".join(x for x in (p.description, p.manufacturer) if x and x != "n/a") or p.device}
            for p in list_ports.comports()
            if not any(x in p.device for x in ("Bluetooth-Incoming", "BLTH", "debug-console"))]


class SettingsIn(BaseModel):
    transport: str | None = None
    serial_port: str | None = Field(default=None, max_length=200)
    tcp_host: str | None = Field(default=None, max_length=200)
    tcp_port: int | None = Field(default=None, ge=1, le=65535)
    ble_address: str | None = Field(default=None, max_length=100)
    bridge_enabled: bool | None = None
    bridge_pairs: list[str] | None = Field(default=None, max_length=8)
    bridge_per_minute: int | None = Field(default=None, ge=1, le=30)


class ChangeIn(BaseModel):
    changes: dict[str, Any] = Field(min_length=1, max_length=30)
    confirm: bool = False


class OwnerIn(BaseModel):
    long_name: str | None = Field(default=None, max_length=39)
    short_name: str | None = Field(default=None, max_length=4)
    confirm: bool = False


class FavoriteIn(BaseModel):
    target: int
    on: bool = True


class RebootIn(BaseModel):
    seconds: int = Field(default=10, ge=5, le=600)
    confirm: bool = False


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    section: str
    fields: dict[str, Any] = Field(min_length=1, max_length=30)


class NodesIn(BaseModel):
    nodes: list[int] = Field(min_length=1, max_length=25)
    confirm: bool = False


def router(addon) -> APIRouter:
    r = APIRouter()
    need = addon.ctx.require

    def admin():
        try:
            return addon.admin()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    async def call(fn, *a):
        try:
            return await asyncio.to_thread(fn, *a)
        except AdminError as exc:
            raise HTTPException(502, str(exc)) from exc

    def check_section(section: str) -> None:
        if section not in SECTIONS:
            raise HTTPException(400, f"Unknown settings section. Pick one of: {', '.join(SECTIONS)}.")

    async def ensure_pki(a, num: int) -> dict[str, Any]:
        meta = await call(a.metadata, num)
        if not firmware_ok(meta.get("firmware_version")):
            raise HTTPException(409, f"Remote admin needs firmware 2.5 or newer; this node runs {meta.get('firmware_version') or 'an unknown version'}.")
        return meta

    # ---- status and settings ------------------------------------------------------
    @r.get("/status")
    def status(request: Request):
        need(request, "viewer")
        s = addon.settings
        return {**addon.info(), "sections": SECTIONS,
                "settings": {k: s.get(k) for k in ("transport", "serial_port", "tcp_host", "tcp_port", "ble_address",
                                                   "bridge_enabled", "bridge_pairs", "bridge_per_minute")},
                "meshcore_channels": [c.name for c in addon.station.channels]}

    @r.put("/settings")
    async def put_settings(body: SettingsIn, request: Request):
        # async on purpose: reconnecting starts a task, which needs the station's event loop.
        need(request, "admin")
        s = addon.settings
        new = body.model_dump(exclude_none=True)
        if "transport" in new and new["transport"] not in TRANSPORTS:
            raise HTTPException(400, "Pick none, serial, tcp or ble.")
        for k in ("serial_port", "tcp_host", "ble_address"):
            if k in new and any(ord(c) < 32 for c in new[k]):
                raise HTTPException(400, f"{k} has odd characters.")
        if "bridge_pairs" in new:
            try:
                pairs = parse_pairs(new["bridge_pairs"])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            known = {c.name.lower() for c in addon.station.channels}
            missing = [c for _, c in pairs if c.lower() not in known]
            if missing:
                raise HTTPException(400, f"The station has no MeshCore channel called {missing[0]!r}.")
        link_keys = ("transport", "serial_port", "tcp_host", "tcp_port", "ble_address")
        relink = any(k in new and new[k] != s.get(k) for k in link_keys)
        s.update(new)
        addon.ctx.save_settings()
        addon.bridge.reset_limits()
        if relink:
            addon.restart_link()
        addon.station.store.audit("meshtastic_settings", {k: v for k, v in new.items()})
        return {"ok": True}

    @r.get("/radios")
    async def radios(request: Request, kind: str = "ble"):
        """Meshtastic radios the station can connect to: nearby Bluetooth ones or USB ports. Listening only."""
        need(request, "admin")
        try:
            return await (scan_ble() if kind == "ble" else asyncio.to_thread(usb_ports))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, f"Looking for radios failed: {exc}. Check that Bluetooth is on and allowed for NOTORYX Ops.") from exc

    @r.get("/nodes")
    def nodes(request: Request):
        need(request, "viewer")
        return addon.link.nodes()

    @r.get("/channels")
    def channels(request: Request):
        need(request, "viewer")
        return addon.link.channels()

    # ---- remote admin (every call goes over the air) -----------------------------------
    @r.get("/nodes/{num}/metadata")
    async def metadata(num: int, request: Request):
        need(request, "operator")
        meta = await call(admin().metadata, num)
        return {**meta, "remote_admin_ok": firmware_ok(meta.get("firmware_version"))}

    @r.get("/nodes/{num}/owner")
    async def owner(num: int, request: Request):
        need(request, "operator")
        return await call(admin().owner, num)

    @r.get("/nodes/{num}/config/{section}")
    async def read_config(num: int, section: str, request: Request):
        need(request, "operator")
        check_section(section)
        a = admin()
        await ensure_pki(a, num)
        return redact(section, await call(a.get, num, section))

    @r.post("/nodes/{num}/config/{section}")
    async def change_config(num: int, section: str, body: ChangeIn, request: Request):
        """Without confirm: read the node and show what would change. With confirm: write it."""
        who = need(request, "operator")
        check_section(section)
        a = admin()
        await ensure_pki(a, num)
        current = await call(a.get, num, section)
        try:
            plan = a.plan(current, section, body.changes)
        except AdminError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not body.confirm or not plan:
            return {"applied": False, "changes": plan}
        done = await call(a.set, num, section, current, body.changes)
        addon.station.store.audit("meshtastic_config", {"node": num, "section": section, "changes": done,
                                                        "by": (who or {}).get("username", "station")})
        return {"applied": True, "changes": done}

    @r.post("/nodes/{num}/owner")
    async def change_owner(num: int, body: OwnerIn, request: Request):
        who = need(request, "operator")
        if body.long_name is None and body.short_name is None:
            raise HTTPException(400, "Give a long name, a short name or both.")
        if not body.confirm:
            raise HTTPException(400, "Confirm the new name first.")
        a = admin()
        await ensure_pki(a, num)
        await call(a.set_owner, num, body.long_name, body.short_name)
        addon.station.store.audit("meshtastic_owner", {"node": num, "long": body.long_name, "short": body.short_name,
                                                       "by": (who or {}).get("username", "station")})
        return {"ok": True}

    @r.post("/nodes/{num}/favorite")
    async def favorite(num: int, body: FavoriteIn, request: Request):
        """Mark one relay as a favourite on another, so it is kept and preferred."""
        need(request, "operator")
        a = admin()
        await ensure_pki(a, num)
        await call(a.favorite, num, body.target, body.on)
        addon.station.store.audit("meshtastic_favorite", {"node": num, "target": body.target, "on": body.on})
        return {"ok": True}

    @r.post("/nodes/{num}/reboot")
    async def reboot(num: int, body: RebootIn, request: Request):
        need(request, "operator")
        if not body.confirm:
            raise HTTPException(400, "Confirm the reboot first.")
        a = admin()
        await ensure_pki(a, num)
        await call(a.reboot, num, body.seconds)
        addon.station.store.audit("meshtastic_reboot", {"node": num, "seconds": body.seconds})
        return {"ok": True}

    # ---- templates and compliance ---------------------------------------------------------
    @r.get("/templates")
    def templates(request: Request):
        need(request, "viewer")
        return addon.templates()

    @r.post("/templates")
    def add_template(body: TemplateIn, request: Request):
        need(request, "operator")
        check_section(body.section)
        if not re.fullmatch(r"[\w .-]{1,40}", body.name):
            raise HTTPException(400, "Template names use letters, numbers, spaces, dots and dashes.")
        try:
            tid = addon.add_template(body.name, body.section, body.fields)
        except (AdminError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": tid}

    @r.delete("/templates/{tid}")
    def delete_template(tid: int, request: Request):
        need(request, "operator")
        addon.station.store.x("DELETE FROM mt_templates WHERE id = ?", (tid,))
        return {"ok": True}

    def get_template(tid: int) -> dict[str, Any]:
        try:
            return addon.template(tid)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    @r.post("/templates/{tid}/check")
    async def check_template(tid: int, body: NodesIn, request: Request):
        """Compliance check: read each node and list where it differs from the template."""
        need(request, "operator")
        t = get_template(tid)
        a = admin()
        gap = float(addon.settings.get("stagger_s", 3))

        async def work(job):
            for i, num in enumerate(body.nodes):
                if i:
                    await asyncio.sleep(gap)  # airtime: one node at a time, with a pause
                try:
                    cur = await asyncio.to_thread(a.get, num, t["section"])
                    diffs = a.plan(cur, t["section"], t["fields"])
                    job["results"].append({"node": num, "ok": True, "compliant": not diffs, "differences": diffs})
                except AdminError as exc:
                    job["results"].append({"node": num, "ok": False, "error": str(exc)})
                job["done"] += 1

        return addon.jobs.start("check", len(body.nodes), work)

    @r.post("/templates/{tid}/apply")
    async def apply_template(tid: int, body: NodesIn, request: Request):
        who = need(request, "operator")
        if not body.confirm:
            raise HTTPException(400, "Run a check first, then confirm to apply.")
        t = get_template(tid)
        a = admin()
        gap = float(addon.settings.get("stagger_s", 3))

        async def work(job):
            for i, num in enumerate(body.nodes):
                if i:
                    await asyncio.sleep(gap)
                try:
                    cur = await asyncio.to_thread(a.get, num, t["section"])
                    done = await asyncio.to_thread(a.set, num, t["section"], cur, t["fields"])
                    job["results"].append({"node": num, "ok": True, "changed": done})
                except AdminError as exc:
                    job["results"].append({"node": num, "ok": False, "error": str(exc)})
                job["done"] += 1
            addon.station.store.audit("meshtastic_template", {"template": t["name"], "nodes": body.nodes,
                                                              "by": (who or {}).get("username", "station")})

        return addon.jobs.start("apply", len(body.nodes), work)

    @r.get("/jobs/{jid}")
    def job(jid: str, request: Request):
        need(request, "viewer")
        j = addon.jobs.jobs.get(jid)
        if not j:
            raise HTTPException(404, "No such job.")
        return j

    return r

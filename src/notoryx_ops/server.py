"""Web API, live updates and the dashboard pages."""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import re
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analytics, decoder, planning, settings
from .channels import decrypt_group_text
from .config import Config
from .fleet import BULK_SETTINGS
from .nodestate import is_stale
from .radio import RadioError
from .station import Station
from .store import PKT_NOT_IGNORED


def web_dir() -> Path | None:
    here = Path(__file__).parent
    for cand in (here / "web", here.parent.parent / "web" / "dist"):
        if (cand / "index.html").exists():
            return cand
    return None


class RepeaterIn(BaseModel):
    public_key: str = Field(min_length=64, max_length=64)
    name: str = Field(min_length=1, max_length=40)
    password: str = Field(default="", max_length=64)
    notes: str = Field(default="", max_length=500)


class RepeaterPatch(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    password: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=50)
    key: str
    value: str = Field(max_length=32)
    confirm: bool = False


class IgnoreIn(BaseModel):
    public_key: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    name: str | None = Field(default=None, max_length=60)


class DecodeIn(BaseModel):
    raw: str = Field(max_length=1200)


def create_app(cfg: Config, station: Station | None = None) -> FastAPI:
    st = station or Station(cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await st.start()
        try:
            yield
        finally:
            await st.stop()

    app = FastAPI(title="NOTORYX Ops", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.station = st

    def may_change(request: Request) -> None:
        """Changes need the admin token when one is set; without one, only this computer may change things."""
        token = cfg.admin_token
        if token:
            got = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(got.encode(), token.encode()):
                raise HTTPException(401, "This change needs the station's admin token.")
            return
        host = request.client.host if request.client else ""
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host in ("localhost", "testclient")
        if not local:
            raise HTTPException(403, "Changes only work from the station computer unless an admin token is set.")

    @app.exception_handler(RadioError)
    async def radio_error(_req, exc: RadioError):
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(ValueError)
    async def value_error(_req, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # ---- station ------------------------------------------------------------
    @app.get("/api/station")
    def station_info():
        return st.info()

    @app.post("/api/station/stop", dependencies=[Depends(may_change)])
    async def station_stop():
        """Stop the station process. The Mac app has no window, so the dashboard offers this."""
        asyncio.get_running_loop().call_later(0.5, os.kill, os.getpid(), signal.SIGINT)
        return {"stopping": True}

    @app.get("/api/overview")
    def overview():
        return analytics.overview(st.store)

    # ---- packets ------------------------------------------------------------
    @app.get("/api/packets")
    def packets(limit: int = 200, before: float | None = None, type: str | None = None,
                observer: str | None = None, since: float | None = None):
        return st.store.recent_packets(limit, before, type, observer, since)

    @app.get("/api/packets/{phash}")
    def packet_detail(phash: str):
        rows = st.store.q("SELECT * FROM packets WHERE hash = ? ORDER BY received_at", (phash,))
        if not rows:
            raise HTTPException(404, "Packet not found.")
        pkt = decoder.decode(rows[0]["raw"])
        msg = None
        if pkt.payload_type == "group_text":
            m = decrypt_group_text(bytes.fromhex(pkt.payload), st.channels)
            msg = m.__dict__ if m else None
        for r in rows:
            r["path"] = json.loads(r["path"])
        return {"decoded": pkt.to_dict(), "message": msg, "receptions": rows}

    @app.post("/api/decode")
    def decode(body: DecodeIn):
        try:
            pkt = decoder.decode(body.raw)
        except decoder.DecodeError as exc:
            raise HTTPException(400, f"Couldn't decode that packet: {exc}") from exc
        msg = None
        if pkt.payload_type == "group_text":
            m = decrypt_group_text(bytes.fromhex(pkt.payload), st.channels)
            msg = m.__dict__ if m else None
        return {"decoded": pkt.to_dict(), "message": msg}

    @app.get("/api/replay")
    def replay(hours: float = 24):
        hours = max(0.25, min(hours, 24 * 7))
        rows = st.store.q(
            "SELECT hash, MIN(received_at) t, ptype, route, path, node_key, src_hash, summary, COUNT(*) heard"
            f" FROM packets WHERE received_at >= ? AND {PKT_NOT_IGNORED} GROUP BY hash ORDER BY t",
            (time.time() - hours * 3600,))
        for r in rows:
            r["path"] = json.loads(r["path"])
        return rows

    # ---- nodes, channels, observers ---------------------------------------------
    def mark(rows: list[dict[str, Any]], key: str | None = "public_key") -> list[dict[str, Any]]:
        now = time.time()
        hidden = st.store.ignored_keys()
        for r in rows:
            r["stale"] = is_stale(r.get("last_seen"), now, cfg.stale_after_hours)
            if key:
                r["ignored"] = r.get(key) in hidden
        return rows

    @app.get("/api/nodes")
    def nodes(include_ignored: bool = False):
        return mark(st.store.nodes(include_ignored))

    # ---- ignore list ----------------------------------------------------------
    @app.get("/api/ignored")
    def ignored_list():
        return mark(st.store.ignored(), key=None)

    @app.post("/api/ignored", dependencies=[Depends(may_change)])
    def ignore_node(body: IgnoreIn):
        key = body.public_key.lower()
        added = st.store.ignore(key, body.name)
        cleared = st.alerts.clear_for_node(key)
        if added:
            st.store.audit("ignore_node", {"public_key": key})
        return {"ok": True, "added": added, "alerts_cleared": cleared}

    @app.delete("/api/ignored/{public_key}", dependencies=[Depends(may_change)])
    def unignore_node(public_key: str):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", public_key):
            raise HTTPException(400, "That isn't a node's full public key.")
        if not st.store.unignore(public_key):
            raise HTTPException(404, "That node isn't on the ignore list.")
        st.store.audit("unignore_node", {"public_key": public_key.lower()})
        return {"ok": True}

    @app.get("/api/messages")
    def messages(channel: str | None = None, limit: int = 200):
        if channel:
            return st.store.q("SELECT * FROM messages WHERE channel = ? ORDER BY received_at DESC LIMIT ?",
                              (channel, min(limit, 2000)))
        return st.store.q("SELECT * FROM messages ORDER BY received_at DESC LIMIT ?", (min(limit, 2000),))

    @app.get("/api/channels")
    def channels():
        counts = {r["channel"]: r for r in st.store.q(
            "SELECT channel, COUNT(*) n, MAX(received_at) last FROM messages GROUP BY channel")}
        heard = {r["channel_hash"]: r["n"] for r in st.store.q(
            "SELECT channel_hash, COUNT(DISTINCT hash) n FROM packets WHERE channel_hash IS NOT NULL"
            " AND received_at >= ? GROUP BY channel_hash", (time.time() - 86400,))}
        known = {c.hash for c in st.channels}
        return {
            "channels": [{"name": c.name, "hash": c.hash, "messages": (counts.get(c.name) or {}).get("n", 0),
                          "last": (counts.get(c.name) or {}).get("last")} for c in st.channels],
            "unknown": [{"hash": h, "packets_24h": n} for h, n in sorted(heard.items()) if h not in known],
        }

    @app.get("/api/observers")
    def observers():
        return mark(analytics.observer_stats(st.store))

    # ---- analytics ------------------------------------------------------------
    @app.get("/api/analytics/heatmap")
    def heatmap(days: int = 7, tz: int = 0):
        return analytics.heatmap(st.store, max(1, min(days, 30)), tz_offset_min=tz)

    @app.get("/api/analytics/top")
    def top(hours: int = 24):
        return analytics.top_nodes(st.store, max(1, min(hours, 24 * 14)))

    @app.get("/api/analytics/prefixes")
    def prefixes():
        return analytics.prefix_usage(st.store)

    @app.get("/api/links")
    def links(hours: int = 24):
        return analytics.links(st.store, max(1, min(hours, 24 * 14)))

    @app.get("/api/coverage")
    def coverage(size_m: int = 1500, days: int = 30):
        return analytics.coverage(st.store, max(250, min(size_m, 20000)), max(1, min(days, 365)))

    # ---- terrain planning -----------------------------------------------------
    app.include_router(planning.router(st.planner))

    @app.post("/api/terrain/calibrate", dependencies=[Depends(may_change)])
    def terrain_calibrate():
        from .terrain import TerrainError

        try:
            return st.planner.recalibrate()
        except TerrainError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ---- fleet ----------------------------------------------------------------
    @app.get("/api/fleet")
    def fleet():
        return st.fleet.overview()

    @app.post("/api/fleet", dependencies=[Depends(may_change)])
    def fleet_add(body: RepeaterIn):
        try:
            rid = st.fleet.add(body.public_key, body.name, body.password, body.notes)
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(409, "That repeater is already in your fleet.") from exc
            raise
        return {"id": rid}

    @app.patch("/api/fleet/{rid}", dependencies=[Depends(may_change)])
    def fleet_update(rid: int, body: RepeaterPatch):
        fields: dict[str, Any] = body.model_dump()
        if fields.get("enabled") is not None:
            fields["enabled"] = int(fields["enabled"])
        st.fleet.update(rid, **fields)
        return {"ok": True}

    @app.delete("/api/fleet/{rid}", dependencies=[Depends(may_change)])
    def fleet_remove(rid: int):
        st.fleet.remove(rid)
        return {"ok": True}

    @app.get("/api/fleet/{rid}/history")
    def fleet_history(rid: int, days: float = 7):
        return st.store.status_history(rid, time.time() - max(0.1, min(days, 365)) * 86400)

    @app.post("/api/fleet/poll", dependencies=[Depends(may_change)])
    def fleet_poll():
        if not st.radio.connected:
            raise HTTPException(503, "The station radio isn't connected.")
        return {"started": st.fleet.poll_now()}

    @app.get("/api/fleet/bulk/settings")
    def bulk_settings():
        return sorted(BULK_SETTINGS)

    @app.post("/api/fleet/bulk/preview", dependencies=[Depends(may_change)])
    async def bulk_preview(body: BulkIn):
        return await st.fleet.bulk_preview(body.ids, body.key, body.value)

    @app.post("/api/fleet/bulk/apply", dependencies=[Depends(may_change)])
    async def bulk_apply(body: BulkIn):
        return await st.fleet.bulk_apply(body.ids, body.key, body.value, body.confirm)

    # ---- settings -------------------------------------------------------------
    @app.get("/api/settings")
    def settings_get():
        return settings.public_view(cfg)

    @app.put("/api/settings", dependencies=[Depends(may_change)])
    async def settings_put(body: dict[str, Any]):
        restart = await st.apply_settings(body)
        return {"saved": True, "restart_needed": restart, "settings": settings.public_view(cfg)}

    @app.get("/api/settings/serial-ports", dependencies=[Depends(may_change)])
    def settings_serial():
        return settings.serial_ports()

    @app.get("/api/settings/ble-scan", dependencies=[Depends(may_change)])
    async def settings_ble():
        try:
            return await settings.ble_devices()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, f"Bluetooth scan failed: {exc}. Check that Bluetooth is on and allowed for NOTORYX Ops.") from exc

    # ---- alerts ---------------------------------------------------------------
    @app.post("/api/alerts/test", dependencies=[Depends(may_change)])
    async def alert_test():
        if not (cfg.discord_webhook or cfg.webhook_url):
            raise HTTPException(400, "Add a Discord webhook or a webhook URL first.")
        from .alerts import post_json

        out = {}
        if cfg.discord_webhook:
            out["discord"] = await asyncio.to_thread(
                post_json, cfg.discord_webhook, {"content": f"✅ {cfg.name}: test alert from NOTORYX Ops"}, None)
        if cfg.webhook_url:
            out["webhook"] = await asyncio.to_thread(
                post_json, cfg.webhook_url, {"event": "test", "station": cfg.name, "message": "Test alert from NOTORYX Ops"},
                cfg.webhook_secret or None)
        return out

    @app.get("/api/alerts")
    def alerts(open: bool = False, limit: int = 200):
        sql = "SELECT * FROM alerts"
        if open:
            sql += " WHERE resolved_at IS NULL"
        return st.store.q(sql + " ORDER BY created_at DESC LIMIT ?", (min(limit, 1000),))

    @app.post("/api/alerts/{aid}/resolve", dependencies=[Depends(may_change)])
    def alert_resolve(aid: int):
        st.store.x("UPDATE alerts SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL", (time.time(), aid))
        return {"ok": True}

    @app.post("/api/alerts/check", dependencies=[Depends(may_change)])
    def alert_check():
        st.alerts.evaluate()
        return {"ok": True}

    # ---- live updates ---------------------------------------------------------
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        q = st.hub.subscribe()
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=8)
                except asyncio.TimeoutError:
                    ev = {"type": "ping", "t": time.time()}
                await sock.send_json(ev)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            st.hub.unsubscribe(q)

    # ---- dashboard ------------------------------------------------------------
    wd = web_dir()
    if wd:
        app.mount("/assets", StaticFiles(directory=wd / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = (wd / path).resolve()
            if path and f.is_file() and wd.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(wd / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_web():
            return JSONResponse({"detail": "Dashboard files are missing. Build them with: cd web && npm ci && npm run build"})

    return app

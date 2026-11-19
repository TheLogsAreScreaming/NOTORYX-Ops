"""Web API, live updates and the dashboard pages."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analytics, decoder, integrations, planning, settings
from .auth import COOKIE, RANK, SESSION_DAYS, is_local
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


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=200)


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=10, max_length=200)
    role: str = "viewer"


class UserPatch(BaseModel):
    role: str | None = None
    password: str | None = Field(default=None, max_length=200)
    disabled: bool | None = None


class ReportIn(BaseModel):
    kind: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    note: str = Field(default="", max_length=200)
    send: bool = True
    channel: str = Field(default="Public", max_length=60)


class ClearIn(BaseModel):
    send: bool = True
    channel: str = Field(default="Public", max_length=60)


class TeamIn(BaseModel):
    name: str = Field(min_length=2, max_length=24)
    notes: str = Field(default="", max_length=200)
    add_to_radio: bool = False


class MembersIn(BaseModel):
    members: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


class SurveysIn(BaseModel):
    points: list[dict[str, Any]] = Field(max_length=5000)


class ImportIn(BaseModel):
    format: str = Field(pattern="^(csv|gpx)$")
    text: str = Field(max_length=5_000_000)


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

    auth = st.auth

    def operator(request: Request) -> None:
        auth.require(request, "operator")

    def admin(request: Request) -> None:
        auth.require(request, "admin")

    # Paths anyone may open even when accounts are on.
    open_paths = ("/api/auth/", "/api/public/")

    @app.middleware("http")
    async def need_sign_in(request: Request, call_next):
        path = request.url.path
        guarded = path.startswith("/api/") or path == "/metrics"
        if guarded and not path.startswith(open_paths) and auth.accounts_on():
            if path == "/metrics" and cfg.metrics_token:
                got = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                if hmac.compare_digest(got.encode(), cfg.metrics_token.encode()):
                    return await call_next(request)
            if auth.principal(request) is None:
                return JSONResponse({"detail": "Sign in first."}, status_code=401)
        return await call_next(request)

    # ---- accounts -------------------------------------------------------------
    @app.get("/api/auth/me")
    def auth_me(request: Request):
        who = auth.principal(request)
        on = auth.accounts_on()
        return {"accounts_on": on, "user": who, "local": is_local(request),
                "public_map": cfg.public_map,
                "can_change": bool(who and RANK[who["role"]] >= RANK["operator"]) if on
                else bool(who) or (not cfg.admin_token and is_local(request)),
                "is_admin": bool(who and who["role"] == "admin") if on
                else bool(who) or (not cfg.admin_token and is_local(request))}

    def set_cookie(request: Request, resp: JSONResponse, tok: str) -> JSONResponse:
        resp.set_cookie(COOKIE, tok, max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/")
        return resp

    @app.post("/api/auth/login")
    def auth_login(body: LoginIn, request: Request):
        tok, user = auth.login(request, body.username, body.password)
        st.store.audit("sign_in", {"user": user["username"]})
        return set_cookie(request, JSONResponse({"user": user}), tok)

    @app.post("/api/auth/logout")
    def auth_logout(request: Request):
        auth.logout(request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.post("/api/auth/setup")
    def auth_setup(body: LoginIn, request: Request):
        """Create the first admin account. Allowed only while there are none, under the old change rules."""
        if auth.accounts_on():
            raise HTTPException(409, "Accounts are already set up. Sign in as an admin to add more.")
        auth.require(request, "admin")
        auth.add_user(body.username, body.password, "admin")
        tok, user = auth.login(request, body.username, body.password)
        st.store.audit("accounts_on", {"user": user["username"]})
        return set_cookie(request, JSONResponse({"user": user}), tok)

    @app.get("/api/users", dependencies=[Depends(admin)])
    def users_list():
        return auth.users()

    @app.post("/api/users", dependencies=[Depends(admin)])
    def users_add(body: UserIn):
        uid = auth.add_user(body.username, body.password, body.role)
        st.store.audit("user_add", {"user": body.username, "role": body.role})
        return {"id": uid}

    @app.patch("/api/users/{uid}", dependencies=[Depends(admin)])
    def users_update(uid: int, body: UserPatch):
        auth.update_user(uid, body.role, body.password, body.disabled)
        st.store.audit("user_change", {"id": uid, "role": body.role, "disabled": body.disabled,
                                       "password": body.password is not None})
        return {"ok": True}

    @app.delete("/api/users/{uid}", dependencies=[Depends(admin)])
    def users_remove(uid: int):
        auth.remove_user(uid)
        st.store.audit("user_remove", {"id": uid})
        return {"ok": True}

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

    @app.post("/api/station/stop", dependencies=[Depends(admin)])
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

    @app.post("/api/ignored", dependencies=[Depends(operator)])
    def ignore_node(body: IgnoreIn):
        key = body.public_key.lower()
        added = st.store.ignore(key, body.name)
        cleared = st.alerts.clear_for_node(key)
        if added:
            st.store.audit("ignore_node", {"public_key": key})
        return {"ok": True, "added": added, "alerts_cleared": cleared}

    @app.delete("/api/ignored/{public_key}", dependencies=[Depends(operator)])
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

    # ---- exports, metrics, public map ------------------------------------------
    def download(body: str, name: str, media: str) -> Response:
        return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/export/nodes.geojson")
    def export_nodes_geojson(days: int = 30):
        return download(json.dumps(integrations.nodes_geojson(st.store, max(1, min(days, 365)))),
                        "notoryx-ops-nodes.geojson", "application/geo+json")

    @app.get("/api/export/links.geojson")
    def export_links_geojson(hours: int = 24):
        return download(json.dumps(integrations.links_geojson(st.store, max(1, min(hours, 24 * 14)))),
                        "notoryx-ops-links.geojson", "application/geo+json")

    @app.get("/api/export/nodes.gpx")
    def export_nodes_gpx(days: int = 30):
        return download(integrations.nodes_gpx(st.store, max(1, min(days, 365)), cfg.name),
                        "notoryx-ops-nodes.gpx", "application/gpx+xml")

    @app.get("/metrics", include_in_schema=False)
    def prom_metrics():
        return PlainTextResponse(integrations.metrics(st.store, st), media_type="text/plain; version=0.0.4")

    @app.get("/api/public/map")
    def public_map():
        if not cfg.public_map:
            raise HTTPException(404, "The public map is off.")
        return integrations.public_view(st.store, cfg)

    @app.post("/api/integrations/homeassistant/test", dependencies=[Depends(admin)])
    async def ha_test():
        if not (cfg.ha_enabled and cfg.ha_host):
            raise HTTPException(400, "Turn on Home Assistant and enter its MQTT broker first.")
        if st.ha.client is None:
            try:
                st.ha.client = await asyncio.to_thread(st.ha._connect)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(503, f"Couldn't reach the broker: {exc}") from exc
        return {"sent": st.ha.publish_once()}

    @app.post("/api/integrations/postgres/run", dependencies=[Depends(admin)])
    async def pg_run():
        if not cfg.pg_dsn:
            raise HTTPException(400, "Enter the PostgreSQL connection first.")
        try:
            n = await asyncio.to_thread(st.pg.run_once)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, f"PostgreSQL copy failed: {str(exc).splitlines()[0][:300]}") from exc
        st.pg.copied += n
        return {"copied": n}

    # ---- field work ---------------------------------------------------------------
    from . import field as fieldmod

    @app.get("/api/field/kinds")
    def field_kinds():
        return fieldmod.KINDS

    @app.get("/api/field/reports")
    def field_reports(hours: float = 24):
        return st.field.reports(max(0, min(hours, 24 * 30)))

    @app.get("/api/export/reports.geojson")
    def export_reports(hours: float = 24):
        return download(json.dumps(fieldmod.reports_geojson(st.field.reports(max(0, min(hours, 24 * 30))))),
                        "notoryx-ops-reports.geojson", "application/geo+json")

    def who(request: Request) -> str:
        p = auth.principal(request)
        return p["username"] if p else "station"

    @app.post("/api/field/reports", dependencies=[Depends(operator)])
    async def field_report_add(body: ReportIn, request: Request):
        text = fieldmod.format_report(body.kind, body.lat, body.lon, body.note)
        note = " ".join(body.note.split())
        ch = st.channel(body.channel) if body.send else None
        # Save before sending, so an echo heard back from a repeater is recognised as ours.
        rid = st.field.add_report(body.kind, round(body.lat, 5), round(body.lon, 5), note, "station", who(request),
                                  body.channel if body.send else None, sent=False)
        if ch:
            try:
                await st.radio.send_channel(ch.key, text)
            except Exception:
                st.store.x("DELETE FROM reports WHERE id = ?", (rid,))
                raise
            st.store.x("UPDATE reports SET sent = 1 WHERE id = ?", (rid,))
        st.store.audit("report_add", {"id": rid, "kind": body.kind, "sent": body.send, "by": who(request)})
        return {"id": rid, "text": text if body.send else None}

    @app.post("/api/field/reports/{rid}/clear", dependencies=[Depends(operator)])
    async def field_report_clear(rid: int, body: ClearIn, request: Request):
        r = st.store.one("SELECT * FROM reports WHERE id = ? AND cleared_at IS NULL", (rid,))
        if not r:
            raise HTTPException(404, "That report is already cleared or doesn't exist.")
        ch = st.channel(body.channel) if body.send else None
        st.field.clear_report(rid, who(request))  # first, so our own echo finds nothing left to clear
        if ch:
            try:
                await st.radio.send_channel(ch.key, fieldmod.format_report(r["kind"], r["lat"], r["lon"], clear=True))
            except Exception:
                st.store.x("UPDATE reports SET cleared_at = NULL, cleared_by = NULL WHERE id = ?", (rid,))
                raise
        st.store.audit("report_clear", {"id": rid, "sent": body.send, "by": who(request)})
        return {"ok": True}

    @app.get("/api/field/teams")
    def field_teams(request: Request):
        p = auth.principal(request)
        show_keys = (p and RANK[p["role"]] >= RANK["operator"]) or (not auth.accounts_on() and (not cfg.admin_token and is_local(request)))
        teams = st.field.teams()
        if not show_keys:
            for t in teams:
                t["channel_key"] = None
        return teams

    @app.post("/api/field/teams", dependencies=[Depends(operator)])
    async def field_team_add(body: TeamIn):
        t = st.field.add_team(body.name, body.notes)
        await st.apply_settings({"channels": [*cfg.channel_keys, f"{t['channel']}={t['channel_key']}"]})
        slot = None
        if body.add_to_radio:
            slot = await st.radio.add_channel(t["channel"], bytes.fromhex(t["channel_key"]))
        st.store.audit("team_add", {"team": t["name"], "radio_slot": slot})
        return {**t, "radio_slot": slot}

    @app.post("/api/field/teams/{tid}/radio", dependencies=[Depends(operator)])
    async def field_team_radio(tid: int):
        t = st.store.one("SELECT * FROM teams WHERE id = ?", (tid,))
        if not t:
            raise HTTPException(404, "No such team.")
        slot = await st.radio.add_channel(fieldmod.team_channel_name(t["name"]), bytes.fromhex(t["channel_key"]))
        return {"radio_slot": slot}

    @app.put("/api/field/teams/{tid}/members", dependencies=[Depends(operator)])
    def field_team_members(tid: int, body: MembersIn):
        st.field.set_members(tid, body.members)
        return {"ok": True}

    @app.delete("/api/field/teams/{tid}", dependencies=[Depends(operator)])
    async def field_team_remove(tid: int):
        t = st.field.remove_team(tid)
        if not t:
            raise HTTPException(404, "No such team.")
        spec = f"{fieldmod.team_channel_name(t['name'])}={t['channel_key']}"
        if spec in cfg.channel_keys:
            await st.apply_settings({"channels": [c for c in cfg.channel_keys if c != spec]})
        st.store.audit("team_remove", {"team": t["name"]})
        return {"ok": True, "note": "The channel stays on any radio it was added to; remove it there if you no longer need it."}

    @app.get("/api/field/tracks")
    def field_tracks(hours: float = 12, team_id: int | None = None):
        return st.field.tracks(max(0.5, min(hours, 24 * 7)), team_id)

    @app.get("/api/field/surveys")
    def field_surveys(days: int = 30):
        return st.field.surveys(max(1, min(days, 365)))

    @app.post("/api/field/surveys")
    def field_surveys_add(body: SurveysIn, request: Request):
        # Field crews may only have viewer accounts, so anyone who may look may add survey points.
        auth.require(request, "viewer")
        res = st.field.add_surveys(body.points, who(request))
        return res

    @app.post("/api/field/surveys/import", dependencies=[Depends(operator)])
    def field_surveys_import(body: ImportIn, request: Request):
        pts = fieldmod.parse_survey_csv(body.text) if body.format == "csv" else fieldmod.parse_survey_gpx(body.text)
        if not pts:
            raise HTTPException(400, "No points with a position found in that file.")
        res = st.field.add_surveys(pts, who(request))
        st.store.audit("survey_import", {"format": body.format, **res})
        return res

    # ---- terrain planning -----------------------------------------------------
    app.include_router(planning.router(st.planner))

    @app.post("/api/terrain/calibrate", dependencies=[Depends(operator)])
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

    @app.post("/api/fleet", dependencies=[Depends(operator)])
    def fleet_add(body: RepeaterIn):
        try:
            rid = st.fleet.add(body.public_key, body.name, body.password, body.notes)
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(409, "That repeater is already in your fleet.") from exc
            raise
        return {"id": rid}

    @app.patch("/api/fleet/{rid}", dependencies=[Depends(operator)])
    def fleet_update(rid: int, body: RepeaterPatch):
        fields: dict[str, Any] = body.model_dump()
        if fields.get("enabled") is not None:
            fields["enabled"] = int(fields["enabled"])
        st.fleet.update(rid, **fields)
        return {"ok": True}

    @app.delete("/api/fleet/{rid}", dependencies=[Depends(operator)])
    def fleet_remove(rid: int):
        st.fleet.remove(rid)
        return {"ok": True}

    @app.get("/api/fleet/{rid}/history")
    def fleet_history(rid: int, days: float = 7):
        return st.store.status_history(rid, time.time() - max(0.1, min(days, 365)) * 86400)

    @app.post("/api/fleet/poll", dependencies=[Depends(operator)])
    def fleet_poll():
        if not st.radio.connected:
            raise HTTPException(503, "The station radio isn't connected.")
        return {"started": st.fleet.poll_now()}

    @app.get("/api/fleet/bulk/settings")
    def bulk_settings():
        return sorted(BULK_SETTINGS)

    @app.post("/api/fleet/bulk/preview", dependencies=[Depends(operator)])
    async def bulk_preview(body: BulkIn):
        return await st.fleet.bulk_preview(body.ids, body.key, body.value)

    @app.post("/api/fleet/bulk/apply", dependencies=[Depends(operator)])
    async def bulk_apply(body: BulkIn):
        return await st.fleet.bulk_apply(body.ids, body.key, body.value, body.confirm)

    # ---- settings -------------------------------------------------------------
    @app.get("/api/settings")
    def settings_get():
        return settings.public_view(cfg)

    @app.put("/api/settings", dependencies=[Depends(admin)])
    async def settings_put(body: dict[str, Any]):
        restart = await st.apply_settings(body)
        return {"saved": True, "restart_needed": restart, "settings": settings.public_view(cfg)}

    @app.get("/api/settings/serial-ports", dependencies=[Depends(admin)])
    def settings_serial():
        return settings.serial_ports()

    @app.get("/api/settings/ble-scan", dependencies=[Depends(admin)])
    async def settings_ble():
        try:
            return await settings.ble_devices()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, f"Bluetooth scan failed: {exc}. Check that Bluetooth is on and allowed for NOTORYX Ops.") from exc

    # ---- alerts ---------------------------------------------------------------
    @app.post("/api/alerts/test", dependencies=[Depends(operator)])
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

    @app.post("/api/alerts/{aid}/resolve", dependencies=[Depends(operator)])
    def alert_resolve(aid: int):
        st.store.x("UPDATE alerts SET resolved_at = ? WHERE id = ? AND resolved_at IS NULL", (time.time(), aid))
        return {"ok": True}

    @app.post("/api/alerts/check", dependencies=[Depends(operator)])
    def alert_check():
        st.alerts.evaluate()
        return {"ok": True}

    # ---- live updates ---------------------------------------------------------
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        if auth.accounts_on() and auth.session_user(sock.cookies.get(COOKIE)) is None:
            await sock.close(code=4401)
            return
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

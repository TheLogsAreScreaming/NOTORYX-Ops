"""Getting data out: GeoJSON/GPX exports, Prometheus metrics, the public map,
Home Assistant (MQTT discovery) and a PostgreSQL archive."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any
from xml.sax.saxutils import escape

from . import analytics
from .config import Config
from .nodestate import is_stale
from .store import NODE_NOT_IGNORED, Store

log = logging.getLogger(__name__)


# ---- exports ------------------------------------------------------------------

def _nodes(store: Store, days: int) -> list[dict[str, Any]]:
    return store.q(f"SELECT * FROM nodes WHERE lat IS NOT NULL AND last_seen >= ? AND {NODE_NOT_IGNORED}"
                   " ORDER BY name", (time.time() - days * 86400,))


def nodes_geojson(store: Store, days: int = 30) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [n["lon"], n["lat"]]},
         "properties": {"name": n["name"], "public_key": n["public_key"], "type": n["node_type"],
                        "last_seen": n["last_seen"], "best_snr": n["best_snr"]}}
        for n in _nodes(store, days)]}


def links_geojson(store: Store, hours: int = 24) -> dict[str, Any]:
    pos = {n["public_key"]: n for n in store.q("SELECT public_key, name, lat, lon FROM nodes WHERE lat IS NOT NULL")}
    feats = []
    for lk in analytics.links(store, hours):
        a, b = pos.get(lk["a"]), pos.get(lk["b"])
        if a and b:
            feats.append({"type": "Feature",
                          "geometry": {"type": "LineString", "coordinates": [[a["lon"], a["lat"]], [b["lon"], b["lat"]]]},
                          "properties": {"a": a["name"], "b": b["name"], "count": lk["count"], "snr": lk["snr"]}})
    return {"type": "FeatureCollection", "features": feats}


def nodes_gpx(store: Store, days: int = 30, creator: str = "NOTORYX Ops") -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<gpx version="1.1" creator="{escape(creator)}" xmlns="http://www.topografix.com/GPX/1/1">']
    for n in _nodes(store, days):
        t = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(n["last_seen"]))
        out.append(f'  <wpt lat="{n["lat"]:.6f}" lon="{n["lon"]:.6f}"><time>{t}</time>'
                   f'<name>{escape(n["name"] or n["prefix"])}</name><type>{escape(n["node_type"] or "unknown")}</type></wpt>')
    out.append("</gpx>")
    return "\n".join(out) + "\n"


# ---- Prometheus --------------------------------------------------------------

def _label(v: Any) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def metrics(store: Store, station: Any) -> str:
    """Station numbers in the Prometheus text format."""
    ov = analytics.overview(store)
    lines: list[str] = []
    described: set[str] = set()

    def g(name: str, help_: str, value: Any, labels: dict[str, Any] | None = None, kind: str = "gauge") -> None:
        if value is None:
            return
        if name not in described:
            described.add(name)
            lines.append(f"# HELP {name} {help_}")
            lines.append(f"# TYPE {name} {kind}")
        lab = "{" + ",".join(f'{k}="{_label(v)}"' for k, v in labels.items()) + "}" if labels else ""
        lines.append(f"{name}{lab} {float(value):g}")

    g("notoryx_ops_up", "The station is running.", 1)
    g("notoryx_ops_radio_connected", "The station radio is connected.", int(station.radio.connected))
    g("notoryx_ops_packets_24h", "Packet receptions in the last 24 hours.", ov["packets_24h"])
    g("notoryx_ops_unique_packets_24h", "Distinct packets in the last 24 hours.", ov["unique_24h"])
    g("notoryx_ops_unique_packets_1h", "Distinct packets in the last hour.", ov["unique_1h"])
    g("notoryx_ops_nodes_24h", "Nodes heard in the last 24 hours.", ov["nodes_24h"])
    g("notoryx_ops_repeaters_24h", "Repeaters and rooms heard in the last 24 hours.", ov["repeaters_24h"])
    g("notoryx_ops_observers_live", "Observers heard in the last 30 minutes.", ov["observers_live"])
    g("notoryx_ops_prefix_clashes", "One-byte prefixes shared by more than one repeater.", ov["prefix_clashes"])
    for t in ov["types_24h"]:
        g("notoryx_ops_packets_by_type_24h", "Receptions in the last 24 hours by packet type.", t["n"], {"type": t["ptype"]})
    g("notoryx_ops_alerts_open", "Open alerts.", store.one("SELECT COUNT(*) n FROM alerts WHERE resolved_at IS NULL")["n"])
    for r in station.fleet.overview():
        lab = {"repeater": r["name"], "key": r["public_key"][:12]}
        ok = r.get("last_ok") or {}
        g("notoryx_ops_repeater_battery_volts", "Repeater battery from its last good status.",
          ok.get("battery_mv") / 1000 if ok.get("battery_mv") else None, lab)
        g("notoryx_ops_repeater_noise_floor_dbm", "Repeater noise floor.", ok.get("noise_floor"), lab)
        g("notoryx_ops_repeater_last_snr_db", "SNR of the last packet the repeater heard.", ok.get("last_snr"), lab)
        g("notoryx_ops_repeater_uptime_seconds", "Repeater uptime.", ok.get("uptime_s"), lab)
        g("notoryx_ops_repeater_last_heard_timestamp", "When the repeater was last heard (unix time).", r.get("last_heard"), lab)
    return "\n".join(lines) + "\n"


# ---- public map ------------------------------------------------------------------

def _blur(v: float, decimals: int) -> float:
    return round(v, decimals)


def public_view(store: Store, cfg: Config) -> dict[str, Any]:
    """What the public map shows: repeaters and rooms (and companions if allowed), positions rounded."""
    now = time.time()
    types = ("repeater", "room", "companion", "sensor") if cfg.public_show_companions else ("repeater", "room")
    rows = store.q(f"SELECT public_key, name, node_type, lat, lon, last_seen FROM nodes WHERE lat IS NOT NULL"
                   f" AND last_seen >= ? AND {NODE_NOT_IGNORED}", (now - 7 * 86400,))
    nodes = []
    keep = set()
    for r in rows:
        if r["node_type"] not in types:
            continue
        keep.add(r["public_key"])
        nodes.append({"id": r["public_key"][:8], "name": r["name"], "type": r["node_type"],
                      "lat": _blur(r["lat"], cfg.public_precision), "lon": _blur(r["lon"], cfg.public_precision),
                      "stale": is_stale(r["last_seen"], now, cfg.stale_after_hours)})
    pos = {r["public_key"]: r for r in rows if r["public_key"] in keep}
    links = []
    for lk in analytics.links(store, 24):
        a, b = pos.get(lk["a"]), pos.get(lk["b"])
        if a and b:
            links.append({"a": [_blur(a["lon"], cfg.public_precision), _blur(a["lat"], cfg.public_precision)],
                          "b": [_blur(b["lon"], cfg.public_precision), _blur(b["lat"], cfg.public_precision)],
                          "snr": lk["snr"]})
    ov = analytics.overview(store)
    return {"name": cfg.public_title or cfg.name, "nodes": nodes, "links": links,
            "stats": {"nodes_24h": ov["nodes_24h"], "repeaters_24h": ov["repeaters_24h"], "unique_24h": ov["unique_24h"]},
            "updated": now}


# ---- Home Assistant ---------------------------------------------------------------

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")[:40] or "x"


class HomeAssistant:
    """Publishes station and fleet sensors over MQTT with Home Assistant discovery."""

    def __init__(self, cfg: Config, store: Store, station: Any, client_factory=None):
        self.cfg = cfg
        self.store = store
        self.station = station
        self._factory = client_factory
        self.client = None
        self.state = "off"
        self.error: str | None = None
        self.last_publish: float | None = None
        self._task: asyncio.Task | None = None
        self._announced: set[str] = set()

    def info(self) -> dict[str, Any]:
        return {"enabled": self.cfg.ha_enabled, "state": self.state, "error": self.error, "last_publish": self.last_publish}

    @property
    def node_id(self) -> str:
        return "notoryx_ops_" + _slug(self.cfg.name)

    def start(self) -> None:
        if not self.cfg.ha_enabled or not self.cfg.ha_host:
            self.state = "off"
            return
        self._task = asyncio.create_task(self._run(), name="homeassistant")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.client:
            try:
                self.client.publish(f"{self.node_id}/status", "offline", retain=True)
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self.client = None
        self.state = "off"

    def _connect(self):
        if self._factory:
            return self._factory()
        import paho.mqtt.client as mqtt

        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{self.node_id}_{int(time.time())}")
        if self.cfg.ha_username:
            c.username_pw_set(self.cfg.ha_username, self.cfg.ha_password or None)
        if self.cfg.ha_tls:
            from .net import tls_context

            c.tls_set_context(tls_context())
        c.will_set(f"{self.node_id}/status", "offline", retain=True)
        c.connect(self.cfg.ha_host, self.cfg.ha_port, keepalive=60)
        c.loop_start()
        return c

    async def _run(self) -> None:
        while True:
            try:
                if self.client is None:
                    self.state = "connecting"
                    self.client = await asyncio.to_thread(self._connect)
                    self._announced.clear()
                self.publish_once()
                self.state, self.error = "connected", None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.state, self.error = "error", str(exc) or exc.__class__.__name__
                log.warning("Home Assistant publish failed: %s", self.error)
                self.client = None
            await asyncio.sleep(max(15, self.cfg.ha_interval_s))

    def _device(self) -> dict[str, Any]:
        from . import __version__

        return {"identifiers": [self.node_id], "name": self.cfg.name, "manufacturer": "NOTORYX",
                "model": "NOTORYX Ops station", "sw_version": __version__}

    def _sensor(self, uid: str, name: str, value: Any, unit: str | None = None, dev_class: str | None = None,
                device: dict[str, Any] | None = None, binary: bool = False, state_class: str | None = "measurement") -> None:
        comp = "binary_sensor" if binary else "sensor"
        state_topic = f"{self.node_id}/{uid}/state"
        if uid not in self._announced:
            cfg: dict[str, Any] = {"name": name, "unique_id": f"{self.node_id}_{uid}", "state_topic": state_topic,
                                   "availability_topic": f"{self.node_id}/status", "device": device or self._device()}
            if unit:
                cfg["unit_of_measurement"] = unit
            if dev_class:
                cfg["device_class"] = dev_class
            if binary:
                cfg.update({"payload_on": "ON", "payload_off": "OFF"})
            elif state_class:
                cfg["state_class"] = state_class
            self.client.publish(f"{self.cfg.ha_prefix}/{comp}/{self.node_id}/{uid}/config", json.dumps(cfg), retain=True)
            self._announced.add(uid)
        if binary:
            payload = "ON" if value else "OFF"
        else:
            payload = "unknown" if value is None else str(value)
        self.client.publish(state_topic, payload, retain=True)

    def publish_once(self) -> int:
        """Send every sensor once. Returns how many were sent."""
        c = self.client
        c.publish(f"{self.node_id}/status", "online", retain=True)
        ov = analytics.overview(self.store)
        sent = 0
        for uid, name, val in (("radio", "Radio connected", self.station.radio.connected),):
            self._sensor(uid, name, val, dev_class="connectivity", binary=True)
            sent += 1
        for uid, name, val, unit in (
            ("unique_1h", "Packets last hour", ov["unique_1h"], "packets"),
            ("unique_24h", "Packets last 24 h", ov["unique_24h"], "packets"),
            ("nodes_24h", "Nodes heard 24 h", ov["nodes_24h"], "nodes"),
            ("repeaters_24h", "Repeaters heard 24 h", ov["repeaters_24h"], "repeaters"),
            ("alerts_open", "Open alerts", self.store.one("SELECT COUNT(*) n FROM alerts WHERE resolved_at IS NULL")["n"], None),
        ):
            self._sensor(uid, name, val, unit)
            sent += 1
        now = time.time()
        for r in self.station.fleet.overview():
            ok = r.get("last_ok") or {}
            key = r["public_key"][:12]
            dev = {"identifiers": [f"{self.node_id}_rep_{key}"], "name": r["name"], "manufacturer": "MeshCore",
                   "model": "Repeater", "via_device": self.node_id}
            heard = r.get("last_heard")
            quiet = not heard or now - heard > self.cfg.quiet_hours * 3600
            self._sensor(f"rep_{key}_online", f"{r['name']} online", not quiet, dev_class="connectivity", device=dev, binary=True)
            mv = ok.get("battery_mv")
            self._sensor(f"rep_{key}_battery", f"{r['name']} battery", round(mv / 1000, 3) if mv else None, "V", "voltage", dev)
            self._sensor(f"rep_{key}_noise", f"{r['name']} noise floor", ok.get("noise_floor"), "dBm", "signal_strength", dev)
            self._sensor(f"rep_{key}_snr", f"{r['name']} last SNR", ok.get("last_snr"), "dB", None, dev)
            sent += 4
        self.last_publish = time.time()
        return sent


# ---- PostgreSQL archive ----------------------------------------------------------------

ARCHIVE_TABLES = {
    # table: (columns, extra SQL types for Postgres)
    "packets": ["id", "hash", "received_at", "observer", "source", "route", "ptype", "hops", "hash_size", "path",
                "snr", "rssi", "raw", "channel_hash", "src_hash", "dest_hash", "node_key", "summary"],
    "messages": ["id", "packet_hash", "received_at", "channel", "sender", "text", "sent_ts", "hops"],
    "repeater_status": ["id", "repeater_id", "polled_at", "ok", "error", "battery_mv", "noise_floor", "last_rssi",
                        "last_snr", "uptime_s", "airtime_s", "rx_airtime_s", "tx_queue", "recv", "sent", "flood_dups",
                        "direct_dups", "recv_errors", "firmware", "neighbours"],
    "alerts": ["id", "created_at", "kind", "subject", "severity", "message", "resolved_at", "delivered"],
}
PG_TYPES = {
    "id": "BIGINT PRIMARY KEY", "received_at": "DOUBLE PRECISION", "polled_at": "DOUBLE PRECISION",
    "created_at": "DOUBLE PRECISION", "resolved_at": "DOUBLE PRECISION", "snr": "REAL", "rssi": "REAL",
    "last_snr": "REAL", "hops": "INTEGER", "hash_size": "INTEGER", "repeater_id": "INTEGER", "ok": "INTEGER",
    "battery_mv": "INTEGER", "noise_floor": "INTEGER", "last_rssi": "INTEGER", "uptime_s": "BIGINT",
    "airtime_s": "BIGINT", "rx_airtime_s": "BIGINT", "tx_queue": "INTEGER", "recv": "BIGINT", "sent": "BIGINT",
    "flood_dups": "BIGINT", "direct_dups": "BIGINT", "recv_errors": "BIGINT", "sent_ts": "BIGINT", "delivered": "INTEGER",
}


def pg_schema() -> list[str]:
    out = []
    for t, cols in ARCHIVE_TABLES.items():
        defs = ", ".join(f"{c} {PG_TYPES.get(c, 'TEXT')}" for c in cols)
        out.append(f"CREATE TABLE IF NOT EXISTS ops_{t} ({defs})")
    out.append("CREATE INDEX IF NOT EXISTS ops_packets_time ON ops_packets(received_at)")
    return out


class PostgresArchive:
    """Copies new rows to PostgreSQL so history outlives the station's own retention.

    Rows are copied by id; alerts are re-copied when they change (resolved, delivered).
    The station keeps working if PostgreSQL is down and catches up later.
    """

    BATCH = 2000

    def __init__(self, cfg: Config, store: Store, connect=None):
        self.cfg = cfg
        self.store = store
        self._connect = connect
        self.state = "off"
        self.error: str | None = None
        self.copied = 0
        self.last_run: float | None = None
        self._task: asyncio.Task | None = None

    def info(self) -> dict[str, Any]:
        return {"enabled": self.cfg.pg_enabled, "state": self.state, "error": self.error,
                "copied": self.copied, "last_run": self.last_run}

    def _conn(self):
        if self._connect:
            return self._connect()
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgreSQL support isn't installed. Run: pip install 'notoryx-ops[postgres]'") from exc
        return psycopg.connect(self.cfg.pg_dsn, connect_timeout=10)

    def _mark(self, table: str) -> int:
        r = self.store.one("SELECT value FROM meta WHERE key = ?", (f"pg_mark_{table}",))
        return int(r["value"]) if r else 0

    def _set_mark(self, table: str, v: int) -> None:
        self.store.x("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (f"pg_mark_{table}", str(v)))

    def run_once(self) -> int:
        """Copy everything new. Returns the number of rows sent."""
        sent = 0
        with self._conn() as conn:
            cur = conn.cursor()
            for stmt in pg_schema():
                cur.execute(stmt)
            for table, cols in ARCHIVE_TABLES.items():
                mark = self._mark(table)
                while True:
                    rows = self.store.q(f"SELECT {','.join(cols)} FROM {table} WHERE id > ? ORDER BY id LIMIT ?",
                                        (mark, self.BATCH))
                    if not rows:
                        break
                    self._upsert(cur, table, cols, rows)
                    mark = rows[-1]["id"]
                    sent += len(rows)
                    conn.commit()
                    self._set_mark(table, mark)
                    if len(rows) < self.BATCH:
                        break
            # Alerts change after they are created.
            since = time.time() - 2 * 86400
            changed = self.store.q(f"SELECT {','.join(ARCHIVE_TABLES['alerts'])} FROM alerts WHERE resolved_at >= ?", (since,))
            if changed:
                self._upsert(cur, "alerts", ARCHIVE_TABLES["alerts"], changed)
                conn.commit()
        return sent

    @staticmethod
    def _upsert(cur, table: str, cols: list[str], rows: list[dict[str, Any]]) -> None:
        upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "id")
        sql = (f"INSERT INTO ops_{table} ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})"
               f" ON CONFLICT (id) DO UPDATE SET {upd}")
        cur.executemany(sql, [[r[c] for c in cols] for r in rows])

    def start(self) -> None:
        if self.cfg.pg_enabled and self.cfg.pg_dsn:
            self._task = asyncio.create_task(self._loop(), name="postgres")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        self.state = "off"

    async def _loop(self) -> None:
        while True:
            try:
                self.state = "copying"
                self.copied += await asyncio.to_thread(self.run_once)
                self.state, self.error = "ok", None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.state, self.error = "error", str(exc).split("\n")[0][:300]
                log.warning("PostgreSQL archive failed: %s", self.error)
            self.last_run = time.time()
            await asyncio.sleep(60)

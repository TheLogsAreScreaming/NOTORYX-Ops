"""SQLite storage for packets, nodes, observers, fleet history and alerts."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS packets (
  id INTEGER PRIMARY KEY,
  hash TEXT NOT NULL,
  received_at REAL NOT NULL,
  observer TEXT NOT NULL,
  source TEXT NOT NULL,
  route TEXT NOT NULL,
  ptype TEXT NOT NULL,
  hops INTEGER NOT NULL,
  hash_size INTEGER NOT NULL,
  path TEXT NOT NULL,
  snr REAL,
  rssi REAL,
  raw TEXT NOT NULL,
  channel_hash TEXT,
  src_hash TEXT,
  dest_hash TEXT,
  node_key TEXT,
  summary TEXT
);
CREATE INDEX IF NOT EXISTS packets_time ON packets(received_at);
CREATE INDEX IF NOT EXISTS packets_hash ON packets(hash);
CREATE INDEX IF NOT EXISTS packets_node ON packets(node_key);

CREATE TABLE IF NOT EXISTS nodes (
  public_key TEXT PRIMARY KEY,
  prefix TEXT NOT NULL,
  name TEXT,
  node_type TEXT,
  lat REAL,
  lon REAL,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL,
  advert_ts INTEGER,
  last_advert REAL,
  adverts INTEGER NOT NULL DEFAULT 0,
  best_snr REAL,
  direct_observers TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS nodes_prefix ON nodes(prefix);

CREATE TABLE IF NOT EXISTS observers (
  name TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  public_key TEXT,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL,
  packets INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  packet_hash TEXT UNIQUE NOT NULL,
  received_at REAL NOT NULL,
  channel TEXT NOT NULL,
  sender TEXT,
  text TEXT NOT NULL,
  sent_ts INTEGER,
  hops INTEGER
);
CREATE INDEX IF NOT EXISTS messages_time ON messages(received_at);

CREATE TABLE IF NOT EXISTS repeaters (
  id INTEGER PRIMARY KEY,
  public_key TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  password TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS repeater_status (
  id INTEGER PRIMARY KEY,
  repeater_id INTEGER NOT NULL REFERENCES repeaters(id) ON DELETE CASCADE,
  polled_at REAL NOT NULL,
  ok INTEGER NOT NULL,
  error TEXT,
  battery_mv INTEGER,
  noise_floor INTEGER,
  last_rssi INTEGER,
  last_snr REAL,
  uptime_s INTEGER,
  airtime_s INTEGER,
  rx_airtime_s INTEGER,
  tx_queue INTEGER,
  recv INTEGER,
  sent INTEGER,
  flood_dups INTEGER,
  direct_dups INTEGER,
  recv_errors INTEGER,
  firmware TEXT,
  neighbours TEXT
);
CREATE INDEX IF NOT EXISTS status_repeater ON repeater_status(repeater_id, polled_at);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY,
  created_at REAL NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT NOT NULL,
  severity TEXT NOT NULL,
  message TEXT NOT NULL,
  resolved_at REAL,
  delivered INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS alerts_open ON alerts(kind, subject, resolved_at);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY,
  at REAL NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        new = self.path == ":memory:" or not os.path.exists(self.path)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self._add_missing_columns()
        self.db.executescript(SCHEMA)
        self.db.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
        )
        if new and self.path != ":memory:":
            os.chmod(self.path, 0o600)

    def _add_missing_columns(self) -> None:
        """Bring an older database up to date before the schema script runs."""
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "nodes" in tables:
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(nodes)")}
            if "last_advert" not in cols:
                self.db.execute("ALTER TABLE nodes ADD COLUMN last_advert REAL")
                self.db.execute("UPDATE nodes SET last_advert = last_seen")

    # ---- helpers -------------------------------------------------------
    def q(self, sql: str, args: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql: str, args: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args: tuple | list = ()) -> int:
        """Run a statement and return the number of rows it changed."""
        with self._lock:
            return self.db.execute(sql, args).rowcount

    def ins(self, sql: str, args: tuple | list = ()) -> int:
        """Run an INSERT and return the new row id."""
        with self._lock:
            return self.db.execute(sql, args).lastrowid

    def audit(self, action: str, detail: dict[str, Any]) -> None:
        self.x("INSERT INTO audit(at, action, detail) VALUES (?,?,?)", (time.time(), action, json.dumps(detail)))

    # ---- packets -------------------------------------------------------
    def add_packet(self, row: dict[str, Any]) -> int:
        cols = list(row)
        return self.ins(
            f"INSERT INTO packets({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [json.dumps(row[c]) if c == "path" else row[c] for c in cols],
        )

    def seen_hash(self, h: str) -> int:
        r = self.one("SELECT COUNT(*) AS n FROM packets WHERE hash = ?", (h,))
        return r["n"] if r else 0

    def recent_packets(self, limit=200, before: float | None = None, ptype: str | None = None,
                       observer: str | None = None, since: float | None = None) -> list[dict[str, Any]]:
        where, args = [], []
        if before:
            where.append("received_at < ?")
            args.append(before)
        if since:
            where.append("received_at >= ?")
            args.append(since)
        if ptype:
            where.append("ptype = ?")
            args.append(ptype)
        if observer:
            where.append("observer = ?")
            args.append(observer)
        sql = "SELECT * FROM packets"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY received_at DESC LIMIT ?"
        args.append(min(int(limit), 5000))
        rows = self.q(sql, args)
        for r in rows:
            r["path"] = json.loads(r["path"])
        return rows

    def prune(self, packet_days: int, history_days: int) -> None:
        now = time.time()
        self.x("DELETE FROM packets WHERE received_at < ?", (now - packet_days * 86400,))
        self.x("DELETE FROM messages WHERE received_at < ?", (now - packet_days * 86400,))
        self.x("DELETE FROM repeater_status WHERE polled_at < ?", (now - history_days * 86400,))
        self.x("DELETE FROM alerts WHERE resolved_at IS NOT NULL AND resolved_at < ?", (now - history_days * 86400,))

    # ---- nodes ---------------------------------------------------------
    def upsert_node(self, key: str, name, node_type, lat, lon, advert_ts, now, snr, direct_observer=None):
        cur = self.one("SELECT * FROM nodes WHERE public_key = ?", (key,))
        if cur is None:
            obs = [direct_observer] if direct_observer else []
            self.x(
                "INSERT INTO nodes(public_key, prefix, name, node_type, lat, lon, first_seen, last_seen,"
                " advert_ts, last_advert, adverts, best_snr, direct_observers) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (key, key[:2], name, node_type, lat, lon, now, now, advert_ts, now, snr, json.dumps(obs)),
            )
            return
        obs = json.loads(cur["direct_observers"])
        if direct_observer and direct_observer not in obs:
            obs.append(direct_observer)
        best = cur["best_snr"]
        if snr is not None and (best is None or snr > best):
            best = snr
        self.x(
            "UPDATE nodes SET name = COALESCE(?, name), node_type = COALESCE(?, node_type),"
            " lat = COALESCE(?, lat), lon = COALESCE(?, lon), last_seen = MAX(last_seen, ?),"
            " last_advert = MAX(COALESCE(last_advert, 0), ?), advert_ts = MAX(COALESCE(advert_ts,0), ?),"
            " adverts = adverts + 1, best_snr = ?, direct_observers = ? WHERE public_key = ?",
            (name, node_type, lat, lon, now, now, advert_ts or 0, best, json.dumps(obs), key),
        )

    def touch_prefix(self, prefix: str, now: float) -> None:
        """Mark a node as probably heard when only its hash shows up (as a sender or a hop).

        One byte can belong to several nodes, so this only feeds the map's "last heard",
        never the fleet's quiet check, which uses full-key adverts and status polls.
        """
        rows = self.q("SELECT public_key FROM nodes WHERE prefix = ?", (prefix[:2],))
        if len(rows) == 1:
            self.x("UPDATE nodes SET last_seen = MAX(last_seen, ?) WHERE public_key = ?", (now, rows[0]["public_key"]))

    def nodes(self) -> list[dict[str, Any]]:
        rows = self.q("SELECT * FROM nodes ORDER BY last_seen DESC")
        for r in rows:
            r["direct_observers"] = json.loads(r["direct_observers"])
        return rows

    # ---- observers -----------------------------------------------------
    def touch_observer(self, name: str, source: str, now: float, public_key: str | None = None) -> None:
        self.x(
            "INSERT INTO observers(name, source, public_key, first_seen, last_seen, packets) VALUES (?,?,?,?,?,1)"
            " ON CONFLICT(name) DO UPDATE SET last_seen = excluded.last_seen, packets = packets + 1,"
            " public_key = COALESCE(excluded.public_key, public_key)",
            (name, source, public_key, now, now),
        )

    # ---- messages ------------------------------------------------------
    def add_message(self, packet_hash, now, channel, sender, text, sent_ts, hops) -> bool:
        with self._lock:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO messages(packet_hash, received_at, channel, sender, text, sent_ts, hops)"
                " VALUES (?,?,?,?,?,?,?)",
                (packet_hash, now, channel, sender, text, sent_ts, hops),
            )
            return cur.rowcount > 0

    # ---- fleet ---------------------------------------------------------
    def repeaters(self, include_password=False) -> list[dict[str, Any]]:
        rows = self.q("SELECT * FROM repeaters ORDER BY name COLLATE NOCASE")
        for r in rows:
            r["has_password"] = bool(r["password"])
            if not include_password:
                r.pop("password")
        return rows

    def add_status(self, repeater_id: int, row: dict[str, Any]) -> None:
        row = {**row, "repeater_id": repeater_id}
        if isinstance(row.get("neighbours"), list):
            row["neighbours"] = json.dumps(row["neighbours"])
        cols = list(row)
        self.x(
            f"INSERT INTO repeater_status({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [row[c] for c in cols],
        )

    def status_history(self, repeater_id: int, since: float) -> list[dict[str, Any]]:
        rows = self.q(
            "SELECT * FROM repeater_status WHERE repeater_id = ? AND polled_at >= ? ORDER BY polled_at",
            (repeater_id, since),
        )
        for r in rows:
            r["neighbours"] = json.loads(r["neighbours"]) if r["neighbours"] else None
        return rows

    def latest_status(self, repeater_id: int, ok_only=False) -> dict[str, Any] | None:
        sql = "SELECT * FROM repeater_status WHERE repeater_id = ?"
        if ok_only:
            sql += " AND ok = 1"
        r = self.one(sql + " ORDER BY polled_at DESC LIMIT 1", (repeater_id,))
        if r and r["neighbours"]:
            r["neighbours"] = json.loads(r["neighbours"])
        return r

    # ---- alerts --------------------------------------------------------
    def open_alert(self, kind: str, subject: str) -> dict[str, Any] | None:
        return self.one(
            "SELECT * FROM alerts WHERE kind = ? AND subject = ? AND resolved_at IS NULL", (kind, subject)
        )

    def raise_alert(self, kind, subject, severity, message) -> int | None:
        if self.open_alert(kind, subject):
            return None
        return self.ins(
            "INSERT INTO alerts(created_at, kind, subject, severity, message) VALUES (?,?,?,?,?)",
            (time.time(), kind, subject, severity, message),
        )

    def resolve_alert(self, kind, subject) -> bool:
        return bool(self.x(
            "UPDATE alerts SET resolved_at = ? WHERE kind = ? AND subject = ? AND resolved_at IS NULL",
            (time.time(), kind, subject),
        ))

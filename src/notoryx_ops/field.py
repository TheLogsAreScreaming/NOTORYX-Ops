"""Field work: hazard reports over the mesh, teams on private channels, position tracks
and offline field surveys.

Reports travel as ordinary channel messages that any MeshCore app can read, for example

    ⚑ flood 44.26012,-72.57501 Water over the road at the bridge

The station turns messages in that form into map markers, and sends its own the same way.
A clear message ("⚑ clear flood 44.26012,-72.57501") closes the nearest open report of that
kind within 150 m.
"""

from __future__ import annotations

import csv
import io
import math
import re
import secrets
import time
import xml.etree.ElementTree as ET
from typing import Any

from .store import Store

KINDS = {
    "flood": "Flooded road",
    "tree": "Fallen tree",
    "power": "Power line down",
    "blocked": "Road blocked",
    "fire": "Fire or smoke",
    "medical": "Medical help needed",
    "hazard": "Other hazard",
    "ok": "All clear here",
}
MARK = "⚑"
REPORT_RE = re.compile(rf"^\s*{MARK}\s*(clear\s+)?([a-z]+)\s+(-?\d{{1,2}}\.\d+),\s*(-?\d{{1,3}}\.\d+)(?:\s+(.*))?$", re.S)
POS_RE = re.compile(rf"^\s*{MARK}\s*pos\s+(-?\d{{1,2}}\.\d+),\s*(-?\d{{1,3}}\.\d+)\s*$")
MAX_NOTE = 80
EARTH_M = 6371000.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY,
  created_at REAL NOT NULL,
  kind TEXT NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL,          -- 'station' or 'mesh'
  sender TEXT,
  channel TEXT,
  sent INTEGER NOT NULL DEFAULT 0,
  cleared_at REAL,
  cleared_by TEXT
);
CREATE INDEX IF NOT EXISTS reports_open ON reports(cleared_at, created_at);

CREATE TABLE IF NOT EXISTS teams (
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL COLLATE NOCASE,
  channel_key TEXT NOT NULL,
  notes TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS team_members (
  team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  public_key TEXT NOT NULL,
  label TEXT,
  PRIMARY KEY (team_id, public_key)
);

CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY,
  public_key TEXT,
  sender TEXT,
  at REAL NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  source TEXT NOT NULL           -- 'advert' or 'message'
);
CREATE INDEX IF NOT EXISTS positions_key ON positions(public_key, at);
CREATE INDEX IF NOT EXISTS positions_sender ON positions(sender, at);

CREATE TABLE IF NOT EXISTS surveys (
  id INTEGER PRIMARY KEY,
  client_id TEXT UNIQUE,
  taken_at REAL NOT NULL,
  lat REAL NOT NULL,
  lon REAL NOT NULL,
  accuracy_m REAL,
  heard INTEGER,                 -- 1 heard, 0 not heard, NULL unknown
  snr REAL,
  rssi REAL,
  via TEXT,                      -- what was heard (repeater name or prefix)
  note TEXT NOT NULL DEFAULT '',
  who TEXT,
  imported_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS surveys_time ON surveys(taken_at);
"""


def distance_m(a_lat, a_lon, b_lat, b_lon) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(min(1.0, h)))


def _pos_ok(lat: float, lon: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def format_report(kind: str, lat: float, lon: float, note: str = "", clear: bool = False) -> str:
    if kind not in KINDS:
        raise ValueError("Pick a report type.")
    if not _pos_ok(lat, lon):
        raise ValueError("That position isn't on the map.")
    note = " ".join((note or "").split())
    if len(note) > MAX_NOTE:
        raise ValueError(f"Keep the note under {MAX_NOTE} characters so it fits in one mesh message.")
    text = f"{MARK} {'clear ' if clear else ''}{kind} {lat:.5f},{lon:.5f}"
    return f"{text} {note}" if note and not clear else text


def parse_report(text: str) -> dict[str, Any] | None:
    m = REPORT_RE.match(text or "")
    if not m:
        return None
    clear, kind, lat, lon, note = m.groups()
    kind = kind.lower()
    if kind not in KINDS:
        return None
    lat, lon = float(lat), float(lon)
    if not _pos_ok(lat, lon):
        return None
    return {"clear": bool(clear), "kind": kind, "lat": lat, "lon": lon, "note": (note or "").strip()[:MAX_NOTE]}


def parse_position(text: str) -> tuple[float, float] | None:
    m = POS_RE.match(text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if _pos_ok(lat, lon) else None


class Field:
    def __init__(self, store: Store, hub=None):
        self.store = store
        self.hub = hub
        store.db.executescript(SCHEMA)

    def _publish(self, ev: dict[str, Any]) -> None:
        if self.hub:
            self.hub.publish(ev)

    # ---- reports ------------------------------------------------------------
    def reports(self, include_cleared_hours: float = 24) -> list[dict[str, Any]]:
        return self.store.q("SELECT * FROM reports WHERE cleared_at IS NULL OR cleared_at >= ? ORDER BY created_at DESC",
                            (time.time() - include_cleared_hours * 3600,))

    def add_report(self, kind, lat, lon, note, source, sender=None, channel=None, sent=False, at=None) -> int:
        rid = self.store.ins(
            "INSERT INTO reports(created_at, kind, lat, lon, note, source, sender, channel, sent) VALUES (?,?,?,?,?,?,?,?,?)",
            (at or time.time(), kind, lat, lon, note, source, sender, channel, int(sent)))
        self._publish({"type": "report", "id": rid})
        return rid

    def clear_report(self, rid: int, by: str) -> dict[str, Any] | None:
        r = self.store.one("SELECT * FROM reports WHERE id = ? AND cleared_at IS NULL", (rid,))
        if r:
            self.store.x("UPDATE reports SET cleared_at = ?, cleared_by = ? WHERE id = ?", (time.time(), by, rid))
            self._publish({"type": "report", "id": rid})
        return r

    def _recent_same(self, kind, lat, lon, note, within_s=900) -> bool:
        for r in self.store.q("SELECT * FROM reports WHERE kind = ? AND created_at >= ?", (kind, time.time() - within_s)):
            if r["note"] == note and distance_m(r["lat"], r["lon"], lat, lon) < 5:
                return True
        return False

    # ---- incoming channel messages ----------------------------------------------
    def on_message(self, channel: str, sender: str | None, text: str, at: float) -> str | None:
        """Look at a decrypted channel message. Returns 'report', 'clear', 'position' or None."""
        rep = parse_report(text)
        if rep:
            if rep["clear"]:
                for r in self.store.q("SELECT * FROM reports WHERE kind = ? AND cleared_at IS NULL", (rep["kind"],)):
                    if distance_m(r["lat"], r["lon"], rep["lat"], rep["lon"]) <= 150:
                        self.store.x("UPDATE reports SET cleared_at = ?, cleared_by = ? WHERE id = ?",
                                     (at, sender or "mesh", r["id"]))
                        self._publish({"type": "report", "id": r["id"]})
                        return "clear"
                return None
            # Our own report heard back from a repeater, or the same message heard twice.
            if self._recent_same(rep["kind"], rep["lat"], rep["lon"], rep["note"]):
                return None
            self.add_report(rep["kind"], rep["lat"], rep["lon"], rep["note"], "mesh", sender, channel, at=at)
            return "report"
        pos = parse_position(text)
        if pos and sender:
            self.store.ins("INSERT INTO positions(public_key, sender, at, lat, lon, source) VALUES (?,?,?,?,?,?)",
                           (self._key_for_sender(sender), sender, at, pos[0], pos[1], "message"))
            self._publish({"type": "position", "sender": sender})
            return "position"
        return None

    def _key_for_sender(self, sender: str) -> str | None:
        rows = self.store.q("SELECT public_key FROM nodes WHERE name = ?", (sender,))
        return rows[0]["public_key"] if len(rows) == 1 else None

    def on_advert(self, public_key: str, lat: float | None, lon: float | None, at: float) -> None:
        """Keep a track of where a node has been (a new point when it moved more than 25 m)."""
        if lat is None or lon is None or not _pos_ok(lat, lon):
            return
        last = self.store.one("SELECT lat, lon FROM positions WHERE public_key = ? ORDER BY at DESC LIMIT 1", (public_key,))
        if last and distance_m(last["lat"], last["lon"], lat, lon) < 25:
            return
        self.store.ins("INSERT INTO positions(public_key, sender, at, lat, lon, source) VALUES (?,?,?,?,?,?)",
                       (public_key, None, at, lat, lon, "advert"))

    def prune(self, days: int) -> None:
        cut = time.time() - days * 86400
        self.store.x("DELETE FROM positions WHERE at < ?", (cut,))
        self.store.x("DELETE FROM reports WHERE cleared_at IS NOT NULL AND cleared_at < ?", (cut,))

    # ---- teams --------------------------------------------------------------------
    def teams(self) -> list[dict[str, Any]]:
        out = []
        for t in self.store.q("SELECT * FROM teams ORDER BY name"):
            members = self.store.q(
                "SELECT m.public_key, COALESCE(m.label, n.name) AS name, n.lat, n.lon, n.last_seen, n.node_type"
                " FROM team_members m LEFT JOIN nodes n ON n.public_key = m.public_key WHERE m.team_id = ?", (t["id"],))
            out.append({**t, "channel": team_channel_name(t["name"]), "members": members})
        return out

    def add_team(self, name: str, notes: str = "") -> dict[str, Any]:
        name = " ".join((name or "").split())
        if not re.fullmatch(r"[A-Za-z0-9 _.-]{2,24}", name):
            raise ValueError("Team names are 2-24 letters, numbers, spaces, dots, dashes or underscores.")
        if self.store.one("SELECT 1 x FROM teams WHERE name = ?", (name,)):
            raise ValueError("A team with that name already exists.")
        key = secrets.token_bytes(16).hex()
        tid = self.store.ins("INSERT INTO teams(name, channel_key, notes, created_at) VALUES (?,?,?,?)",
                             (name, key, notes[:200], time.time()))
        return {"id": tid, "name": name, "channel_key": key, "channel": team_channel_name(name)}

    def remove_team(self, tid: int) -> dict[str, Any] | None:
        t = self.store.one("SELECT * FROM teams WHERE id = ?", (tid,))
        if t:
            self.store.x("DELETE FROM teams WHERE id = ?", (tid,))
        return t

    def set_members(self, tid: int, members: list[dict[str, Any]]) -> None:
        if not self.store.one("SELECT 1 x FROM teams WHERE id = ?", (tid,)):
            raise ValueError("No such team.")
        self.store.x("DELETE FROM team_members WHERE team_id = ?", (tid,))
        for m in members[:50]:
            key = str(m.get("public_key", "")).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", key):
                raise ValueError("Members are picked from nodes the station has heard.")
            self.store.x("INSERT OR IGNORE INTO team_members(team_id, public_key, label) VALUES (?,?,?)",
                         (tid, key, (m.get("label") or None)))

    def tracks(self, hours: float = 12, team_id: int | None = None) -> list[dict[str, Any]]:
        """Recent positions per node (team members only when a team is given)."""
        since = time.time() - hours * 3600
        if team_id:
            keys = [r["public_key"] for r in self.store.q("SELECT public_key FROM team_members WHERE team_id = ?", (team_id,))]
            names = [r["name"] for r in self.store.q(
                "SELECT n.name FROM team_members m JOIN nodes n ON n.public_key = m.public_key WHERE m.team_id = ?"
                " AND n.name IS NOT NULL", (team_id,))]
            if not keys:
                return []
            rows = self.store.q(
                f"SELECT * FROM positions WHERE at >= ? AND (public_key IN ({','.join('?' * len(keys))})"
                f"{' OR sender IN (' + ','.join('?' * len(names)) + ')' if names else ''}) ORDER BY at",
                [since, *keys, *names])
        else:
            rows = self.store.q("SELECT * FROM positions WHERE at >= ? ORDER BY at", (since,))
        by: dict[str, dict[str, Any]] = {}
        names_by_key = {r["public_key"]: r["name"] for r in self.store.q("SELECT public_key, name FROM nodes")}
        for r in rows:
            k = r["public_key"] or f"name:{r['sender']}"
            t = by.setdefault(k, {"id": k, "name": names_by_key.get(r["public_key"]) or r["sender"], "points": []})
            t["points"].append([r["lon"], r["lat"], r["at"]])
        return [t for t in by.values() if t["points"]]

    # ---- surveys --------------------------------------------------------------------
    def add_surveys(self, points: list[dict[str, Any]], who: str | None) -> dict[str, int]:
        added = skipped = 0
        now = time.time()
        for p in points[:5000]:
            try:
                lat, lon = float(p["lat"]), float(p["lon"])
                at = float(p.get("taken_at") or now)
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue
            if not _pos_ok(lat, lon) or at > now + 3600 or at < now - 5 * 365 * 86400:
                skipped += 1
                continue
            heard = p.get("heard")
            heard = None if heard is None or heard == "" else int(str(heard).lower() in ("1", "true", "yes", "y"))
            snr = _num(p.get("snr"))
            if snr is not None and heard is None:
                heard = 1
            cid = str(p.get("client_id") or "")[:64] or None
            n = self.store.x(
                "INSERT OR IGNORE INTO surveys(client_id, taken_at, lat, lon, accuracy_m, heard, snr, rssi, via, note, who,"
                " imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, at, lat, lon, _num(p.get("accuracy_m")), heard, snr, _num(p.get("rssi")),
                 (str(p.get("via") or "")[:40] or None), str(p.get("note") or "")[:200], who, now))
            if n:
                added += 1
            else:
                skipped += 1
        if added:
            self._publish({"type": "survey", "added": added})
        return {"added": added, "skipped": skipped}

    def surveys(self, days: int = 30) -> list[dict[str, Any]]:
        return self.store.q("SELECT * FROM surveys WHERE taken_at >= ? ORDER BY taken_at DESC",
                            (time.time() - days * 86400,))


def team_channel_name(name: str) -> str:
    return f"team-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}"[:31]


def _num(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


# ---- imports -----------------------------------------------------------------------

def parse_survey_csv(text: str) -> list[dict[str, Any]]:
    """CSV with a header row. Needs lat and lon; time, heard, snr, rssi, via, note are optional."""
    rows = list(csv.DictReader(io.StringIO(text)))
    out = []
    for r in rows:
        low = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        lat = low.get("lat") or low.get("latitude")
        lon = low.get("lon") or low.get("lng") or low.get("longitude")
        if not lat or not lon:
            continue
        t = low.get("time") or low.get("timestamp") or low.get("taken_at")
        out.append({"lat": lat, "lon": lon, "taken_at": _parse_time(t), "heard": low.get("heard"),
                    "snr": low.get("snr"), "rssi": low.get("rssi"), "via": low.get("via") or low.get("repeater"),
                    "note": low.get("note") or low.get("notes") or ""})
    return out


def parse_survey_gpx(text: str) -> list[dict[str, Any]]:
    """Waypoints and track points from a GPX file. An <snr> or <desc>snr=..</desc> is read when present."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"That isn't a readable GPX file: {exc}") from exc
    out = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag not in ("wpt", "trkpt"):
            continue
        p: dict[str, Any] = {"lat": el.get("lat"), "lon": el.get("lon")}
        for ch in el:
            ct = ch.tag.split("}")[-1]
            if ct == "time":
                p["taken_at"] = _parse_time(ch.text)
            elif ct in ("name", "desc", "cmt"):
                txt = ch.text or ""
                m = re.search(r"snr\s*[=:]\s*(-?\d+(?:\.\d+)?)", txt, re.I)
                if m:
                    p["snr"] = m.group(1)
                if ct == "name":
                    p["note"] = txt[:200]
            elif ct == "snr":
                p["snr"] = ch.text
        out.append(p)
    return out


def _parse_time(t: str | None) -> float | None:
    if not t:
        return None
    t = t.strip()
    try:
        return float(t)
    except ValueError:
        pass
    from datetime import datetime

    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def reports_geojson(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
         "properties": {k: r[k] for k in ("id", "kind", "note", "created_at", "sender", "cleared_at")} | {"label": KINDS[r["kind"]]}}
        for r in rows]}



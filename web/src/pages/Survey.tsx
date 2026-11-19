import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

/** Phone-sized field survey. Every spot is saved on this device first and uploaded when the
 *  station can be reached, so it works with no signal. */

type Point = {
  client_id: string;
  taken_at: number;
  lat: number;
  lon: number;
  accuracy_m: number | null;
  heard: boolean;
  snr: number | null;
  via: string;
  note: string;
};

const KEY = "notoryx-ops-survey-queue";
const DONE_KEY = "notoryx-ops-survey-sent";

function load(k: string): Point[] {
  try {
    return JSON.parse(localStorage.getItem(k) || "[]");
  } catch {
    return [];
  }
}

function save(k: string, v: Point[]) {
  try {
    localStorage.setItem(k, JSON.stringify(v));
  } catch {
    /* storage full or blocked: points stay in memory for this visit */
  }
}

function id() {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function Survey() {
  const [queue, setQueue] = useState<Point[]>(() => load(KEY));
  const [sent, setSent] = useState<number>(() => load(DONE_KEY).length);
  const [fix, setFix] = useState<GeolocationPosition | null>(null);
  const [gpsErr, setGpsErr] = useState("");
  const [snr, setSnr] = useState("");
  const [via, setVia] = useState("");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState("");
  const [online, setOnline] = useState(navigator.onLine);
  const [reached, setReached] = useState<boolean | null>(null);
  const syncing = useRef(false);

  useEffect(() => {
    if (!navigator.geolocation) {
      setGpsErr("This browser can't share its location.");
      return;
    }
    const w = navigator.geolocation.watchPosition(
      (p) => (setFix(p), setGpsErr("")),
      (e) => setGpsErr(e.code === 1
        ? window.isSecureContext
          ? "Location is blocked for this page. Allow location for it in the browser's site settings."
          : "This page isn't https, so the browser won't share your location. Open the station through https (a reverse proxy or Tailscale) to survey from a phone."
        : "Waiting for a GPS fix…"),
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 30000 },
    );
    return () => navigator.geolocation.clearWatch(w);
  }, []);

  const sync = useCallback(async () => {
    if (syncing.current) return;
    const q = load(KEY);
    if (!q.length) {
      fetch("/api/auth/me", { cache: "no-store" }).then((r) => setReached(r.ok)).catch(() => setReached(false));
      return;
    }
    syncing.current = true;
    setStatus("Uploading…");
    try {
      const r = await api<any>("/api/field/surveys", { method: "POST", body: { points: q } });
      const done = load(DONE_KEY).concat(q).slice(-500);
      save(DONE_KEY, done);
      save(KEY, []);
      setQueue([]);
      setSent(done.length);
      setStatus(`Uploaded ${q.length} (${r.added} new).`);
      setReached(true);
    } catch (e: any) {
      setStatus(`Saved on this phone; will upload when the station can be reached (${e.message}).`);
      setReached(false);
    } finally {
      syncing.current = false;
    }
  }, []);

  useEffect(() => {
    const on = () => (setOnline(true), sync());
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    const t = setInterval(sync, 30000);
    sync();
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
      clearInterval(t);
    };
  }, [sync]);

  function record(heard: boolean) {
    if (!fix) return;
    const p: Point = {
      client_id: id(),
      taken_at: Date.now() / 1000,
      lat: +fix.coords.latitude.toFixed(6),
      lon: +fix.coords.longitude.toFixed(6),
      accuracy_m: fix.coords.accuracy ? Math.round(fix.coords.accuracy) : null,
      heard,
      snr: heard && snr !== "" && !Number.isNaN(Number(snr)) ? Number(snr) : null,
      via: via.trim().slice(0, 40),
      note: note.trim().slice(0, 200),
    };
    const q = [...load(KEY), p];
    save(KEY, q);
    setQueue(q);
    setNote("");
    setSnr("");
    setStatus(heard ? "Saved: heard here." : "Saved: nothing heard here.");
    if ("vibrate" in navigator) navigator.vibrate?.(40);
    sync();
  }

  const acc = fix?.coords.accuracy;
  return (
    <div className="survey">
      <header className="row">
        <strong style={{ flex: 1 }}>Field survey</strong>
        <span className={`pill ${online && reached !== false ? "ok" : "warn"}`}>
          {!online ? "No signal" : reached === false ? "Station out of reach" : reached ? "Station reachable" : "Checking…"}
        </span>
        <a href="/field" className="small">Done</a>
      </header>

      <div className="card">
        {fix ? (
          <div className="mono">
            {fix.coords.latitude.toFixed(5)}, {fix.coords.longitude.toFixed(5)}
            <span className={`pill ${acc && acc <= 30 ? "ok" : "warn"}`} style={{ marginLeft: 8 }}>±{Math.round(acc || 0)} m</span>
          </div>
        ) : (
          <div className="muted">{gpsErr || "Waiting for a GPS fix…"}</div>
        )}
      </div>

      <div className="card stack">
        <label className="field">
          <span className="flabel">SNR shown on your radio (optional)</span>
          <input inputMode="decimal" value={snr} onChange={(e) => setSnr(e.target.value)} placeholder="-4.5" />
        </label>
        <label className="field">
          <span className="flabel">Heard through (optional)</span>
          <input value={via} onChange={(e) => setVia(e.target.value)} placeholder="repeater name" />
        </label>
        <label className="field">
          <span className="flabel">Note (optional)</span>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="inside the car, hilltop…" />
        </label>
      </div>

      <div className="bigbtns">
        <button className="primary" disabled={!fix} onClick={() => record(true)}>✓ Heard here</button>
        <button className="danger" disabled={!fix} onClick={() => record(false)}>✗ Nothing here</button>
      </div>

      <div className="card small">
        <div>{queue.length} waiting to upload · {sent} uploaded from this phone</div>
        {status && <div className="muted" role="status" style={{ marginTop: 4 }}>{status}</div>}
        {queue.length > 0 && <button style={{ marginTop: 8 }} onClick={sync}>Upload now</button>}
      </div>
    </div>
  );
}

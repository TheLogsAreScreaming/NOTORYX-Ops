import { useEffect, useRef, useState } from "react";
import { useApi, useConnected, useLive } from "../api";
import LiveMap from "./LiveMap";

/** Rough charge left from a single LiPo cell's voltage. Reads high while charging. */
export function batteryPercent(mv: number | null | undefined): number | null {
  if (!mv || mv < 2500 || mv > 4500) return null;
  const curve: [number, number][] = [[3300, 0], [3500, 5], [3600, 10], [3700, 25], [3750, 35], [3800, 45],
    [3850, 55], [3900, 62], [4000, 75], [4100, 88], [4200, 100]];
  if (mv <= curve[0][0]) return 0;
  for (let i = 1; i < curve.length; i++) {
    const [v1, p1] = curve[i], [v0, p0] = curve[i - 1];
    if (mv <= v1) return Math.round(p0 + ((mv - v0) / (v1 - v0)) * (p1 - p0));
  }
  return 100;
}

type Dim = "night" | "always" | "never";
const DIM_KEY = "notoryx-ops-kiosk-dim";
const IDLE_MS = 2 * 60 * 1000;

function loadDim(): Dim {
  const q = new URLSearchParams(location.search).get("dim");
  if (q === "night" || q === "always" || q === "never") return q;
  try {
    const v = localStorage.getItem(DIM_KEY);
    if (v === "night" || v === "always" || v === "never") return v;
  } catch {
    /* private window */
  }
  return "night";
}

function isNight(d = new Date()) {
  const h = d.getHours();
  return h >= 22 || h < 6;
}

/** A full screen live map for a wall or desk screen: big status bar, dims at night, back to live when left alone. */
export default function Kiosk() {
  const live = useConnected();
  const { data: info } = useApi<any>("/api/station", 30000);
  const { data: health } = useApi<any>("/api/station/radio", 120000);
  const { data: ov } = useApi<any>("/api/overview", 60000);
  const { data: alerts } = useApi<any[]>("/api/alerts?open=true", 60000);
  const [now, setNow] = useState(() => new Date());
  const [dim, setDim] = useState<Dim>(loadDim);
  const [awake, setAwake] = useState(true);
  const [mapKey, setMapKey] = useState(0);
  const [menu, setMenu] = useState(false);
  const recent = useRef<number[]>([]);
  const [perMin, setPerMin] = useState(0);
  const touched = useRef(false);

  useLive((ev) => {
    if (ev.type === "packet" && !ev.repeat) recent.current.push(Date.now());
  });

  useEffect(() => {
    const t = setInterval(() => {
      const n = Date.now();
      recent.current = recent.current.filter((x) => n - x < 60000);
      setPerMin(recent.current.length);
      setNow(new Date());
    }, 5000);
    return () => clearInterval(t);
  }, []);

  // Any touch wakes the screen; two minutes without one puts the map back to live and lets it dim again.
  useEffect(() => {
    let timer = 0;
    const wake = () => {
      setAwake(true);
      touched.current = true;
      clearTimeout(timer);
      timer = window.setTimeout(() => {
        setAwake(false);
        setMenu(false);
        if (touched.current) setMapKey((k) => k + 1);
        touched.current = false;
      }, IDLE_MS);
    };
    wake();
    touched.current = false;
    for (const e of ["pointerdown", "keydown", "wheel"]) window.addEventListener(e, wake, { passive: true });
    return () => {
      clearTimeout(timer);
      for (const e of ["pointerdown", "keydown", "wheel"]) window.removeEventListener(e, wake);
    };
  }, []);

  function chooseDim(d: Dim) {
    setDim(d);
    try {
      localStorage.setItem(DIM_KEY, d);
    } catch {
      /* private window */
    }
  }

  const dimmed = !awake && (dim === "always" || (dim === "night" && isNight(now)));
  const pct = batteryPercent(health?.battery_mv);
  const radio = info?.radio?.state;

  return (
    <div className="kiosk">
      <header className="kiosk-bar" aria-label="Station status">
        <strong className="ell">{info?.name || "NOTORYX Ops"}</strong>
        <span className="kiosk-stat">
          <span className="dot" style={{ background: live && radio === "connected" ? "var(--ok)" : "var(--bad)" }} />
          {radio === "connected" ? "Radio on" : radio ? `Radio ${radio}` : "…"}
        </span>
        {pct != null && (
          <span className="kiosk-stat" title={`${(health.battery_mv / 1000).toFixed(2)} V, a guide only; reads high while charging`}
            style={{ color: pct < 20 ? "var(--bad)" : undefined }}>
            Battery {pct}%
          </span>
        )}
        <span className="kiosk-stat">{perMin} / min</span>
        {ov && <span className="kiosk-stat">{ov.nodes_24h} nodes today</span>}
        {alerts && alerts.length > 0 && <span className="pill warn">{alerts.length} alert{alerts.length > 1 ? "s" : ""}</span>}
        <span style={{ flex: 1 }} />
        <span className="kiosk-clock mono">{now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</span>
        <button className="kiosk-btn" onClick={() => setMenu((m) => !m)} aria-expanded={menu} aria-controls="kiosk-menu">Menu</button>
      </header>
      {menu && (
        <div className="kiosk-menu card" id="kiosk-menu" role="dialog" aria-label="Screen settings">
          <div className="small muted" style={{ marginBottom: 6 }}>Dim the screen when nobody has touched it for 2 minutes</div>
          <div className="row" role="radiogroup" aria-label="Dimming">
            {([["night", "At night (10 pm to 6 am)"], ["always", "Always"], ["never", "Never"]] as [Dim, string][]).map(([d, l]) => (
              <button key={d} role="radio" aria-checked={dim === d} className={`kiosk-btn ${dim === d ? "primary" : ""}`} onClick={() => chooseDim(d)}>{l}</button>
            ))}
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="kiosk-btn" onClick={() => { setMapKey((k) => k + 1); setMenu(false); }}>Back to live map</button>
            <button className="kiosk-btn" onClick={() => location.assign("/")}>Full dashboard</button>
          </div>
        </div>
      )}
      <main className="main full kiosk-map">
        <LiveMap key={mapKey} />
      </main>
      <div className="kiosk-dim" aria-hidden="true" style={{ opacity: dimmed ? 0.72 : 0 }} />
    </div>
  );
}

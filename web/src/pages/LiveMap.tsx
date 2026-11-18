import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { Map as MlMap, GeoJSONSource } from "maplibre-gl";
import MapView, { fitTo } from "../components/MapView";
import { currentTheme, nodeColor, onTheme, routeColor, snrColor } from "../themes";
import { Node, Packet, TYPE_LABEL, TYPE_PILL, ago, api, clock, makeResolver, useApi, useLive } from "../api";

const ROUTE_MS = 2600;
const SPEEDS = [1, 10, 60, 300];
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

type Route = { coords: [number, number][]; born: number; color: string };
type ReplayRow = Pick<Packet, "hash" | "ptype" | "route" | "path" | "node_key" | "src_hash" | "summary"> & { t: number; heard: number };

function partial(coords: [number, number][], f: number): [number, number][] {
  if (f >= 1 || coords.length < 2) return coords;
  const seg: number[] = [];
  let total = 0;
  for (let i = 1; i < coords.length; i++) {
    const d = Math.hypot(coords[i][0] - coords[i - 1][0], coords[i][1] - coords[i - 1][1]);
    seg.push(d);
    total += d;
  }
  let want = total * f;
  const out: [number, number][] = [coords[0]];
  for (let i = 1; i < coords.length; i++) {
    if (want >= seg[i - 1]) {
      out.push(coords[i]);
      want -= seg[i - 1];
    } else {
      const k = seg[i - 1] ? want / seg[i - 1] : 0;
      out.push([coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * k, coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * k]);
      break;
    }
  }
  return out;
}

export default function LiveMap() {
  const { data: nodes } = useApi<Node[]>("/api/nodes", 60000);
  const { data: fleet } = useApi<any[]>("/api/fleet", 60000);
  const { data: links } = useApi<any[]>("/api/links", 120000);
  const [feed, setFeed] = useState<Packet[]>([]);
  const [mode, setMode] = useState<"live" | "replay">("live");
  const [replay, setReplay] = useState<ReplayRow[] | null>(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(60);
  const [paused, setPaused] = useState(false);
  const [ready, setReady] = useState(false);
  const [, setLook] = useState(0);
  useEffect(() => onTheme(() => setLook((x) => x + 1)), []);
  const map = useRef<MlMap | null>(null);
  const routes = useRef<Route[]>([]);
  const pings = useRef<{ at: [number, number]; born: number; color: string }[]>([]);
  const fitted = useRef(false);

  const resolve = useMemo(() => makeResolver(nodes || []), [nodes]);
  const byKey = useMemo(() => new Map((nodes || []).map((n) => [n.public_key, n])), [nodes]);
  const fleetKeys = useMemo(() => new Map((fleet || []).map((f) => [f.public_key, f])), [fleet]);

  useEffect(() => {
    api<Packet[]>("/api/packets?limit=40").then(setFeed).catch(() => {});
  }, []);

  function animate(p: { ptype: string; path: string[]; node_key: string | null; src_hash: string | null }) {
    const pts: [number, number][] = [];
    const origin = p.node_key ? byKey.get(p.node_key) : resolve(p.src_hash);
    if (origin?.lat != null && origin.lon != null) pts.push([origin.lon, origin.lat]);
    if (p.ptype !== "trace") {
      for (const h of p.path) {
        const n = resolve(h);
        if (n?.lat != null && n.lon != null) {
          const last = pts[pts.length - 1];
          if (!last || last[0] !== n.lon || last[1] !== n.lat) pts.push([n.lon, n.lat]);
        }
      }
    }
    const color = routeColor(p.ptype);
    const now = performance.now();
    if (pts.length >= 2) routes.current.push({ coords: pts, born: now, color });
    else if (pts.length === 1) pings.current.push({ at: pts[0], born: now, color });
    if (routes.current.length > 200) routes.current.splice(0, routes.current.length - 200);
  }

  useLive((ev) => {
    if (ev.type !== "packet" || mode !== "live") return;
    const p = ev as unknown as Packet;
    if (!paused) setFeed((f) => [p, ...f].slice(0, 60));
    if (!p.repeat) animate(p);
  });

  // Static layers: links and nodes.
  function drawStatic(m: MlMap) {
    const located = (nodes || []).filter((n) => n.lat != null && n.lon != null);
    const nodeFc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: located.map((n) => {
        const f = fleetKeys.get(n.public_key);
        const down = f?.alerts?.some((a: any) => a.kind === "quiet");
        return {
          type: "Feature",
          geometry: { type: "Point", coordinates: [n.lon!, n.lat!] },
          properties: {
            key: n.public_key,
            name: n.name || n.public_key.slice(0, 8),
            type: n.node_type || "unknown",
            color: nodeColor(n.node_type),
            owned: f ? (down ? 2 : 1) : 0,
            big: n.node_type === "repeater" || n.node_type === "room" ? 1 : 0,
          },
        };
      }),
    };
    const linkFc: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: (links || [])
        .map((l) => {
          const a = byKey.get(l.a);
          const b = byKey.get(l.b);
          if (a?.lat == null || b?.lat == null) return null;
          return {
            type: "Feature",
            geometry: { type: "LineString", coordinates: [[a.lon!, a.lat!], [b.lon!, b.lat!]] },
            properties: { color: snrColor(l.snr), weak: l.snr != null && l.snr < -5 ? 1 : 0, w: Math.min(4, 1 + Math.log2(1 + l.count)) },
          } as GeoJSON.Feature;
        })
        .filter(Boolean) as GeoJSON.Feature[],
    };
    const set = (id: string, data: GeoJSON.FeatureCollection) => {
      const s = m.getSource(id) as GeoJSONSource | undefined;
      if (s) s.setData(data);
      else m.addSource(id, { type: "geojson", data });
    };
    set("links", linkFc);
    set("nodes", nodeFc);
    set("routes", EMPTY);
    set("pings", EMPTY);
    const t = currentTheme();
    if (!m.getLayer("links")) {
      m.addLayer({ id: "links", type: "line", source: "links", paint: { "line-color": ["get", "color"], "line-width": ["get", "w"], "line-opacity": 0.55, "line-dasharray": [2, 1.5] } });
      m.addLayer({ id: "routes", type: "line", source: "routes", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": ["get", "color"], "line-width": 3, "line-opacity": ["get", "o"] } });
      m.addLayer({ id: "pings", type: "circle", source: "pings", paint: { "circle-radius": ["get", "r"], "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": ["get", "color"], "circle-stroke-width": 2, "circle-stroke-opacity": ["get", "o"] } });
      m.addLayer({ id: "owned", type: "circle", source: "nodes", filter: [">", ["get", "owned"], 0], paint: { "circle-radius": 13, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 2.5, "circle-stroke-color": ["case", ["==", ["get", "owned"], 2], t.ui.bad, t.ui.accent] } });
      m.addLayer({ id: "nodes", type: "circle", source: "nodes", paint: { "circle-radius": ["case", ["==", ["get", "big"], 1], 7, 4.5], "circle-color": ["get", "color"], "circle-stroke-color": t.map.land, "circle-stroke-width": 1.5 } });
      m.addLayer({ id: "labels", type: "symbol", source: "nodes", filter: ["==", ["get", "big"], 1], minzoom: 9, layout: { "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"], "text-size": 12, "text-offset": [0, 1.4], "text-anchor": "top" }, paint: { "text-color": t.map.label, "text-halo-color": t.map.halo, "text-halo-width": 1.2 } });
      m.on("click", "nodes", (e) => {
        const f = e.features?.[0];
        if (!f) return;
        const n = byKeyRef.current.get(f.properties!.key);
        new maplibregl.Popup({ closeButton: true })
          .setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number])
          .setHTML(
            `<strong>${escapeHtml(f.properties!.name)}</strong><br/>${f.properties!.type} · prefix ${n?.prefix.toUpperCase() ?? ""}<br/>Last heard ${ago(n?.last_seen)}${n?.best_snr != null ? `<br/>Best SNR ${n.best_snr}` : ""}`,
          )
          .addTo(m);
      });
      m.on("mouseenter", "nodes", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "nodes", () => (m.getCanvas().style.cursor = ""));
    }
    m.setPaintProperty("owned", "circle-stroke-color", ["case", ["==", ["get", "owned"], 2], t.ui.bad, t.ui.accent]);
    m.setPaintProperty("nodes", "circle-stroke-color", t.map.land);
    m.setPaintProperty("labels", "text-color", t.map.label);
    m.setPaintProperty("labels", "text-halo-color", t.map.halo);
    if (!fitted.current && located.length) {
      fitted.current = true;
      fitTo(m, located.map((n) => [n.lon!, n.lat!]), { top: 40, bottom: 80, left: 200, right: 380 });
    }
  }
  const byKeyRef = useRef(byKey);
  byKeyRef.current = byKey;

  useEffect(() => {
    if (ready && map.current) drawStatic(map.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, links, fleet, ready]);

  // Animation loop for routes and pings.
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const m = map.current;
      const now = performance.now();
      if (m && m.getSource("routes")) {
        routes.current = routes.current.filter((r) => now - r.born < ROUTE_MS);
        pings.current = pings.current.filter((p) => now - p.born < ROUTE_MS);
        (m.getSource("routes") as GeoJSONSource).setData({
          type: "FeatureCollection",
          features: routes.current.map((r) => {
            const age = (now - r.born) / ROUTE_MS;
            return {
              type: "Feature",
              geometry: { type: "LineString", coordinates: partial(r.coords, age * 2) },
              properties: { color: r.color, o: age < 0.5 ? 0.95 : 0.95 * (1 - (age - 0.5) * 2) },
            };
          }),
        });
        (m.getSource("pings") as GeoJSONSource).setData({
          type: "FeatureCollection",
          features: pings.current.map((p) => {
            const age = (now - p.born) / ROUTE_MS;
            return { type: "Feature", geometry: { type: "Point", coordinates: p.at }, properties: { color: p.color, r: 6 + age * 22, o: 1 - age } };
          }),
        });
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Replay: load the last 24 h and step through it.
  useEffect(() => {
    if (mode !== "replay") return;
    api<ReplayRow[]>("/api/replay?hours=24").then((rows) => {
      setReplay(rows);
      setCursor(rows.length ? rows[0].t : Date.now() / 1000);
      setFeed([]);
    });
  }, [mode]);

  const cursorRef = useRef(cursor);
  cursorRef.current = cursor;
  useEffect(() => {
    if (mode !== "replay" || !playing || !replay) return;
    let last = performance.now();
    let raf = 0;
    const step = () => {
      const now = performance.now();
      const from = cursorRef.current;
      const to = Math.min(from + ((now - last) / 1000) * speed, Date.now() / 1000);
      last = now;
      const batch = replay.filter((r) => r.t > from && r.t <= to);
      batch.forEach((r) => animate(r));
      if (batch.length)
        setFeed((f) => [...batch.reverse().map((r) => ({ ...r, received_at: r.t, observer: `${r.heard}×`, snr: null } as unknown as Packet)), ...f].slice(0, 60));
      setCursor(to);
      if (to >= Date.now() / 1000 - 1) setPlaying(false);
      else raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, speed, mode, replay]);

  const start = replay?.[0]?.t ?? Date.now() / 1000 - 86400;
  const end = Date.now() / 1000;

  return (
    <div className="mapwrap">
      <MapView
        onRetheme={(m) => drawStatic(m)}
        onReady={(m) => {
          map.current = m;
          drawStatic(m);
          setReady(true);
        }}
      />
      <div className="overlay legend" aria-label="Map key">
        <span><span className="dot" style={{ background: nodeColor("repeater") }} />Repeater</span>
        <span><span className="dot" style={{ background: nodeColor("room") }} />Room server</span>
        <span><span className="dot" style={{ background: nodeColor("companion") }} />Companion</span>
        <span><span className="dot" style={{ border: "2px solid var(--accent)", width: 12, height: 12 }} />Your repeater</span>
        <span><span className="dot" style={{ border: "2px solid var(--bad)", width: 12, height: 12 }} />Yours, quiet</span>
        <span className="faint">Dashed lines: links (colour = SNR)</span>
      </div>
      <div className="overlay feed" aria-live="polite">
        <div className="row" style={{ marginBottom: 6 }}>
          <strong>{mode === "live" ? "Live packets" : "Replayed packets"}</strong>
          <span className="sp" style={{ flex: 1 }} />
          {mode === "live" && (
            <button onClick={() => setPaused((p) => !p)} aria-pressed={paused}>
              {paused ? "Resume" : "Pause"}
            </button>
          )}
        </div>
        {feed.length === 0 && <div className="empty small">Waiting for packets…</div>}
        {feed.map((p, i) => (
          <div className="pk" key={`${p.hash}-${i}`}>
            <span className="mono faint">{clock(p.received_at).slice(0, 5)}</span>
            <span><span className={`pill ${TYPE_PILL[p.ptype] || "plain"}`}>{TYPE_LABEL[p.ptype] || p.ptype}</span></span>
            <span className="ell" title={p.summary}>{p.summary}</span>
            <span className="mono faint" title="SNR or times heard">{p.snr != null ? p.snr.toFixed(1) : p.observer}</span>
          </div>
        ))}
      </div>
      <div className="overlay replay">
        {mode === "live" ? (
          <>
            <span className="pill ok">Live</span>
            <span className="small muted">Routes animate as packets arrive.</span>
            <span style={{ flex: 1 }} />
            <button onClick={() => setMode("replay")}>Replay last 24 h</button>
          </>
        ) : (
          <>
            <button onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause replay" : "Play replay"}>
              {playing ? "Pause" : "Play"}
            </button>
            <input
              type="range"
              min={start}
              max={end}
              step={1}
              value={cursor}
              aria-label="Replay time"
              onChange={(e) => {
                setCursor(Number(e.target.value));
                setFeed([]);
              }}
            />
            <span className="mono small" style={{ minWidth: 120 }}>
              {new Date(cursor * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })}
            </span>
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Replay speed">
              {SPEEDS.map((s) => (
                <option key={s} value={s}>{s}×</option>
              ))}
            </select>
            <button
              onClick={() => {
                setPlaying(false);
                setMode("live");
                api<Packet[]>("/api/packets?limit=40").then(setFeed).catch(() => {});
              }}
            >
              Back to live
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { Map as MlMap, GeoJSONSource } from "maplibre-gl";
import MapView, { fitTo } from "../components/MapView";
import { currentTheme, nodeColor, onTheme, routeColor, snrColor } from "../themes";
import { Node, Packet, TYPE_LABEL, TYPE_PILL, ago, api, clock, duration, ignoreNode, knownTime, makeResolver, useApi, useLive } from "../api";
import { CARDINAL_AT, Rect, UNITS, distanceM, freeRect, inRect, inset, pullInside, ringFeatures, ringStep } from "../rings";

const ROUTE_MS = 2600;
const SPEEDS = [1, 10, 60, 300];
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const RINGS_KEY = "notoryx-ops-range-rings";

function loadRings(): boolean {
  try {
    return localStorage.getItem(RINGS_KEY) === "1";
  } catch {
    return false;
  }
}

function saveRings(on: boolean) {
  try {
    localStorage.setItem(RINGS_KEY, on ? "1" : "0");
  } catch {
    /* storage blocked: the choice lasts for this page only */
  }
}

/** The station radio's position, or null when it isn't known (radios report 0, 0 when unset). */
function stationPos(info: any): [number, number] | null {
  const lat = info?.radio?.lat;
  const lon = info?.radio?.lon;
  if (typeof lat !== "number" || typeof lon !== "number" || (lat === 0 && lon === 0)) return null;
  return [lon, lat];
}

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
  // Every node, ignored ones included, so path hashes resolve the same way; ignored ones are never drawn.
  const { data: nodes, reload: reloadNodes } = useApi<Node[]>("/api/nodes?include_ignored=true", 60000);
  const { data: info } = useApi<any>("/api/station", 60000);
  const [rings, setRings] = useState(loadRings);
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
  const home = stationPos(info);
  const fleetKeys = useMemo(() => new Map((fleet || []).map((f) => [f.public_key, f])), [fleet]);

  useEffect(() => {
    api<Packet[]>("/api/packets?limit=40").then(setFeed).catch(() => {});
  }, []);

  function animate(p: { ptype: string; path: string[]; node_key: string | null; src_hash: string | null }) {
    const pts: [number, number][] = [];
    const origin = p.node_key ? byKey.get(p.node_key) : resolve(p.src_hash);
    if (origin?.ignored && p.node_key) return;
    if (origin?.lat != null && origin.lon != null && !origin.ignored) pts.push([origin.lon, origin.lat]);
    if (p.ptype !== "trace") {
      for (const h of p.path) {
        const n = resolve(h);
        if (n?.lat != null && n.lon != null && !n.ignored) {
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
    const located = (nodes || []).filter((n) => n.lat != null && n.lon != null && !n.ignored);
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
            stale: n.stale && knownTime(n.last_seen) != null ? 1 : 0,
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
          if (a?.lat == null || b?.lat == null || a.ignored || b.ignored) return null;
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
      // Stale nodes are drawn faded and smaller, with a dashed-look hollow centre, not only a colour change.
      const fade = (on: number, off: number): any => ["case", ["==", ["get", "stale"], 1], on, off];
      m.addLayer({ id: "nodes", type: "circle", source: "nodes", paint: { "circle-radius": ["case", ["==", ["get", "big"], 1], fade(5.5, 7), fade(3.5, 4.5)], "circle-color": ["get", "color"], "circle-opacity": fade(0.3, 1), "circle-stroke-color": t.map.land, "circle-stroke-width": 1.5, "circle-stroke-opacity": fade(0.5, 1) } });
      m.addLayer({ id: "labels", type: "symbol", source: "nodes", filter: ["==", ["get", "big"], 1], minzoom: 9, layout: { "text-field": ["case", ["==", ["get", "stale"], 1], ["concat", ["get", "name"], " (stale)"], ["get", "name"]], "text-font": ["Noto Sans Regular"], "text-size": 12, "text-offset": [0, 1.4], "text-anchor": "top" }, paint: { "text-color": t.map.label, "text-halo-color": t.map.halo, "text-halo-width": 1.2, "text-opacity": fade(0.55, 1) } });
      m.on("click", "nodes", (e) => {
        const f = e.features?.[0];
        if (!f) return;
        const n = byKeyRef.current.get(f.properties!.key);
        const heard = knownTime(n?.last_seen);
        const stale = n?.stale && heard != null ? ` <span class="pill stale">stale · ${duration(Date.now() / 1000 - heard)}</span>` : "";
        const pop = new maplibregl.Popup({ closeButton: true })
          .setLngLat((f.geometry as GeoJSON.Point).coordinates as [number, number])
          .setHTML(
            `<strong>${escapeHtml(f.properties!.name)}</strong><br/>${f.properties!.type} · prefix ${n?.prefix.toUpperCase() ?? ""}<br/>Last heard ${ago(n?.last_seen)}${stale}${n?.best_snr != null ? `<br/>Best SNR ${n.best_snr}` : ""}` +
              `<div class="popup-actions"><button type="button" class="small-btn" data-ignore>Ignore</button> <span class="err small" data-err></span></div>`,
          )
          .addTo(m);
        const btn = pop.getElement().querySelector<HTMLButtonElement>("[data-ignore]");
        btn?.setAttribute("aria-label", `Ignore ${f.properties!.name}`);
        btn?.addEventListener("click", () => {
          btn.disabled = true;
          ignoreNode(f.properties!.key, n?.name)
            .then(() => {
              pop.remove();
              reloadRef.current();
            })
            .catch((e) => {
              btn.disabled = false;
              const el = pop.getElement().querySelector("[data-err]");
              if (el) el.textContent = e.message;
            });
        });
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
    drawRings(m);
  }
  const byKeyRef = useRef(byKey);
  byKeyRef.current = byKey;
  const reloadRef = useRef(reloadNodes);
  reloadRef.current = reloadNodes;

  // Range rings around the station radio, sized to the visible view.
  const ringState = useRef<{ on: boolean; home: [number, number] | null }>({ on: rings, home });
  ringState.current = { on: rings, home };
  function drawRings(m: MlMap) {
    const { on, home: at } = ringState.current;
    const t = currentTheme();
    let lines = EMPTY;
    let labels = EMPTY;
    if (on && at) {
      // Size the rings from the part of the map no panel or control covers, not the whole
      // canvas, and keep every label inside that part.
      const safe = inset(unobscured(m), LABEL_PAD);
      if (safe.right > safe.left && safe.bottom > safe.top) {
        const cx = (safe.left + safe.right) / 2;
        const cy = (safe.top + safe.bottom) / 2;
        const half = Math.min(safe.right - safe.left, safe.bottom - safe.top) / 2;
        const c = m.unproject([cx, cy]);
        const n = m.unproject([cx, cy - half]);
        const e = m.unproject([cx + half, cy]);
        const span = Math.min(distanceM(c.lat, c.lng, n.lat, n.lng), distanceM(c.lat, c.lng, e.lat, e.lng)) / CARDINAL_AT;
        const fc = ringFeatures(at[1], at[0], ringStep(span, UNITS), UNITS);
        const p = m.project(at);
        const from: [number, number] = [p.x, p.y];
        const kept: GeoJSON.Feature[] = [];
        for (const f of fc.labels.features) {
          const q = m.project((f.geometry as GeoJSON.Point).coordinates as [number, number]);
          if (inRect(safe, q.x, q.y)) kept.push(f);
          else if (f.properties?.kind === "cardinal") {
            // Slide a compass tag back along its spoke into view; drop it if the station is off view.
            const at2 = pullInside(safe, from, [q.x, q.y]);
            if (at2) kept.push({ ...f, geometry: { type: "Point", coordinates: m.unproject(at2).toArray() } });
          }
          // A distance label that can't be seen is dropped: moving it would mislabel its ring.
        }
        lines = fc.lines;
        labels = { type: "FeatureCollection", features: kept };
      }
    }
    for (const [id, data] of [["rings", lines], ["ringlabels", labels]] as const) {
      const src = m.getSource(id) as GeoJSONSource | undefined;
      if (src) src.setData(data);
      else m.addSource(id, { type: "geojson", data });
    }
    if (!m.getLayer("rings")) {
      const below = m.getLayer("links") ? "links" : undefined;
      m.addLayer({ id: "rings", type: "line", source: "rings", paint: {} }, below);
      m.addLayer({ id: "ringlabels", type: "symbol", source: "ringlabels", layout: { "text-field": ["get", "text"], "text-font": ["Noto Sans Regular"], "text-size": ["case", ["==", ["get", "kind"], "cardinal"], 13, 11], "text-anchor": "center", "text-allow-overlap": true, "text-ignore-placement": true } });
    }
    m.setPaintProperty("rings", "line-color", t.ui.accent);
    m.setPaintProperty("rings", "line-opacity", ["case", ["==", ["get", "kind"], "spoke"], 0.2, 0.6]);
    m.setPaintProperty("rings", "line-width", ["case", ["==", ["get", "kind"], "spoke"], 1, 1.4]);
    m.setPaintProperty("ringlabels", "text-color", ["case", ["==", ["get", "kind"], "cardinal"], t.ui.accent, t.map.label]);
    m.setPaintProperty("ringlabels", "text-halo-color", t.map.halo);
    m.setPaintProperty("ringlabels", "text-halo-width", 1.5);
  }
  useEffect(() => {
    saveRings(rings);
    if (ready && map.current) drawRings(map.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rings, home?.[0], home?.[1], ready]);

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
          let queued = 0;
          const redraw = () => {
            if (!ringState.current.on || queued) return;
            queued = requestAnimationFrame(() => {
              queued = 0;
              drawRings(m);
            });
          };
          m.on("move", redraw);
          m.on("resize", redraw);
          // The packet feed grows as packets arrive and panels can change size, which moves the free area.
          const ro = new ResizeObserver(redraw);
          m.getContainer().parentElement?.querySelectorAll(".overlay").forEach((el) => ro.observe(el));
          m.once("remove", () => ro.disconnect());
          setReady(true);
        }}
      />
      <div className="overlay legend" aria-label="Map key">
        <span><span className="dot" style={{ background: nodeColor("repeater") }} />Repeater</span>
        <span><span className="dot" style={{ background: nodeColor("room") }} />Room server</span>
        <span><span className="dot" style={{ background: nodeColor("companion") }} />Companion</span>
        <span><span className="dot" style={{ border: "2px solid var(--accent)", width: 12, height: 12 }} />Your repeater</span>
        <span><span className="dot" style={{ border: "2px solid var(--bad)", width: 12, height: 12 }} />Yours, quiet</span>
        <span><span className="dot" style={{ background: "var(--text-3)", opacity: 0.35 }} />Faded: stale{info?.stale_after_hours ? ` (not heard in ${duration(info.stale_after_hours * 3600)})` : ""}</span>
        <span className="faint">Dashed lines: links (colour = SNR)</span>
        <button type="button" className="small-btn" aria-pressed={rings} onClick={() => setRings((r) => !r)} style={{ marginTop: 4, alignSelf: "flex-start" }}>
          {rings ? "Hide range rings" : "Show range rings"}
        </button>
        {rings && !home && <span className="small" role="status" style={{ color: "var(--warn)", maxWidth: 200 }}>The station radio's position isn't known, so range rings can't be drawn. Set a position on the radio.</span>}
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

/** Room left around ring labels so a whole label, not just its centre point, stays in view. */
const LABEL_PAD = 22;

/** The map area, in pixels, that the legend, packet feed, replay bar, map controls and any open drawer leave visible. */
function unobscured(m: MlMap): Rect {
  const box = m.getContainer().getBoundingClientRect();
  const obstacles: Rect[] = [];
  const parent = m.getContainer().parentElement ?? document.body;
  const els = [
    ...parent.querySelectorAll(".overlay"),
    ...m.getContainer().querySelectorAll(".maplibregl-ctrl"),
    ...document.querySelectorAll(".drawer"),
  ];
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    obstacles.push({ left: r.left - box.left, top: r.top - box.top, right: r.right - box.left, bottom: r.bottom - box.top });
  }
  // The visible part of the map, in case the page scrolls part of it out of the window.
  const view: Rect = {
    left: Math.max(0, -box.left),
    top: Math.max(0, -box.top),
    right: Math.min(box.width, window.innerWidth - box.left),
    bottom: Math.min(box.height, window.innerHeight - box.top),
  };
  return freeRect(view, obstacles);
}

function escapeHtml(s: string) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

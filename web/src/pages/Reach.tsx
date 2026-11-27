import { useCallback, useEffect, useRef, useState } from "react";
import type { Map as MlMap, GeoJSONSource } from "maplibre-gl";
import { api, clock, useApi, type Node } from "../api";
import MapView, { fitTo, onGlobe } from "../components/MapView";
import { currentTheme } from "../themes";

type Match = { public_key: string; name: string | null; node_type: string | null; lat: number | null; lon: number | null };
type Hop = { hash: string; matches: Match[]; snr?: number | null };

/** What the map shows: one line per result, through every hop we can place. */
type Drawn = { lines: [number, number][][]; points: { at: [number, number]; label: string }[] };

function place(m?: Match | null): [number, number] | null {
  if (!m || m.lat == null || m.lon == null) return null;
  const p: [number, number] = [m.lon, m.lat];
  return onGlobe(p) ? p : null;
}

function hopName(h: Hop): string {
  if (!h.matches.length) return `unknown (${h.hash})`;
  const n = h.matches[0].name || h.hash;
  return h.matches.length > 1 ? `${n} or ${h.matches.length - 1} more (${h.hash} is shared)` : n;
}

function snrText(v?: number | null) {
  return v == null ? "" : `${v > 0 ? "+" : ""}${v.toFixed(1)} dB`;
}

export default function Reach() {
  const { data: info } = useApi<any>("/api/station");
  const { data: nodes } = useApi<Node[]>("/api/nodes", 60000);
  const map = useRef<MlMap | null>(null);
  const [drawn, setDrawn] = useState<Drawn>({ lines: [], points: [] });
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const home: [number, number] | null = info?.radio?.lat != null && info?.radio?.lon != null && onGlobe([info.radio.lon, info.radio.lat])
    ? [info.radio.lon, info.radio.lat] : null;

  const draw = useCallback((d: Drawn) => {
    const m = map.current;
    if (!m) return;
    const src = m.getSource("reach") as GeoJSONSource | undefined;
    const fc = {
      type: "FeatureCollection" as const,
      features: [
        ...d.lines.filter((l) => l.length > 1).map((l) => ({ type: "Feature" as const, properties: {}, geometry: { type: "LineString" as const, coordinates: l } })),
        ...d.points.map((p) => ({ type: "Feature" as const, properties: { label: p.label }, geometry: { type: "Point" as const, coordinates: p.at } })),
      ],
    };
    if (src) src.setData(fc);
    const all = d.lines.flat().concat(d.points.map((p) => p.at));
    if (all.length) fitTo(m, all);
  }, []);

  useEffect(() => draw(drawn), [drawn, draw]);

  function onReady(m: MlMap) {
    map.current = m;
    const t = currentTheme();
    m.addSource("reach", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    m.addLayer({ id: "reach-line", type: "line", source: "reach", filter: ["==", "$type", "LineString"],
      paint: { "line-color": t.routes.trace, "line-width": 3, "line-opacity": 0.85 } });
    m.addLayer({ id: "reach-dot", type: "circle", source: "reach", filter: ["==", "$type", "Point"],
      paint: { "circle-radius": 6, "circle-color": t.nodes.repeater, "circle-stroke-color": t.ui.bg, "circle-stroke-width": 2 } });
    m.addLayer({ id: "reach-label", type: "symbol", source: "reach", filter: ["==", "$type", "Point"],
      layout: { "text-field": ["get", "label"], "text-size": 12, "text-offset": [0, 1.2], "text-anchor": "top" },
      paint: { "text-color": t.ui.text, "text-halo-color": t.ui.bg, "text-halo-width": 1.5 } });
    draw(drawn);
  }

  async function run(what: string, fn: () => Promise<void>) {
    setBusy(what);
    setErr(null);
    try {
      await fn();
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusy(null);
    }
  }

  function line(hops: Hop[], end?: [number, number] | null): [number, number][] {
    const pts = hops.map((h) => place(h.matches[0])).filter((p): p is [number, number] => !!p);
    return [...(home ? [home] : []), ...pts, ...(end ? [end] : [])];
  }

  function hopPoints(hops: Hop[]) {
    return hops.flatMap((h) => {
      const p = place(h.matches[0]);
      return p ? [{ at: p, label: `${h.matches[0].name || h.hash}${h.snr != null ? ` ${snrText(h.snr)}` : ""}` }] : [];
    });
  }

  // ---- trace -----------------------------------------------------------------
  const repeaters = (nodes || []).filter((n) => n.node_type === "repeater" || n.node_type === "room");
  const [tracePath, setTracePath] = useState<string[]>([]);
  const [pick, setPick] = useState("");
  const [trace, setTrace] = useState<any>(null);

  function addHop() {
    const n = repeaters.find((r) => r.public_key === pick);
    if (n) setTracePath((p) => [...p, n.public_key.slice(0, 2)]);
  }

  const doTrace = () => run("trace", async () => {
    const r = await api<any>("/api/reach/trace", { method: "POST", body: { path: tracePath } });
    setTrace(r);
    const out = r.hops.slice(0, tracePath.length);
    setDrawn({ lines: [line(out)], points: hopPoints(out) });
  });

  // ---- find a route ------------------------------------------------------------
  const [target, setTarget] = useState("");
  const [route, setRoute] = useState<any>(null);
  const doRoute = () => run("route", async () => {
    const r = await api<any>("/api/reach/route", { method: "POST", body: { public_key: target } });
    setRoute(r);
    const end = place((nodes || []).find((n) => n.public_key === target) as any);
    setDrawn({ lines: [line(r.out, end), line(r.back, end)], points: hopPoints([...r.out, ...r.back]) });
  });

  // ---- who can hear me ------------------------------------------------------------
  const [who, setWho] = useState<any>(null);
  const doWho = () => run("who", async () => {
    const r = await api<any>("/api/reach/who-hears-me", { method: "POST" });
    setWho(r);
    const pts = r.heard.flatMap((h: any) => {
      const p = place(h.node);
      return p ? [{ at: p, label: `${h.node?.name || h.key.slice(0, 8)} ${snrText(h.snr_them)}` }] : [];
    });
    setDrawn({ lines: pts.map((p: any) => (home ? [home, p.at] : [p.at])), points: pts });
  });

  const nameOf = (key: string) => (nodes || []).find((n) => n.public_key === key)?.name || key.slice(0, 8);

  return (
    <>
      <div className="top">
        <h1>Reach</h1>
        <span className="muted small">Test the mesh from the station radio. Each one sends a packet, so they're spaced out to save airtime.</span>
      </div>
      {err && <div className="card" role="alert" style={{ borderColor: "var(--bad)" }}>{err}</div>}
      <div className="grid2" style={{ gridTemplateColumns: "minmax(280px, 420px) minmax(0, 1fr)" }}>
        <div>
          <div className="card">
            <h2>Who can hear me?</h2>
            <p className="small muted">Asks repeaters in direct range to answer. Shows how well each heard the station and how well the station heard it back.</p>
            <button onClick={doWho} disabled={!!busy}>{busy === "who" ? "Listening for 10 s…" : "Ask"}</button>
            {who && (
              <div style={{ marginTop: 8 }}>
                {who.heard.length === 0 && <div className="empty">No repeater answered. Older firmware doesn't answer this.</div>}
                {who.heard.map((h: any) => (
                  <div key={h.key} className="row small">
                    <span className="ell">{h.node?.name || h.key.slice(0, 12)}</span>
                    <span title="How well it heard the station">heard us {snrText(h.snr_them)}</span>
                    <span className="faint" title="How well the station heard it">we heard {snrText(h.snr_us)}</span>
                  </div>
                ))}
                <div className="faint small">at {clock(who.at)}</div>
              </div>
            )}
          </div>

          <div className="card">
            <h2>Trace route</h2>
            <p className="small muted">Pick repeaters in order. The trace goes out through them and comes back the same way, with the signal at every hop.</p>
            <div className="row">
              <label className="sr-only" htmlFor="trace-pick">Repeater</label>
              <select id="trace-pick" value={pick} onChange={(e) => setPick(e.target.value)}>
                <option value="">Choose a repeater</option>
                {repeaters.map((r) => <option key={r.public_key} value={r.public_key}>{r.name || r.prefix} ({r.public_key.slice(0, 2)})</option>)}
              </select>
              <button className="small" onClick={addHop} disabled={!pick || tracePath.length >= 8}>Add hop</button>
            </div>
            <div className="small mono" style={{ margin: "6px 0" }}>
              {tracePath.length ? tracePath.join(" → ") : "No hops yet"}
              {tracePath.length > 0 && <button className="small" style={{ marginLeft: 8 }} onClick={() => setTracePath([])}>Clear</button>}
            </div>
            <button onClick={doTrace} disabled={!!busy || !tracePath.length}>{busy === "trace" ? "Tracing…" : "Trace"}</button>
            {trace && (
              <ol className="small" style={{ marginTop: 8 }}>
                {trace.hops.map((h: Hop, i: number) => <li key={i}>{hopName(h)} <span className="faint">{snrText(h.snr)}</span></li>)}
                <li>Back at the station <span className="faint">{snrText(trace.final_snr)}</span></li>
              </ol>
            )}
          </div>

          <div className="card">
            <h2>Find a route</h2>
            <p className="small muted">Asks the mesh for a fresh route to someone and back. The node has to be in the station radio's contacts.</p>
            <label className="sr-only" htmlFor="route-pick">Node</label>
            <select id="route-pick" value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="">Choose a node</option>
              {(nodes || []).map((n) => <option key={n.public_key} value={n.public_key}>{n.name || n.prefix}</option>)}
            </select>
            <button style={{ marginLeft: 8 }} onClick={doRoute} disabled={!!busy || !target}>{busy === "route" ? "Asking…" : "Find"}</button>
            {route && (
              <div className="small" style={{ marginTop: 8 }}>
                <div><b>To {nameOf(target)}:</b> {route.out.length ? route.out.map(hopName).join(" → ") : "direct"}</div>
                <div><b>Back:</b> {route.back.length ? route.back.map(hopName).join(" → ") : "direct"}</div>
              </div>
            )}
          </div>
        </div>
        <div className="card" style={{ padding: 0, minHeight: 420, position: "relative" }}>
          <MapView onReady={onReady} center={home || undefined} zoom={home ? 10 : undefined} />
        </div>
      </div>
    </>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import { GeoJSONSource, ImageSource, Map as MlMap, MapMouseEvent } from "maplibre-gl";
import MapView, { fitTo, onGlobe } from "../components/MapView";
import { currentTheme, onTheme, snrColor } from "../themes";
import { api, useApi, Node } from "../api";

type Mode = "measured" | "terrain" | "profile" | "plan";
type Pt = { lat: number; lon: number };
type Raster = { bounds: [number, number, number, number]; width: number; height: number; margin?: string; classes?: string };

const SIZES = [500, 1500, 5000];
const MODES: [Mode, string][] = [
  ["measured", "Measured"],
  ["terrain", "Terrain"],
  ["profile", "Link profile"],
  ["plan", "Plan relays"],
];

function b64bytes(s: string): Uint8Array {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function hexRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

/** Paint a coverage grid into a PNG data URL in the current look's signal colours. */
function paint(r: Raster): string {
  const c = document.createElement("canvas");
  c.width = r.width;
  c.height = r.height;
  const ctx = c.getContext("2d")!;
  const img = ctx.createImageData(r.width, r.height);
  const t = currentTheme();
  if (r.margin) {
    const scale = t.snr.map(hexRgb);
    const px = b64bytes(r.margin);
    for (let i = 0; i < px.length; i++) {
      const v = px[i];
      if (!v) continue;
      const m = ((v - 1) / 254) * 90 - 40;
      if (m < 0) continue;
      const col = m >= 20 ? scale[0] : m >= 12 ? scale[1] : m >= 6 ? scale[2] : m >= 2 ? scale[3] : scale[4];
      img.data.set([col[0], col[1], col[2], 150], i * 4);
    }
  } else if (r.classes) {
    const today = hexRgb(t.ui.accent);
    const gain = hexRgb(t.ui.ok);
    const px = b64bytes(r.classes);
    for (let i = 0; i < px.length; i++) {
      if (px[i] === 1) img.data.set([today[0], today[1], today[2], 95], i * 4);
      else if (px[i] === 2) img.data.set([gain[0], gain[1], gain[2], 175], i * 4);
    }
  }
  ctx.putImageData(img, 0, 0);
  return c.toDataURL();
}

function corners(b: [number, number, number, number]): [[number, number], [number, number], [number, number], [number, number]] {
  const [w, s, e, n] = b;
  return [[w, n], [e, n], [e, s], [w, s]];
}

function km(m: number) {
  return m >= 1000 ? `${(m / 1000).toFixed(m >= 10000 ? 0 : 1)} km` : `${Math.round(m)} m`;
}

/** Ground, line of sight and the first Fresnel zone along a link. */
function ProfileChart({ p }: { p: any }) {
  const W = 560, H = 170, pad = 6;
  const pts: any[] = p.points;
  const dist = p.distance_m || 1;
  const lo = Math.min(...pts.map((q) => Math.min(q.ground, q.los - q.fresnel)));
  const hi = Math.max(...pts.map((q) => Math.max(q.ground, q.los)));
  const span = Math.max(hi - lo, 10);
  const x = (d: number) => pad + (d / dist) * (W - 2 * pad);
  const y = (h: number) => H - pad - ((h - lo) / span) * (H - 2 * pad);
  const ground = `M${x(0)},${H} ` + pts.map((q) => `L${x(q.d)},${y(q.ground)}`).join(" ") + ` L${x(dist)},${H} Z`;
  const los = pts.map((q, i) => `${i ? "L" : "M"}${x(q.d)},${y(q.los)}`).join(" ");
  const fres = pts.map((q, i) => `${i ? "L" : "M"}${x(q.d)},${y(q.los - q.fresnel)}`).join(" ");
  const t = currentTheme();
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Terrain profile of the link">
      <path d={ground} fill={t.ui.panel2} stroke={t.ui.text3} strokeWidth={1} />
      <path d={fres} fill="none" stroke={t.ui.warn} strokeDasharray="4 3" strokeWidth={1} />
      <path d={los} fill="none" stroke={p.line_of_sight ? t.ui.ok : t.ui.bad} strokeWidth={2} />
      {p.worst && !p.line_of_sight && <circle cx={x(p.worst.d)} cy={y(p.worst.ground)} r={4} fill={t.ui.bad} />}
    </svg>
  );
}

function Budget({ b }: { b: any }) {
  const rows: [string, string][] = [
    ["Power + antennas − cables", `${b.eirp_and_gains_db} dB`],
    ["Free-space loss", `−${b.free_space_db} dB`],
    ["Terrain (diffraction)", `−${b.diffraction_db} dB`],
    ...(b.calibration_db ? [["Calibration", `${b.calibration_db > 0 ? "+" : ""}${b.calibration_db} dB`] as [string, string]] : []),
    ["Received", `${b.rx_dbm} dBm`],
    ["Noise floor", `${b.noise_dbm} dBm`],
    ["Predicted SNR", `${b.snr_db} dB`],
    ["Needed for this SF", `${b.snr_limit_db} dB`],
  ];
  return (
    <table className="small" style={{ width: "100%" }}>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}><td className="muted">{k}</td><td className="mono" style={{ textAlign: "right" }}>{v}</td></tr>
        ))}
        <tr>
          <td><strong>Margin</strong></td>
          <td style={{ textAlign: "right" }}>
            <span className={`pill ${b.margin_db >= 10 ? "ok" : b.margin_db >= 0 ? "warn" : "bad"}`}>{b.margin_db} dB</span>
          </td>
        </tr>
      </tbody>
    </table>
  );
}

export default function Coverage() {
  const [mode, setMode] = useState<Mode>("measured");
  const [size, setSize] = useState(1500);
  const [days, setDays] = useState(30);
  const { data, error } = useApi<any>(mode === "measured" ? `/api/coverage?size_m=${size}&days=${days}` : null);
  const { data: status, reload: reloadStatus } = useApi<any>("/api/terrain/status");
  const { data: surveys } = useApi<any[]>(mode === "measured" ? `/api/field/surveys?days=${days}` : null);
  const { data: nodes } = useApi<Node[]>("/api/nodes");
  const placed = useMemo(() => (nodes || []).filter((n) => n.lat != null && n.lon != null), [nodes]);

  const map = useRef<MlMap | null>(null);
  const fitted = useRef(false);
  const [ready, setReady] = useState(false);
  const [look, setLook] = useState(0);
  useEffect(() => onTheme(() => setLook((x) => x + 1)), []);

  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  // Terrain coverage
  const [site, setSite] = useState<Pt | null>(null);
  const [siteH, setSiteH] = useState<number | "">("");
  const [radius, setRadius] = useState(15);
  const [cov, setCov] = useState<any>(null);
  // Profile
  const [a, setA] = useState<Pt | null>(null);
  const [b, setB] = useState<Pt | null>(null);
  const [aH, setAH] = useState<number | "">("");
  const [bH, setBH] = useState<number | "">("");
  const [prof, setProf] = useState<any>(null);
  // Plan
  const [newSites, setNewSites] = useState<Pt[]>([]);
  const [plan, setPlan] = useState<any>(null);
  const [existing, setExisting] = useState<any[]>([]);
  const [sugg, setSugg] = useState<any[]>([]);
  const [highs, setHighs] = useState<any[]>([]);

  const modeRef = useRef(mode);
  modeRef.current = mode;
  const clickRef = useRef<(p: Pt) => void>(() => {});
  clickRef.current = (p: Pt) => {
    const m = modeRef.current;
    if (m === "terrain") setSite(p);
    else if (m === "profile") {
      if (!a || (a && b)) {
        setA(p);
        setB(null);
        setProf(null);
      } else setB(p);
    } else if (m === "plan") setNewSites((s) => (s.length >= 5 ? s : [...s, p]));
  };

  async function run<T>(label: string, f: () => Promise<T>): Promise<T | null> {
    setBusy(label);
    setErr("");
    try {
      return await f();
    } catch (e: any) {
      setErr(String(e.message || e));
      return null;
    } finally {
      setBusy("");
    }
  }

  // ---- compute -------------------------------------------------------------
  useEffect(() => {
    if (mode !== "terrain" || !site) return;
    const h = siteH === "" ? "" : `&height_m=${siteH}`;
    run("Working out coverage… (first use of an area downloads elevation)", () =>
      api(`/api/terrain/coverage?lat=${site.lat}&lon=${site.lon}&radius_km=${radius}${h}`),
    ).then((r: any) => {
      if (!r) return;
      setCov(r);
      const m = map.current;
      if (m) {
        const [w, s, e, n] = r.bounds;
        fitTo(m, [[w, s], [e, n]], 30);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [site, radius, siteH, mode]);

  useEffect(() => {
    if (mode !== "profile" || !a || !b) return;
    const q = `a_lat=${a.lat}&a_lon=${a.lon}&b_lat=${b.lat}&b_lon=${b.lon}` +
      (aH === "" ? "" : `&a_h=${aH}`) + (bH === "" ? "" : `&b_h=${bH}`);
    run("Reading the terrain…", () => api(`/api/terrain/profile?${q}`)).then((r) => r && setProf(r));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a, b, aH, bH, mode]);

  useEffect(() => {
    if (mode !== "plan" || !newSites.length) {
      setPlan(null);
      return;
    }
    run("Comparing coverage…", () =>
      api("/api/terrain/what-if", { method: "POST", body: { new: newSites.map((s) => ({ ...s, height_m: siteH === "" ? status?.repeater_height_m ?? 10 : siteH })), radius_km: radius } }),
    ).then((r: any) => {
      if (!r) return;
      setPlan(r);
      const m = map.current;
      if (m) {
        const [w, s, e, n] = r.bounds;
        fitTo(m, [[w, s], [e, n]], 30);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newSites, radius, mode]);

  function center(): Pt | null {
    const m = map.current;
    if (!m) return null;
    const c = m.getCenter();
    return { lat: c.lat, lon: c.lng };
  }

  async function suggest() {
    const c = center();
    if (!c) return;
    const r = await run("Looking for high ground…", () => api<any>(`/api/terrain/suggest?lat=${c.lat}&lon=${c.lon}&radius_km=${radius}`));
    if (r) {
      setSugg(r.suggestions);
      setExisting(r.existing);
    }
  }

  async function highPoints() {
    const c = site || center();
    if (!c) return;
    const r = await run("Looking for high ground…", () => api<any[]>(`/api/terrain/high-points?lat=${c.lat}&lon=${c.lon}&radius_km=${radius}`));
    if (r) setHighs(r);
  }

  async function recalibrate() {
    const r = await run("Comparing predictions with measured links…", () => api("/api/terrain/calibrate", { method: "POST" }));
    if (r) reloadStatus();
  }

  // ---- drawing ---------------------------------------------------------------
  function setSrc(m: MlMap, id: string, d: any) {
    const s = m.getSource(id) as GeoJSONSource | undefined;
    if (s) s.setData(d);
    else m.addSource(id, { type: "geojson", data: d });
  }

  function setRaster(m: MlMap, r: Raster | null) {
    const src = m.getSource("terrain-raster") as ImageSource | undefined;
    if (!r) {
      if (m.getLayer("terrain-raster")) m.setLayoutProperty("terrain-raster", "visibility", "none");
      return;
    }
    const url = paint(r);
    const coordinates = corners(r.bounds);
    if (src) src.updateImage({ url, coordinates });
    else m.addSource("terrain-raster", { type: "image", url, coordinates });
    if (!m.getLayer("terrain-raster")) m.addLayer({ id: "terrain-raster", type: "raster", source: "terrain-raster", paint: { "raster-fade-duration": 0 } });
    m.setLayoutProperty("terrain-raster", "visibility", "visible");
  }

  function draw(m: MlMap) {
    const t = currentTheme();
    const empty: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
    // Measured hexes
    if (mode === "measured" && data) {
      setSrc(m, "hexes", {
        ...data,
        features: data.features.map((f: any) => ({ ...f, properties: { ...f.properties, color: snrColor(f.properties.best_snr) } })),
      });
      setSrc(m, "gaps", {
        type: "FeatureCollection",
        features: data.gaps.map((g: any) => ({ type: "Feature", geometry: { type: "Point", coordinates: [g.lon, g.lat] }, properties: g })),
      });
    } else {
      setSrc(m, "hexes", empty);
      setSrc(m, "gaps", empty);
    }
    setSrc(m, "surveys", mode === "measured" && surveys ? {
      type: "FeatureCollection",
      features: surveys.map((p: any) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] },
        properties: { color: p.heard === 0 ? t.ui.bad : p.snr != null ? snrColor(p.snr) : t.ui.ok, ring: p.heard === 0 ? t.ui.bad : t.ui.bg },
      })),
    } : empty);
    if (!m.getLayer("hexes")) {
      m.addLayer({ id: "hexes", type: "fill", source: "hexes", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.45 } });
      m.addLayer({ id: "hex-lines", type: "line", source: "hexes", paint: { "line-color": ["get", "color"], "line-width": 1 } });
      m.addLayer({ id: "gaps", type: "circle", source: "gaps", paint: { "circle-radius": 7, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 2.5 } });
      m.addLayer({ id: "surveys", type: "circle", source: "surveys", paint: { "circle-radius": 4.5, "circle-color": ["get", "color"], "circle-stroke-width": 1, "circle-stroke-color": ["get", "ring"] } });
    }
    m.setPaintProperty("gaps", "circle-stroke-color", t.ui.accent2);

    // Terrain raster
    setRaster(m, mode === "terrain" ? cov : mode === "plan" ? plan : null);

    // Points and the profile line
    const pt = (p: Pt, kind: string, extra: Record<string, unknown> = {}) => ({
      type: "Feature" as const, geometry: { type: "Point" as const, coordinates: [p.lon, p.lat] }, properties: { kind, ...extra },
    });
    const feats: any[] = [];
    if (mode === "terrain") {
      if (site) feats.push(pt(site, "site"));
      highs.forEach((h) => feats.push(pt(h, "high")));
    }
    if (mode === "profile") {
      if (a) feats.push(pt(a, "a"));
      if (b) feats.push(pt(b, "b"));
      if (prof?.worst && !prof.line_of_sight) feats.push(pt(prof.worst, "worst"));
    }
    if (mode === "plan") {
      (plan?.sites || existing).filter((s: any) => !s.new).forEach((s: any) => feats.push(pt(s, "existing")));
      newSites.forEach((s) => feats.push(pt(s, "new")));
      sugg.forEach((s) => feats.push(pt(s, "suggest")));
    }
    setSrc(m, "plan-pts", { type: "FeatureCollection", features: feats });
    setSrc(m, "plan-line", mode === "profile" && a && b
      ? { type: "Feature", geometry: { type: "LineString", coordinates: [[a.lon, a.lat], [b.lon, b.lat]] }, properties: {} }
      : empty);
    if (!m.getLayer("plan-line")) {
      m.addLayer({ id: "plan-line", type: "line", source: "plan-line", paint: { "line-width": 3, "line-dasharray": [2, 1] } });
      m.addLayer({ id: "plan-pts", type: "circle", source: "plan-pts", paint: { "circle-radius": 7, "circle-stroke-width": 2 } });
    }
    m.setPaintProperty("plan-line", "line-color", prof ? (prof.works ? t.ui.ok : t.ui.bad) : t.ui.accent);
    m.setPaintProperty("plan-pts", "circle-color", ["match", ["get", "kind"],
      "site", t.ui.accent, "a", t.ui.accent, "b", t.ui.accent2, "worst", t.ui.bad, "existing", t.nodes.repeater,
      "new", t.ui.ok, "suggest", t.ui.warn, "high", t.ui.warn, t.ui.text3]);
    m.setPaintProperty("plan-pts", "circle-stroke-color", t.ui.bg);

    const ptsFit: [number, number][] = mode === "measured" && data
      ? [...data.features.flatMap((f: any) => f.geometry.coordinates[0]), ...data.gaps.map((g: any) => [g.lon, g.lat])]
      : placed.map((n) => [n.lon as number, n.lat as number]);
    if (!fitted.current && ptsFit.filter(onGlobe).length) {
      fitted.current = true;
      fitTo(m, ptsFit);
    }
  }

  useEffect(() => {
    if (ready && map.current) draw(map.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, ready, mode, cov, prof, a, b, site, plan, newSites, sugg, highs, existing, look, placed, surveys]);

  function onReady(m: MlMap) {
    map.current = m;
    m.on("click", (e: MapMouseEvent) => clickRef.current({ lat: +e.lngLat.lat.toFixed(6), lon: +e.lngLat.lng.toFixed(6) }));
    draw(m);
    setReady(true);
  }

  useEffect(() => {
    const m = map.current;
    if (m) m.getCanvas().style.cursor = mode === "measured" ? "" : "crosshair";
  }, [mode, ready]);

  const cal = status?.calibration;
  const pickNode = (set: (p: Pt) => void, setH: (h: number | "") => void) => (key: string) => {
    const n = placed.find((x) => x.public_key === key);
    if (!n) return;
    set({ lat: n.lat as number, lon: n.lon as number });
    setH(n.node_type === "repeater" || n.node_type === "room" ? status?.repeater_height_m ?? 10 : status?.client_height_m ?? 1.5);
  };

  return (
    <div className="mapwrap">
      <MapView onRetheme={(m) => draw(m)} onReady={onReady} />
      <div className="overlay" style={{ left: 12, top: 12, width: 340, maxWidth: "calc(100% - 24px)", maxHeight: "calc(100% - 24px)", overflowY: "auto" }}>
        <div className="row" role="tablist" aria-label="Coverage tools" style={{ marginBottom: 8 }}>
          {MODES.map(([k, l]) => (
            <button key={k} role="tab" aria-selected={mode === k} className={mode === k ? "primary" : ""} onClick={() => setMode(k)}>
              {l}
            </button>
          ))}
        </div>

        {mode === "measured" && (
          <>
            <div className="small muted" style={{ marginBottom: 8 }}>
              Places where a node was heard directly by an observer, coloured by the best SNR. Ringed points are nodes far from every known repeater: places a new repeater could help.
            </div>
            <div className="row small">
              <label>
                Hex size{" "}
                <select value={size} onChange={(e) => setSize(Number(e.target.value))}>
                  {SIZES.map((s) => <option key={s} value={s}>{s >= 1000 ? `${s / 1000} km` : `${s} m`}</option>)}
                </select>
              </label>
              <label>
                Range{" "}
                <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
                  <option value={7}>7 days</option>
                  <option value={30}>30 days</option>
                  <option value={365}>1 year</option>
                </select>
              </label>
            </div>
            <div className="row small" style={{ marginTop: 8 }}>
              {[["≥ 5", 6], ["0 to 5", 2], ["-5 to 0", -3], ["-10 to -5", -7], ["< -10", -12]].map(([l, v]) => (
                <span key={l as string}><span className="dot" style={{ background: snrColor(v as number) }} />{l}</span>
              ))}
            </div>
            {data && <div className="small faint" style={{ marginTop: 6 }}>{data.features.length} hexes · {data.gaps.length} far-out nodes{surveys?.length ? ` · ${surveys.length} survey points (small dots; red = nothing heard)` : ""}</div>}
            {error && <div className="err">{error}</div>}
            {data && data.features.length === 0 && <div className="small muted">No positioned nodes heard directly yet.</div>}
          </>
        )}

        {mode === "terrain" && (
          <>
            <div className="small muted" style={{ marginBottom: 8 }}>
              Click the map to place a site. The shading shows where a {status?.client_height_m ?? 1.5} m handheld should hear it, worked out from the terrain.
            </div>
            <div className="row small">
              <label>Antenna height (m) <input type="number" min={0} max={300} style={{ width: 70 }} placeholder={String(status?.repeater_height_m ?? 10)} value={siteH} onChange={(e) => setSiteH(e.target.value === "" ? "" : Number(e.target.value))} /></label>
              <label>Radius <select value={radius} onChange={(e) => setRadius(Number(e.target.value))}>{[5, 10, 15, 25, 40].map((r) => <option key={r} value={r}>{r} km</option>)}</select></label>
            </div>
            {cov && (
              <div className="small" style={{ marginTop: 8 }}>
                Ground {cov.site.ground_m} m · covers about <strong>{cov.covered_km2} km²</strong>
                <div className="row" style={{ marginTop: 6 }}>
                  {[["strong", 25], ["good", 15], ["fair", 8], ["weak", 3], ["edge", 0.5]].map(([l, v]) => (
                    <span key={l as string}><span className="dot" style={{ background: currentTheme().snr[[25, 15, 8, 3, 0.5].indexOf(v as number)] }} />{l}</span>
                  ))}
                </div>
              </div>
            )}
            <div className="row" style={{ marginTop: 8 }}>
              <button onClick={highPoints}>Find high ground</button>
            </div>
            {highs.length > 0 && (
              <div className="small" style={{ marginTop: 6 }}>
                {highs.map((h, i) => (
                  <div key={i} className="row">
                    <button className="small" onClick={() => setSite({ lat: h.lat, lon: h.lon })}>Use</button>
                    {h.elevation_m} m <span className="faint">(+{h.prominence_m} m over its surroundings)</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {mode === "profile" && (
          <>
            <div className="small muted" style={{ marginBottom: 8 }}>
              Click two points on the map, or pick nodes. Shows the ground between them, the line of sight, the first Fresnel zone (dashed) and a link budget.
            </div>
            <div className="stack small">
              <label>From <select value="" onChange={(e) => pickNode(setA, setAH)(e.target.value)}><option value="">{a ? `${a.lat.toFixed(4)}, ${a.lon.toFixed(4)}` : "click the map or pick a node"}</option>{placed.map((n) => <option key={n.public_key} value={n.public_key}>{n.name || n.prefix}</option>)}</select></label>
              <label>Height (m) <input type="number" min={0} max={300} style={{ width: 70 }} placeholder={String(status?.client_height_m ?? 1.5)} value={aH} onChange={(e) => setAH(e.target.value === "" ? "" : Number(e.target.value))} /></label>
              <label>To <select value="" onChange={(e) => pickNode(setB, setBH)(e.target.value)}><option value="">{b ? `${b.lat.toFixed(4)}, ${b.lon.toFixed(4)}` : "click the map or pick a node"}</option>{placed.map((n) => <option key={n.public_key} value={n.public_key}>{n.name || n.prefix}</option>)}</select></label>
              <label>Height (m) <input type="number" min={0} max={300} style={{ width: 70 }} placeholder={String(status?.client_height_m ?? 1.5)} value={bH} onChange={(e) => setBH(e.target.value === "" ? "" : Number(e.target.value))} /></label>
            </div>
            {prof && (
              <div style={{ marginTop: 8 }}>
                <div className="row small">
                  <strong>{km(prof.distance_m)}</strong>
                  <span className={`pill ${prof.line_of_sight ? "ok" : "bad"}`}>{prof.line_of_sight ? "Line of sight" : "Blocked by terrain"}</span>
                  <span className="pill plain">Fresnel {prof.fresnel_clear_pct}% clear</span>
                  <span className={`pill ${prof.works ? "ok" : "bad"}`}>{prof.works ? "Should work" : "Unlikely"}</span>
                </div>
                <ProfileChart p={prof} />
                <Budget b={prof.budget} />
              </div>
            )}
          </>
        )}

        {mode === "plan" && (
          <>
            <div className="small muted" style={{ marginBottom: 8 }}>
              Repeaters heard in the last 30 days with a known position count as today's network. Click the map to try up to 5 new relays: green is the area they would add.
            </div>
            <div className="row small">
              <label>Relay height (m) <input type="number" min={0} max={300} style={{ width: 70 }} placeholder={String(status?.repeater_height_m ?? 10)} value={siteH} onChange={(e) => setSiteH(e.target.value === "" ? "" : Number(e.target.value))} /></label>
              <label>Radius <select value={radius} onChange={(e) => setRadius(Number(e.target.value))}>{[5, 10, 15, 25].map((r) => <option key={r} value={r}>{r} km</option>)}</select></label>
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <button onClick={suggest}>Suggest spots here</button>
              {newSites.length > 0 && <button onClick={() => setNewSites([])}>Clear new relays</button>}
            </div>
            {plan && (
              <div className="small" style={{ marginTop: 8 }}>
                Today: <strong>{plan.covered_km2_before} km²</strong> · with the new relays: <strong>{plan.covered_km2_after} km²</strong>
                <div><span className="pill ok">+{plan.gained_km2} km²</span> <span className="faint">from {plan.sites.filter((s: any) => !s.new).length} repeaters today</span></div>
              </div>
            )}
            {sugg.length > 0 && (
              <div className="small" style={{ marginTop: 8 }}>
                <strong>Best spots near the map centre</strong>
                {sugg.map((s, i) => (
                  <div key={i} className="row">
                    <button className="small" onClick={() => setNewSites((x) => (x.length >= 5 ? x : [...x, { lat: s.lat, lon: s.lon }]))}>Try</button>
                    {s.elevation_m} m high · adds {s.gained_km2} km²
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {mode !== "measured" && (
          <div className="small faint" style={{ marginTop: 10, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
            <div>
              {status ? `${status.params.freq_mhz} MHz · SF${status.params.spreading_factor} · ${status.params.bandwidth_khz} kHz · ${status.params.tx_power_dbm} dBm. ` : ""}
              Change these in Settings → Terrain.
            </div>
            <div className="row" style={{ marginTop: 4 }}>
              <span>
                Calibration:{" "}
                {cal && cal.used ? `${cal.offset_db > 0 ? "+" : ""}${cal.offset_db} dB from ${cal.used} measured links (±${cal.rms_db} dB)` : "not yet"}
                {status && !status.use_calibration ? " (off)" : ""}
              </span>
              <button className="small" onClick={recalibrate}>Recalibrate</button>
            </div>
            <div style={{ marginTop: 4 }}>{status?.attribution}</div>
          </div>
        )}
        {busy && <div className="small muted" style={{ marginTop: 6 }}>{busy}</div>}
        {err && <div className="err" style={{ marginTop: 6 }}>{err}</div>}
      </div>
    </div>
  );
}

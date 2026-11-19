import { useEffect, useRef, useState } from "react";
import { GeoJSONSource, Map as MlMap } from "maplibre-gl";
import MapView, { fitTo } from "../components/MapView";
import { api } from "../api";
import { currentTheme, nodeColor, onTheme } from "../themes";

/** Read-only map for anyone: repeaters (and companions if the station allows), positions rounded. */
export default function PublicMap() {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  const map = useRef<MlMap | null>(null);
  const fitted = useRef(false);
  const [, setLook] = useState(0);
  useEffect(() => onTheme(() => setLook((x) => x + 1)), []);

  useEffect(() => {
    const load = () => api("/api/public/map").then((d) => (setData(d), setErr(""))).catch((e) => setErr(e.message));
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (data?.name) document.title = `${data.name} · mesh map`;
  }, [data?.name]);

  function draw(m: MlMap) {
    if (!data) return;
    const t = currentTheme();
    const nodes = {
      type: "FeatureCollection",
      features: data.nodes.map((n: any) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [n.lon, n.lat] },
        properties: { ...n, color: nodeColor(n.type), op: n.stale ? 0.35 : 1 },
      })),
    };
    const links = {
      type: "FeatureCollection",
      features: data.links.map((l: any) => ({ type: "Feature", geometry: { type: "LineString", coordinates: [l.a, l.b] }, properties: {} })),
    };
    for (const [id, d] of [["pub-links", links], ["pub-nodes", nodes]] as const) {
      const s = m.getSource(id) as GeoJSONSource | undefined;
      if (s) s.setData(d as any);
      else m.addSource(id, { type: "geojson", data: d as any });
    }
    if (!m.getLayer("pub-links")) {
      m.addLayer({ id: "pub-links", type: "line", source: "pub-links", paint: { "line-width": 1.5, "line-opacity": 0.6 } });
      m.addLayer({ id: "pub-nodes", type: "circle", source: "pub-nodes", paint: { "circle-radius": 6, "circle-stroke-width": 2, "circle-color": ["get", "color"], "circle-opacity": ["get", "op"] } });
    }
    m.setPaintProperty("pub-links", "line-color", t.ui.accent);
    m.setPaintProperty("pub-nodes", "circle-stroke-color", t.ui.bg);
    if (!fitted.current && data.nodes.length) {
      fitted.current = true;
      fitTo(m, data.nodes.map((n: any) => [n.lon, n.lat]));
    }
  }

  useEffect(() => {
    if (map.current) draw(map.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  return (
    <div className="public">
      <div className="mapwrap">
        <MapView onRetheme={draw} onReady={(m) => ((map.current = m), draw(m))} />
        <div className="overlay" style={{ left: 12, top: 12, maxWidth: 300 }}>
          <strong>{data?.name || "Mesh map"}</strong>
          {data && (
            <div className="small muted" style={{ marginTop: 4 }}>
              {data.stats.repeaters_24h} repeaters and {data.stats.nodes_24h} nodes heard in the last day. Positions are rounded.
            </div>
          )}
          {err && <div className="err">{err}</div>}
          <div className="small" style={{ marginTop: 6 }}><a href="/">Sign in</a></div>
        </div>
      </div>
    </div>
  );
}

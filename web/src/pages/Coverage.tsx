import { useEffect, useRef, useState } from "react";
import { GeoJSONSource, Map as MlMap } from "maplibre-gl";
import MapView, { fitTo, snrColor } from "../components/MapView";
import { useApi } from "../api";

const SIZES = [500, 1500, 5000];

export default function Coverage() {
  const [size, setSize] = useState(1500);
  const [days, setDays] = useState(30);
  const { data, error } = useApi<any>(`/api/coverage?size_m=${size}&days=${days}`);
  const map = useRef<MlMap | null>(null);
  const fitted = useRef(false);
  const [ready, setReady] = useState(false);

  function draw(m: MlMap) {
    if (!data) return;
    const hexes = {
      ...data,
      features: data.features.map((f: any) => ({ ...f, properties: { ...f.properties, color: snrColor(f.properties.best_snr) } })),
    };
    const gaps: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: data.gaps.map((g: any) => ({ type: "Feature", geometry: { type: "Point", coordinates: [g.lon, g.lat] }, properties: g })),
    };
    const set = (id: string, d: any) => {
      const s = m.getSource(id) as GeoJSONSource | undefined;
      if (s) s.setData(d);
      else m.addSource(id, { type: "geojson", data: d });
    };
    set("hexes", hexes);
    set("gaps", gaps);
    if (!m.getLayer("hexes")) {
      m.addLayer({ id: "hexes", type: "fill", source: "hexes", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.45 } });
      m.addLayer({ id: "hex-lines", type: "line", source: "hexes", paint: { "line-color": ["get", "color"], "line-width": 1 } });
      m.addLayer({ id: "gaps", type: "circle", source: "gaps", paint: { "circle-radius": 7, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": "#9d8cf2", "circle-stroke-width": 2.5 } });
    }
    const pts: [number, number][] = [
      ...data.features.flatMap((f: any) => f.geometry.coordinates[0]),
      ...data.gaps.map((g: any) => [g.lon, g.lat]),
    ];
    if (!fitted.current && pts.length) {
      fitted.current = true;
      fitTo(m, pts);
    }
  }

  useEffect(() => {
    if (ready && map.current) draw(map.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, ready]);

  return (
    <div className="mapwrap">
      <MapView onReady={(m) => ((map.current = m), draw(m), setReady(true))} />
      <div className="overlay" style={{ left: 12, top: 12, maxWidth: 320 }}>
        <strong>Coverage</strong>
        <div className="small muted" style={{ margin: "4px 0 8px" }}>
          Places where a node was heard directly by an observer, coloured by the best SNR. Purple rings are nodes far from every known repeater: places a new repeater could help.
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
        {data && (
          <div className="small faint" style={{ marginTop: 6 }}>
            {data.features.length} hexes · {data.gaps.length} far-out nodes
          </div>
        )}
        {error && <div className="err">{error}</div>}
        {data && data.features.length === 0 && <div className="small muted">No positioned nodes heard directly yet.</div>}
      </div>
    </div>
  );
}

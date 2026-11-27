import { useEffect, useRef, useState } from "react";
import { api, clock, fmt, getToken, useApi } from "../api";

type Pack = {
  id: number; name: string; bbox: number[]; min_zoom: number; max_zoom: number; tiles: number;
  state: string; done: number | null; total: number | null; failed: number; created: number;
};
type Summary = { tiles: number; bytes: number; pinned: number; limit_bytes: number; packs: Pack[] };

const mb = (b: number) => `${fmt(b / 1024 / 1024, b < 10 * 1024 * 1024 ? 1 : 0)} MB`;

/** A box around a point, radius in km. */
function around(lat: number, lon: number, km: number): number[] {
  const dLat = km / 111.32;
  const dLon = km / (111.32 * Math.max(0.1, Math.cos((lat * Math.PI) / 180)));
  return [lon - dLon, lat - dLat, lon + dLon, lat + dLat].map((v) => Math.round(v * 1e5) / 1e5);
}

/** Map packs kept on the station so the map works with no internet. */
export default function OfflineMaps() {
  const { data, reload } = useApi<Summary>("/api/maps", 0);
  const { data: info } = useApi<any>("/api/station");
  const { data: nodes } = useApi<any[]>("/api/nodes");
  const [name, setName] = useState("Home area");
  const [km, setKm] = useState(25);
  const [zoom, setZoom] = useState(13);
  const [useNodes, setUseNodes] = useState(false);
  const [est, setEst] = useState<{ tiles: number; limit: number; approx_bytes: number } | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const busy = (data?.packs || []).some((p) => p.state === "downloading" || p.state === "queued");

  useEffect(() => {
    if (!busy) return;
    const t = setInterval(reload, 1500);
    return () => clearInterval(t);
  }, [busy, reload]);

  const lat = info?.radio?.lat, lon = info?.radio?.lon;
  const placed = (nodes || []).filter((n) => n.lat != null && n.lon != null && Math.abs(n.lat) <= 85 && Math.abs(n.lon) <= 180);
  const bbox: number[] | null = useNodes && placed.length
    ? (() => {
        const lats = placed.map((n) => n.lat), lons = placed.map((n) => n.lon);
        const pad = 0.02;
        return [Math.min(...lons) - pad, Math.min(...lats) - pad, Math.max(...lons) + pad, Math.max(...lats) + pad];
      })()
    : lat != null && lon != null ? around(lat, lon, km) : null;

  useEffect(() => {
    if (!bbox) return setEst(null);
    const [w, s, e, n] = bbox;
    api<any>(`/api/maps/estimate?west=${w}&south=${s}&east=${e}&north=${n}&max_zoom=${zoom}`).then(setEst).catch(() => setEst(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(bbox), zoom]);

  async function download() {
    if (!bbox) return;
    setNote(null);
    try {
      await api("/api/maps/packs", { method: "POST", body: { name, bbox, max_zoom: zoom } });
      reload();
    } catch (e: any) {
      setNote(String(e.message || e));
    }
  }

  async function remove(p: Pack) {
    if (!confirm(`Remove the map pack "${p.name}"? Its tiles may be dropped from the station later.`)) return;
    await api(`/api/maps/packs/${p.id}`, { method: "DELETE" }).catch((e) => setNote(String(e.message || e)));
    reload();
  }

  async function exportPack(p: Pack) {
    setNote(`Exporting ${p.name}…`);
    const headers: Record<string, string> = {};
    const tok = getToken();
    if (tok) headers.Authorization = `Bearer ${tok}`;
    const r = await fetch(`/api/maps/packs/${p.id}/export.mbtiles`, { headers });
    if (!r.ok) return setNote(`Couldn't export ${p.name}.`);
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = `${p.name.replace(/[^A-Za-z0-9 _-]/g, "").trim() || "map-pack"}.mbtiles`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    setNote(null);
  }

  async function importFile(f: File) {
    setNote(`Adding ${f.name}…`);
    const headers: Record<string, string> = {};
    const tok = getToken();
    if (tok) headers.Authorization = `Bearer ${tok}`;
    const r = await fetch(`/api/maps/import?name=${encodeURIComponent(f.name.replace(/\.mbtiles$/i, ""))}`, { method: "POST", headers, body: f });
    const body = await r.json().catch(() => ({}));
    setNote(r.ok ? `Added ${fmt(body.tiles)} tiles from ${f.name}.` : body.detail || `Couldn't add ${f.name}.`);
    reload();
  }

  const tooBig = est && est.tiles > est.limit;
  return (
    <div className="card" id="set-maps">
      <h2>Offline maps</h2>
      <div className="small muted" style={{ marginBottom: 8 }}>
        The station keeps every map tile it shows, so places you've looked at work with no internet. Download an area ahead of time to keep it for good.
      </div>
      {data && (
        <div className="small" style={{ marginBottom: 10 }}>
          {fmt(data.tiles)} tiles on the station ({mb(data.bytes)}), {fmt(data.pinned)} kept in packs. Tiles outside packs are trimmed above {mb(data.limit_bytes)}.
        </div>
      )}
      <div className="seg" role="radiogroup" aria-label="Area" style={{ marginBottom: 10 }}>
        <button type="button" role="radio" aria-checked={!useNodes} className={!useNodes ? "on" : ""} onClick={() => setUseNodes(false)}>Around the station</button>
        <button type="button" role="radio" aria-checked={useNodes} className={useNodes ? "on" : ""} onClick={() => setUseNodes(true)} disabled={!placed.length}>Every node with a position</button>
      </div>
      <div className="formgrid">
        <label className="field">
          <span className="flabel">Name</span>
          <input value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
        </label>
        {!useNodes && (
          <label className="field">
            <span className="flabel">Distance (km)</span>
            <input type="number" min={1} max={200} value={km} onChange={(e) => setKm(Math.max(1, Math.min(200, Number(e.target.value) || 1)))} />
          </label>
        )}
        <label className="field">
          <span className="flabel">Most detail</span>
          <select value={zoom} onChange={(e) => setZoom(Number(e.target.value))}>
            <option value={10}>Towns (zoom 10)</option>
            <option value={12}>Main roads (zoom 12)</option>
            <option value={13}>Streets (zoom 13)</option>
            <option value={14}>Every building (zoom 14)</option>
          </select>
        </label>
      </div>
      {!bbox && <div className="small muted">The station radio has no position yet. Set one on the radio, or pick every node with a position.</div>}
      {est && (
        <div className={`small ${tooBig ? "" : "muted"}`} style={{ margin: "8px 0", color: tooBig ? "var(--bad)" : undefined }}>
          {fmt(est.tiles)} tiles, about {mb(est.approx_bytes)}.{tooBig && ` That's over the ${fmt(est.limit)} tile limit. Make it smaller or lower the detail.`}
        </div>
      )}
      <div className="row">
        <button className="primary" onClick={download} disabled={!bbox || !!tooBig || busy || !name.trim()}>Download</button>
        <button onClick={() => file.current?.click()}>Add an MBTiles file</button>
        <input ref={file} type="file" accept=".mbtiles" hidden onChange={(e) => e.target.files?.[0] && importFile(e.target.files[0])} />
      </div>
      {note && <div className="small" role="status" style={{ marginTop: 6 }}>{note}</div>}
      {(data?.packs || []).length > 0 && (
        <table className="small" style={{ marginTop: 10, width: "100%" }}>
          <thead><tr><th>Pack</th><th>Tiles</th><th>State</th><th /></tr></thead>
          <tbody>
            {data!.packs.map((p) => (
              <tr key={p.id}>
                <td>{p.name}<div className="faint">zoom {p.min_zoom} to {p.max_zoom} · {clock(p.created, false)}</div></td>
                <td>{fmt(p.tiles)}</td>
                <td>
                  {p.state === "downloading" && p.total ? `${Math.round(((p.done || 0) / p.total) * 100)}%` : p.state}
                  {p.failed > 0 && <span className="faint"> ({fmt(p.failed)} missed)</span>}
                </td>
                <td className="row">
                  <button className="small" onClick={() => exportPack(p)}>Export</button>
                  <button className="small" onClick={() => remove(p)} disabled={p.state === "downloading"}>Remove</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="small faint" style={{ marginTop: 8 }}>
        Map data © OpenMapTiles © OpenStreetMap contributors. Downloads are capped at 25,000 tiles to be fair to the free tile server.
      </div>
    </div>
  );
}

import { useMemo, useState } from "react";
import { Node, ago, fmt, useApi } from "../api";
import { IgnoreButton, StaleTag } from "../components/NodeTags";

const TYPES = ["", "repeater", "room", "companion", "sensor"];

export default function Nodes() {
  const { data, error, reload } = useApi<Node[]>("/api/nodes", 30000);
  const { data: info } = useApi<any>("/api/station");
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [hideStale, setHideStale] = useState(false);
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (data || []).filter(
      (n) =>
        (!type || n.node_type === type) &&
        (!hideStale || !n.stale) &&
        (!needle || (n.name || "").toLowerCase().includes(needle) || n.public_key.startsWith(needle)),
    );
  }, [data, q, type, hideStale]);
  const staleCount = (data || []).filter((n) => n.stale).length;
  const staleAfter = info?.stale_after_hours;

  return (
    <>
      <div className="top">
        <h1>Nodes</h1>
        <span className="muted small">
          Every node this station has heard.{" "}
          {staleAfter ? `Nodes not heard for ${staleAfter >= 24 ? `${staleAfter / 24} d` : `${staleAfter} h`} are marked stale (${staleCount} now).` : "Stale marking is off."}
        </span>
        <span style={{ flex: 1 }} />
        <input placeholder="Name or key" aria-label="Search nodes" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Node type">
          {TYPES.map((t) => (
            <option key={t} value={t}>{t || "All types"}</option>
          ))}
        </select>
        <label className="row small"><input type="checkbox" checked={hideStale} onChange={(e) => setHideStale(e.target.checked)} /> Hide stale</label>
      </div>
      {error && <div className="err">{error}</div>}
      <div className="card tablewrap" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr><th>Name</th><th>Type</th><th>Prefix</th><th>Last heard</th><th>Adverts</th><th>Best SNR</th><th>Position</th><th /></tr>
          </thead>
          <tbody>
            {rows.map((n) => (
              <tr key={n.public_key} className={n.stale ? "is-stale" : undefined}>
                <td>
                  <div>{n.name || <span className="faint">unnamed</span>}</div>
                  <div className="small faint mono">{n.public_key.slice(0, 12)}</div>
                </td>
                <td>{n.node_type || "—"}</td>
                <td className="mono">{n.prefix.toUpperCase()}</td>
                <td>
                  <div className="row" style={{ gap: 6 }}>
                    <span>{ago(n.last_seen)}</span>
                    <StaleTag stale={n.stale} lastHeard={n.last_seen} />
                  </div>
                </td>
                <td>{n.adverts}</td>
                <td>{fmt(n.best_snr, 1)}</td>
                <td className="small">{n.lat != null ? "known" : <span className="faint">none</span>}</td>
                <td><IgnoreButton small publicKey={n.public_key} name={n.name} onChange={reload} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        {data && rows.length === 0 && <div className="empty" style={{ padding: 16 }}>No nodes match.</div>}
      </div>
      <div className="small faint">Ignored nodes are listed under Settings, where you can show them again.</div>
    </>
  );
}

import { Fragment, useMemo } from "react";
import { Node, TYPE_LABEL, makeResolver, useApi } from "../api";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function Analytics() {
  const tz = -new Date().getTimezoneOffset();
  const { data: ov } = useApi<any>("/api/overview", 30000);
  const { data: heat } = useApi<number[][]>(`/api/analytics/heatmap?tz=${tz}`, 300000);
  const { data: top } = useApi<any>("/api/analytics/top", 60000);
  const { data: prefixes } = useApi<any[]>("/api/analytics/prefixes", 120000);
  const { data: nodes } = useApi<Node[]>("/api/nodes", 120000);
  const resolve = useMemo(() => makeResolver(nodes || []), [nodes]);
  const maxHeat = Math.max(1, ...(heat || []).flat());
  const maxType = Math.max(1, ...((ov?.types_24h || []).map((t: any) => t.n) as number[]));
  const clashes = (prefixes || []).filter((p) => p.repeaters > 1);
  const free = (prefixes || []).filter((p) => p.repeaters === 0 && p.others === 0);

  return (
    <>
      <div className="top"><h1>Analytics</h1></div>
      <div className="tiles">
        <div className="tile"><div className="k">Packets 24 h</div><div className="v">{ov?.packets_24h ?? "—"}</div></div>
        <div className="tile"><div className="k">Unique packets 24 h</div><div className="v">{ov?.unique_24h ?? "—"}</div></div>
        <div className="tile"><div className="k">Unique last hour</div><div className="v">{ov?.unique_1h ?? "—"}</div></div>
        <div className="tile"><div className="k">Nodes heard 24 h</div><div className="v">{ov?.nodes_24h ?? "—"}</div></div>
        <div className="tile"><div className="k">Repeaters heard 24 h</div><div className="v">{ov?.repeaters_24h ?? "—"}</div></div>
        <div className="tile"><div className="k">Prefix clashes</div><div className="v" style={{ color: ov?.prefix_clashes ? "var(--warn)" : undefined }}>{ov?.prefix_clashes ?? "—"}</div></div>
      </div>

      <div className="card">
        <h2>Activity, last 7 days</h2>
        <div className="heat" role="img" aria-label="Unique packets by weekday and hour">
          <div className="lab" />
          {Array.from({ length: 24 }, (_, h) => <div key={h} className="lab" style={{ justifyContent: "center" }}>{h % 3 === 0 ? h : ""}</div>)}
          {(heat || []).map((row, d) => (
            <Fragment key={d}>
              <div className="lab">{DAYS[d]}</div>
              {row.map((v, h) => (
                <div key={`${d}-${h}`} title={`${DAYS[d]} ${h}:00 · ${v} packets`} style={{ background: v ? `color-mix(in srgb, var(--accent) ${Math.round(15 + (v / maxHeat) * 85)}%, transparent)` : "var(--panel-2)" }} />
              ))}
            </Fragment>
          ))}
        </div>
      </div>

      <div className="grid2">
        <div className="card">
          <h2>Packet types, last 24 h</h2>
          {(ov?.types_24h || []).map((t: any) => (
            <div key={t.ptype} className="row" style={{ flexWrap: "nowrap", marginBottom: 4 }}>
              <span style={{ width: 90 }}>{TYPE_LABEL[t.ptype] || t.ptype}</span>
              <div className="bar" style={{ flex: 1 }}><i style={{ width: `${(t.n / maxType) * 100}%`, background: "linear-gradient(90deg, var(--accent), var(--accent-2))" }} /></div>
              <span className="mono small" style={{ width: 50, textAlign: "right" }}>{t.n}</span>
            </div>
          ))}
        </div>
        <div className="card">
          <h2>Busiest relays, last 24 h</h2>
          <div className="small muted" style={{ marginBottom: 6 }}>Repeaters that showed up most in packet paths.</div>
          <table>
            <thead><tr><th>Prefix</th><th>Repeater</th><th>Packets</th></tr></thead>
            <tbody>
              {(top?.relays || []).map(([p, n]: [string, number]) => (
                <tr key={p}><td className="mono">{p.toUpperCase()}</td><td>{resolve(p)?.name || <span className="faint">unknown or shared</span>}</td><td>{n}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h2>Most adverts, last 24 h</h2>
          <table>
            <thead><tr><th>Node</th><th>Type</th><th>Adverts</th></tr></thead>
            <tbody>
              {(top?.adverts || []).map((a: any) => (
                <tr key={a.public_key}><td>{a.name || a.public_key.slice(0, 8)}</td><td>{a.node_type}</td><td>{a.adverts}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <h2>Busiest senders, last 24 h</h2>
          <table>
            <thead><tr><th>Prefix</th><th>Node</th><th>Packets</th></tr></thead>
            <tbody>
              {(top?.senders || []).map(([p, n]: [string, number]) => (
                <tr key={p}><td className="mono">{p.toUpperCase()}</td><td>{resolve(p)?.name || <span className="faint">unknown or shared</span>}</td><td>{n}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h2>
          One-byte prefixes
          <span className="pill ok">Repeater</span>
          <span className="pill blue">Other node</span>
          <span className="pill warn">Clash</span>
        </h2>
        <div className="small muted" style={{ marginBottom: 8 }}>
          Packet paths name repeaters by the first byte of their key. Two repeaters on the same byte make routes ambiguous. Nodes heard in the last 7 days.
        </div>
        <div className="pgrid">
          {(prefixes || []).map((p) => (
            <div
              key={p.prefix}
              className={p.repeaters > 1 ? "clash" : p.repeaters ? "rep" : p.others ? "other" : ""}
              title={`${p.prefix.toUpperCase()}: ${p.repeaters} repeaters, ${p.others} other nodes${p.names.length ? ` (${p.names.join(", ")})` : ""}`}
            >
              {p.prefix.toUpperCase()}
            </div>
          ))}
        </div>
        {clashes.length > 0 && (
          <div className="stack small" style={{ marginTop: 10 }}>
            {clashes.map((c) => <div key={c.prefix}><span className="pill warn">{c.prefix.toUpperCase()}</span> {c.names.join(", ")}</div>)}
          </div>
        )}
        <div className="small faint" style={{ marginTop: 8 }}>
          {free.length} prefixes are unused. A new repeater whose key starts with one of these avoids clashes: {free.slice(0, 12).map((f) => f.prefix.toUpperCase()).join(" ")}{free.length > 12 ? " …" : ""}
        </div>
      </div>
    </>
  );
}

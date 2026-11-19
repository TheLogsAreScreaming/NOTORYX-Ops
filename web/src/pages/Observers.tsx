import { ago, fmt, useApi } from "../api";
import { StaleTag } from "../components/NodeTags";

export default function Observers() {
  const { data, error } = useApi<any[]>("/api/observers", 30000);
  return (
    <>
      <div className="top">
        <h1>Observers</h1>
        <span className="muted small">Radios that feed packets to this station: the station radio and any observers on your MQTT brokers.</span>
      </div>
      {error && <div className="err">{error}</div>}
      {data?.length === 0 && <div className="empty">No observers yet. Connect a radio or add an MQTT broker in the station settings.</div>}
      <div className="grid2">
        {(data || []).map((o) => {
          const max = Math.max(1, ...o.hourly);
          return (
            <div className="card" key={o.name}>
              <h2>
                {o.name}
                <span className={`pill ${o.live ? "ok" : "bad"}`}>{o.live ? "Live" : "Quiet"}</span>
                <StaleTag stale={o.stale} lastHeard={o.last_seen} />
              </h2>
              <div className="tiles" style={{ marginBottom: 8 }}>
                <div className="tile"><div className="k">Packets 24 h</div><div className="v">{o.packets_24h}</div></div>
                <div className="tile"><div className="k">Average SNR</div><div className="v">{fmt(o.avg_snr, 1)}</div></div>
                <div className="tile"><div className="k">Average RSSI</div><div className="v">{fmt(o.avg_rssi)}</div></div>
              </div>
              <div className="small muted" style={{ marginBottom: 4 }}>Packets per hour, last 24 h</div>
              <div className="spark" aria-label="Packets per hour">
                {o.hourly.map((v: number, i: number) => <i key={i} style={{ height: `${(v / max) * 100}%` }} title={`${v}`} />)}
              </div>
              <div className="small faint" style={{ marginTop: 8 }}>
                Source {o.source} · last packet {ago(o.last_seen)} · first seen {new Date(o.first_seen * 1000).toLocaleDateString()}
                {o.public_key ? ` · key ${o.public_key.slice(0, 8)}` : ""}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

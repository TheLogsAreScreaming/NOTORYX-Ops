import { Link } from "react-router-dom";
import { ago, api, useApi } from "../api";

const STATE: Record<string, string> = { connected: "ok", connecting: "warn", disconnected: "bad", error: "bad", off: "plain" };

export default function Station() {
  const { data: s, error } = useApi<any>("/api/station", 5000);
  if (error) return <div className="err">{error}</div>;
  if (!s) return <div className="empty">Loading…</div>;
  const r = s.radio;
  return (
    <>
      <div className="top">
        <h1>Station</h1>
        <span className="muted">{s.name} · v{s.version}</span>
        <span style={{ flex: 1 }} />
        <button
          className="danger"
          onClick={() => {
            if (window.confirm("Stop the station? Packets and fleet polls stop until you start it again."))
              api("/api/station/stop", { method: "POST" }).then(() => alert("The station is stopping. Start it again from the NOTORYX Ops app or its service.")).catch((e) => alert(e.message));
          }}
        >
          Stop station
        </button>
      </div>
      {s.demo && <div className="note" style={{ marginBottom: 14 }}>Demo mode: everything on the dashboard is made-up traffic. No radio is used.</div>}
      <div className="grid2">
        <div className="card">
          <h2>Radio <span className={`pill ${STATE[r.state] || "plain"}`}>{r.state}</span></h2>
          <table>
            <tbody>
              <tr><th>Connection</th><td>{r.transport}</td></tr>
              <tr><th>Radio name</th><td>{r.name || "—"}</td></tr>
              <tr><th>Public key</th><td className="mono small wrap">{r.public_key || "—"}</td></tr>
              <tr><th>Position</th><td>{r.lat ? `${r.lat}, ${r.lon}` : "—"}</td></tr>
              <tr><th>Connected</th><td>{ago(r.connected_at)}</td></tr>
              {r.error && <tr><th>Last error</th><td className="err">{r.error}</td></tr>}
            </tbody>
          </table>
          <RadioHealth enabled={r.state !== "off"} />
          {r.state === "off" && <div className="small muted" style={{ marginTop: 8 }}>No radio set yet. Pick one in <Link to="/settings">Settings</Link>.</div>}
        </div>
        <div className="card">
          <h2>MQTT brokers</h2>
          {s.mqtt.length === 0 && <div className="small muted">None. Add one in Settings to read packets from observers elsewhere on the mesh.</div>}
          {s.mqtt.map((m: any) => (
            <div key={m.name} className="stack" style={{ marginBottom: 8 }}>
              <div className="row"><strong>{m.name}</strong><span className={`pill ${STATE[m.state] || "plain"}`}>{m.state}</span></div>
              <div className="small muted">{m.host}:{m.port} · {m.topics.join(", ")} · {m.messages} messages</div>
              {m.error && <div className="err">{m.error}</div>}
            </div>
          ))}
        </div>
        <div className="card">
          <h2>Decoding</h2>
          <table>
            <tbody>
              <tr><th>Packets decoded</th><td>{s.decoded.ok}</td></tr>
              <tr><th>Couldn't decode</th><td>{s.decoded.bad}</td></tr>
              <tr><th>Packets kept for</th><td>{s.retention_days} days</td></tr>
              <tr><th>Channel keys</th><td>{s.channels.map((c: any) => `${c.name} (${c.hash})`).join(", ") || "none"}</td></tr>
              <tr><th>Running since</th><td>{new Date(s.started_at * 1000).toLocaleString()}</td></tr>
            </tbody>
          </table>
        </div>
        <div className="card">
          <h2>Fleet polling</h2>
          <table>
            <tbody>
              <tr><th>Every</th><td>{s.fleet.poll_minutes} minutes</td></tr>
              <tr><th>Between repeaters</th><td>{s.fleet.stagger_seconds} seconds</td></tr>
              <tr><th>Last round</th><td>{ago(s.fleet.last_round)}</td></tr>
            </tbody>
          </table>
          <div className="small faint" style={{ marginTop: 8 }}>Each poll sends a login and a status request over the air. Firmware is checked once a day and neighbours every 6 hours.</div>
        </div>
        <div className="card">
          <h2>Settings</h2>
          <div className="small muted" style={{ marginBottom: 10 }}>Radio, channels, fleet checks, alerts, brokers, access and the look are all on the Settings page.</div>
          <Link to="/settings"><button>Open Settings</button></Link>
        </div>
      </div>
      <LeavesStation />
    </>
  );
}

/** Everything that can leave the station, from the same list the build's privacy check uses. */
function LeavesStation() {
  const { data } = useApi<{ flows: any[] }>("/api/privacy", 60000);
  if (!data) return null;
  const onRadio = data.flows.filter((f) => f.on && f.radio_data).length;
  return (
    <div className="card">
      <h2>What leaves this station</h2>
      <div className="small muted" style={{ marginBottom: 8 }}>
        {onRadio === 0
          ? "Nothing your radios hear leaves this station right now."
          : `${onRadio} of these send something your radios heard. Each one is off until you turn it on.`}
        {" "}Every build checks that no other code can reach the network.
      </div>
      <table className="small">
        <tbody>
          {data.flows.map((f) => (
            <tr key={f.id}>
              <th style={{ whiteSpace: "nowrap" }}>
                <span className={`pill ${f.on ? (f.radio_data ? "warn" : "plain") : "ok"}`}>{f.on ? "On" : "Off"}</span> {f.name}
              </th>
              <td>{f.sends}<div className="faint">{f.radio_data ? "Includes radio data · " : ""}{f.setting}</div></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function dur(sec?: number | null): string {
  if (sec == null) return "—";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}

/** Battery, firmware, uptime and airtime of the station radio. */
function RadioHealth({ enabled }: { enabled: boolean }) {
  const { data: h } = useApi<any>(enabled ? "/api/station/radio" : null, 60000);
  if (!enabled || !h) return null;
  const v = h.battery_mv ? h.battery_mv / 1000 : null;
  return (
    <>
      <h3 style={{ marginTop: 14 }}>Health</h3>
      <table>
        <tbody>
          {h.model && <tr><th>Model</th><td>{h.model}</td></tr>}
          {h.firmware && <tr><th>Firmware</th><td>{h.firmware}</td></tr>}
          {v != null && <tr><th>Battery</th><td className={v < 3.6 ? "err" : ""}>{v.toFixed(2)} V</td></tr>}
          {h.uptime_s != null && <tr><th>Radio uptime</th><td>{dur(h.uptime_s)}</td></tr>}
          {h.noise_floor != null && <tr><th>Noise floor</th><td>{h.noise_floor} dBm</td></tr>}
          {h.last_rssi != null && <tr><th>Last packet</th><td>{h.last_rssi} dBm{h.last_snr != null ? ` · SNR ${h.last_snr} dB` : ""}</td></tr>}
          {h.tx_air_s != null && <tr><th>Airtime</th><td>sent {dur(h.tx_air_s)} · heard {dur(h.rx_air_s)}</td></tr>}
          <tr><th>Reconnects</th><td>{h.reconnects}{h.disconnected_since ? ` · down since ${ago(h.disconnected_since)}` : ""}</td></tr>
        </tbody>
      </table>
      <div className="small faint" style={{ marginTop: 6 }}>Checked once a minute. An alert goes out when the radio is lost for longer than the time set in Settings → Alerts.</div>
    </>
  );
}

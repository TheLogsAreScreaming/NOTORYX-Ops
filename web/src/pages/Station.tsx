import { useState } from "react";
import { ago, api, getToken, setToken, useApi } from "../api";

const STATE: Record<string, string> = { connected: "ok", connecting: "warn", disconnected: "bad", error: "bad", off: "plain" };

export default function Station() {
  const { data: s, error } = useApi<any>("/api/station", 5000);
  const [tok, setTok] = useState(getToken());
  const [saved, setSaved] = useState(false);
  const [theme, setTheme] = useState(document.documentElement.dataset.theme || "");
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
              <tr><th>Public key</th><td className="mono small" style={{ wordBreak: "break-all" }}>{r.public_key || "—"}</td></tr>
              <tr><th>Position</th><td>{r.lat ? `${r.lat}, ${r.lon}` : "—"}</td></tr>
              <tr><th>Connected</th><td>{ago(r.connected_at)}</td></tr>
              {r.error && <tr><th>Last error</th><td className="err">{r.error}</td></tr>}
            </tbody>
          </table>
          {r.state === "off" && <div className="small muted" style={{ marginTop: 8 }}>No radio set. Set [radio] transport in the config file to serial, ble or tcp.</div>}
        </div>
        <div className="card">
          <h2>MQTT brokers</h2>
          {s.mqtt.length === 0 && <div className="small muted">None. Add a [[mqtt]] block to the config file to read observer packets from a broker.</div>}
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
          <h2>Admin token</h2>
          <div className="small muted" style={{ marginBottom: 8 }}>
            {s.changes_need_token
              ? "This station needs its admin token for changes (fleet, bulk settings, alerts). It's kept in this browser only."
              : "No admin token set, so changes only work from the station computer itself."}
          </div>
          <form className="row" onSubmit={(e) => (e.preventDefault(), setToken(tok.trim()), setSaved(true))}>
            <input type="password" value={tok} onChange={(e) => (setTok(e.target.value), setSaved(false))} placeholder="Admin token" aria-label="Admin token" style={{ flex: 1 }} autoComplete="off" />
            <button type="submit">Save</button>
          </form>
          {saved && <div className="small" style={{ color: "var(--ok)", marginTop: 6 }}>Saved in this browser.</div>}
        </div>
        <div className="card">
          <h2>Settings file</h2>
          <div className="mono small" style={{ wordBreak: "break-all" }}>{s.config_path}</div>
          <div className="small muted" style={{ marginTop: 6 }}>Edit it and restart the station to change the radio, brokers, channels, polling or alerts.</div>
          <div className="row" style={{ marginTop: 10 }}>
            <span className="small">Theme</span>
            <select
              value={theme}
              aria-label="Theme"
              onChange={(e) => {
                const v = e.target.value;
                setTheme(v);
                if (v) document.documentElement.dataset.theme = v;
                else delete document.documentElement.dataset.theme;
                try {
                  localStorage.setItem("notoryx-ops-theme", v);
                } catch {
                  /* storage blocked */
                }
              }}
            >
              <option value="">Match system</option>
              <option value="dark">Dark</option>
              <option value="light">Light</option>
            </select>
          </div>
        </div>
      </div>
    </>
  );
}

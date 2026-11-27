import { ReactNode, useEffect, useState } from "react";
import { ago, api, getToken, setToken, unignoreNode, useApi } from "../api";
import { LookGallery } from "../components/ThemePicker";
import Accounts from "../components/Accounts";
import RadioCard from "../components/RadioCard";
import UpdatesCard from "../components/UpdatesCard";
import OfflineMaps from "../components/OfflineMaps";
import { useMe } from "../auth";
import { Units, fmtDistance, heightIn, heightOut, heightUnit, setUnits, useUnits } from "../units";

type S = any;

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="flabel">{label}</span>
      {children}
      {hint && <span className="fhint">{hint}</span>}
    </label>
  );
}

function Secret({ isSet, value, onChange, placeholder }: { isSet: boolean; value: string | null; onChange: (v: string | null) => void; placeholder: string }) {
  if (value === null) {
    return (
      <div className="row">
        <span className={`pill ${isSet ? "ok" : "plain"}`}>{isSet ? "Saved" : "Not set"}</span>
        <button type="button" onClick={() => onChange("")}>{isSet ? "Change or remove" : "Add"}</button>
      </div>
    );
  }
  return (
    <div className="row">
      <input type="password" style={{ flex: 1 }} value={value} placeholder={isSet ? "Leave empty to remove it" : placeholder} autoComplete="off" onChange={(e) => onChange(e.target.value)} />
      <button type="button" onClick={() => onChange(null)}>Keep old</button>
    </div>
  );
}

const STALE_LABEL: Record<number, string> = { 0: "Off", 1: "1 h", 6: "6 h", 24: "24 h", 72: "3 days", 168: "7 days" };

function IgnoredNodes({ onError }: { onError: (m: string) => void }) {
  const { data, reload } = useApi<any[]>("/api/ignored");
  return (
    <div className="card">
      <h2>Ignored nodes</h2>
      <div className="small muted" style={{ marginBottom: 10 }}>
        Hidden from the map, node and packet lists and analytics, and they never raise alerts. Their packets are still stored. Changes here apply right away.
      </div>
      {data?.length === 0 && <div className="small faint">No ignored nodes. Use Ignore on the Nodes page, a map popup or a packet to hide one.</div>}
      <div className="stack">
        {(data || []).map((n) => (
          <div key={n.public_key} className="row chanrow">
            <span style={{ flex: 1 }}>
              {n.name || <span className="faint">unnamed</span>}
              <span className="small faint mono"> {n.public_key.slice(0, 12)}</span>
              <span className="small faint"> · ignored {ago(n.added_at)}{n.last_seen ? ` · last heard ${ago(n.last_seen)}` : ""}</span>
            </span>
            <button type="button" onClick={() => unignoreNode(n.public_key).then(reload).catch((e) => onError(e.message))}>Show again</button>
          </div>
        ))}
      </div>
    </div>
  );
}

const SECTIONS: [string, string][] = [
  ["set-general", "General"],
  ["set-radio", "Radio and mesh"],
  ["set-fleet", "Fleet and alerts"],
  ["set-terrain", "Coverage model"],
  ["set-sharing", "Sharing"],
  ["set-access", "Access"],
];

const CHANNEL_HELP = "Public, a #hashtag channel, or a private channel as Name=32 hex digits.";

export default function Settings() {
  const units = useUnits();
  const [s, setS] = useState<S | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [newChan, setNewChan] = useState("");
  const [token, setTok] = useState<string | null>(null);
  const [discord, setDiscord] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [mqttPw, setMqttPw] = useState<Record<number, string | null>>({});
  const [metricsTok, setMetricsTok] = useState<string | null>(null);
  const [statsTok, setStatsTok] = useState<string | null>(null);
  const [haPw, setHaPw] = useState<string | null>(null);
  const [pgDsn, setPgDsn] = useState<string | null>(null);
  const [intMsg, setIntMsg] = useState("");
  const [base, setBase] = useState("");
  const me = useMe();

  const load = () =>
    api("/api/settings")
      .then((d) => (setS(d), setBase(JSON.stringify(d)), setTok(null), setDiscord(null), setSecret(null), setMqttPw({}), setMetricsTok(null), setStatsTok(null), setHaPw(null), setPgDsn(null)))
      .catch((e) => setErr(e.message));
  useEffect(() => {
    load();
  }, []);
  const loaded = s !== null;
  useEffect(() => {
    const id = location.hash.slice(1);
    if (loaded && id) requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ block: "start" }));
  }, [loaded]);

  if (!s) return err ? <div className="err">{err}</div> : <div className="empty">Loading…</div>;
  const set = (path: string, value: unknown) => {
    const [a, b] = path.split(".");
    setS((x: S) => ({ ...x, [a]: b ? { ...x[a], [b]: value } : value }));
    setMsg(null);
    setErr(null);
  };

  function save() {
    setBusy(true);
    const body = {
      station: { ...s.station, admin_token: token },
      radio: s.radio,
      channels: s.channels,
      fleet: s.fleet,
      terrain: s.terrain,
      integrations: { ...s.integrations, metrics_token: metricsTok, stats_push_token: statsTok },
      homeassistant: { ...s.homeassistant, password: haPw },
      postgres: { enabled: s.postgres.enabled, dsn: pgDsn },
      alerts: { webhook_url: s.alerts.webhook_url, discord_webhook: discord, webhook_secret: secret, radio_lost_minutes: s.alerts.radio_lost_minutes },
      mqtt: s.mqtt.map((m: any, i: number) => ({ ...m, password: mqttPw[i] === undefined ? null : mqttPw[i] })),
    };
    api("/api/settings", { method: "PUT", body })
      .then((r) => {
        if (token !== null) setToken(token);
        setS(r.settings);
        setBase(JSON.stringify(r.settings));
        setTok(null);
        setDiscord(null);
        setSecret(null);
        setMqttPw({});
        setMetricsTok(null);
        setStatsTok(null);
        setHaPw(null);
        setPgDsn(null);
        setMsg(r.restart_needed.length
          ? "Saved. The new address or port takes effect after you restart the station."
          : "Saved and applied.");
      })
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(false));
  }

  const r = s.radio;
  const secretsTyped = [token, discord, secret, metricsTok, statsTok, haPw, pgDsn].some((v) => v !== null) || Object.keys(mqttPw).length > 0;
  const dirty = secretsTyped || JSON.stringify(s) !== base;
  return (
    <>
      <div className="settop">
        <div className="top">
          <h1>Settings</h1>
          <span style={{ flex: 1 }} />
          {dirty && !busy && <span className="pill warn" role="status">Unsaved changes</span>}
          <button onClick={load} disabled={busy || !dirty}>Undo changes</button>
          <button className="primary" onClick={save} disabled={busy || !dirty}>{busy ? "Saving…" : "Save"}</button>
        </div>
        <nav className="jump" aria-label="Settings sections">
          {SECTIONS.map(([id, label]) => (
            <button key={id} type="button" className="small" onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })}>{label}</button>
          ))}
        </nav>
        {msg && <div className="note ok-note" role="status">{msg}</div>}
        {err && <div className="note err-note" role="alert">{err}</div>}
      </div>
      {!getToken() && s.station.admin_token_set && (
        <div className="note" style={{ marginBottom: 12 }}>This station has an admin token. Enter it under Access to save changes from this browser.</div>
      )}

      <section className="setsec" id="set-general" aria-labelledby="set-general-h">
        <h2 className="sechead" id="set-general-h">General</h2>
      <div className="card" id="set-look">
        <h2>Look and units</h2>
        <div className="small muted" style={{ marginBottom: 10 }}>Changes right away and is saved in this browser.</div>
        <div className="row" role="radiogroup" aria-label="Distance units" style={{ marginBottom: 12 }}>
          <span className="small muted">Distances</span>
          {([["metric", "Kilometres and metres"], ["imperial", "Miles and feet"]] as [Units, string][]).map(([u, l]) => (
            <button key={u} role="radio" aria-checked={units === u} className={units === u ? "primary" : ""} onClick={() => setUnits(u)}>{l}</button>
          ))}
        </div>
        <LookGallery />
      </div>

      {me?.is_admin && <UpdatesCard />}
      {me?.is_admin && <OfflineMaps />}
      <div className="grid2">
        <div className="card">
          <h2>Station</h2>
          <div className="formgrid">
            <Field label="Station name"><input value={s.station.name} onChange={(e) => set("station.name", e.target.value)} /></Field>
            <Field label="Days to keep packets"><input type="number" value={s.station.packet_retention_days} onChange={(e) => set("station.packet_retention_days", Number(e.target.value))} /></Field>
            <Field label="Days to keep fleet history"><input type="number" value={s.station.fleet_history_days} onChange={(e) => set("station.fleet_history_days", Number(e.target.value))} /></Field>
            <Field label="Mark nodes stale after" hint="Nodes and observers not heard for this long are labelled stale and faded on the map.">
              <select value={s.station.stale_after_hours} onChange={(e) => set("station.stale_after_hours", Number(e.target.value))}>
                {(s.stale_choices_hours || [0, 1, 6, 24, 72, 168]).map((h: number) => (
                  <option key={h} value={h}>{STALE_LABEL[h] ?? `${h} h`}</option>
                ))}
              </select>
            </Field>
          </div>
          <div className="small faint" style={{ marginTop: 8 }}>Settings file: <span className="mono">{s.config_path}</span></div>
        </div>

        <IgnoredNodes onError={setErr} />
      </div>
      </section>
      <section className="setsec" id="set-radio" aria-labelledby="set-radio-h">
        <h2 className="sechead" id="set-radio-h">Radio and mesh</h2>
      <div className="grid2">
        <RadioCard r={r} set={set} onSaved={(radio) => setS((x: S) => ({ ...x, radio }))} onError={setErr} />

        <div className="card">
          <h2>Channels</h2>
          <div className="small muted" style={{ marginBottom: 10 }}>Channels whose messages the station can read. {CHANNEL_HELP}</div>
          <div className="stack">
            {s.channels.map((c: string, i: number) => (
              <div key={`${c}-${i}`} className="row chanrow">
                <span className="mono" style={{ flex: 1 }}>{c.includes("=") ? `${c.split("=")[0]} (private key)` : c}</span>
                <button type="button" className="danger" onClick={() => set("channels", s.channels.filter((_: string, j: number) => j !== i))}>Remove</button>
              </div>
            ))}
            <form
              className="row"
              onSubmit={(e) => {
                e.preventDefault();
                const v = newChan.trim();
                if (!v) return setErr("Type a channel first.");
                if (s.channels.includes(v)) return setErr("That channel is already on the list.");
                set("channels", [...s.channels, v]);
                setNewChan("");
              }}
            >
              <input style={{ flex: 1 }} value={newChan} placeholder="#hikers" aria-label="New channel" onChange={(e) => setNewChan(e.target.value)} />
              <button type="submit">Add</button>
            </form>
          </div>
        </div>

        <div className="card">
          <h2>MQTT brokers</h2>
          <div className="small muted" style={{ marginBottom: 10 }}>Brokers carrying packets from observer radios elsewhere on the mesh.</div>
          <div className="stack">
            {s.mqtt.map((m: any, i: number) => {
              const upd = (k: string, v: unknown) => set("mqtt", s.mqtt.map((x: any, j: number) => (j === i ? { ...x, [k]: v } : x)));
              return (
                <div key={i} className="subcard">
                  <div className="formgrid">
                    <Field label="Name"><input value={m.name} onChange={(e) => upd("name", e.target.value)} /></Field>
                    <Field label="Address"><input value={m.host} onChange={(e) => upd("host", e.target.value)} /></Field>
                    <Field label="Port"><input type="number" value={m.port} onChange={(e) => upd("port", Number(e.target.value))} /></Field>
                    <Field label="Username"><input value={m.username} autoComplete="off" onChange={(e) => upd("username", e.target.value)} /></Field>
                  </div>
                  <Field label="Password">
                    <Secret isSet={m.password_set} value={mqttPw[i] === undefined ? null : mqttPw[i]} onChange={(v) => setMqttPw((p) => ({ ...p, [i]: v }))} placeholder="Password" />
                  </Field>
                  <Field label="Topics" hint="One per line.">
                    <textarea rows={2} className="mono" value={m.topics.join("\n")} onChange={(e) => upd("topics", e.target.value.split("\n").map((t) => t.trim()).filter(Boolean))} />
                  </Field>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <label className="row small"><input type="checkbox" checked={m.tls} onChange={(e) => upd("tls", e.target.checked)} /> Use TLS</label>
                    <button type="button" className="danger" onClick={() => set("mqtt", s.mqtt.filter((_: any, j: number) => j !== i))}>Remove broker</button>
                  </div>
                </div>
              );
            })}
            <button type="button" onClick={() => set("mqtt", [...s.mqtt, { name: `Broker ${s.mqtt.length + 1}`, host: "", port: 1883, username: "", password_set: false, tls: false, topics: ["meshcore/+/+/packets"] }])}>
              Add a broker
            </button>
          </div>
        </div>
      </div>
      </section>
      <section className="setsec" id="set-fleet" aria-labelledby="set-fleet-h">
        <h2 className="sechead" id="set-fleet-h">Fleet and alerts</h2>
      <div className="grid2">
        <div className="card">
          <h2>Fleet checks</h2>
          <div className="small muted" style={{ marginBottom: 10 }}>Every check goes over the air. Longer gaps save airtime on a busy mesh.</div>
          <div className="formgrid">
            <Field label="Minutes between checks"><input type="number" min={5} value={s.fleet.poll_minutes} onChange={(e) => set("fleet.poll_minutes", Number(e.target.value))} /></Field>
            <Field label="Seconds between repeaters"><input type="number" min={5} value={s.fleet.stagger_seconds} onChange={(e) => set("fleet.stagger_seconds", Number(e.target.value))} /></Field>
            <Field label="Quiet after (hours)"><input type="number" step="0.5" value={s.fleet.quiet_hours} onChange={(e) => set("fleet.quiet_hours", Number(e.target.value))} /></Field>
            <Field label="Low battery (volts)"><input type="number" step="0.05" value={s.fleet.low_battery_volts} onChange={(e) => set("fleet.low_battery_volts", Number(e.target.value))} /></Field>
            <Field label="Expected firmware" hint="Older repeaters get an alert. Empty = off."><input value={s.fleet.expected_firmware} placeholder="1.11.0" onChange={(e) => set("fleet.expected_firmware", e.target.value)} /></Field>
          </div>
        </div>

        <div className="card">
          <h2>Alerts</h2>
          <div className="stack">
            <Field label="Discord webhook">
              <Secret isSet={s.alerts.discord_webhook_set} value={discord} onChange={setDiscord} placeholder="https://discord.com/api/webhooks/…" />
            </Field>
            <Field label="Webhook URL" hint="Receives each alert as JSON.">
              <input value={s.alerts.webhook_url} placeholder="https://example.com/hook" onChange={(e) => set("alerts.webhook_url", e.target.value)} />
            </Field>
            <Field label="Webhook signing secret" hint="Signs the JSON in an X-Notoryx-Signature header.">
              <Secret isSet={s.alerts.webhook_secret_set} value={secret} onChange={setSecret} placeholder="Shared secret" />
            </Field>
            <Field label="Station radio lost after (minutes)" hint="Alert when the station's own radio stays disconnected this long. 0 = off.">
              <input type="number" min={0} max={1440} value={s.alerts.radio_lost_minutes ?? 5} onChange={(e) => set("alerts.radio_lost_minutes", Number(e.target.value))} />
            </Field>
            <div className="row">
              <button
                type="button"
                onClick={() =>
                  api("/api/alerts/test", { method: "POST" })
                    .then((res) => setMsg(Object.entries(res).map(([k, v]) => `${k === "discord" ? "Discord" : "Webhook"}: ${v ? "sent" : "failed"}`).join(" · ")))
                    .catch((e) => setErr(e.message))
                }
              >
                Send a test alert
              </button>
              <span className="small faint">Uses the saved settings.</span>
            </div>
          </div>
        </div>
      </div>
      </section>
      <section className="setsec" id="set-terrain" aria-labelledby="set-terrain-h">
        <h2 className="sechead" id="set-terrain-h">Coverage model</h2>
      <div className="grid2">
        <div className="card">
          <h2>Terrain and radio model</h2>
          <div className="small muted" style={{ marginBottom: 10 }}>
            Used by Coverage → Terrain, Link profile and Plan relays. Set these to match your radios so predictions are close.
          </div>
          <div className="formgrid">
            <Field label="Elevation data" hint="Copernicus DEM. Downloads on first use of an area: about 5 MB (90 m) or 40 MB (30 m) per tile.">
              <select value={s.terrain.source} onChange={(e) => set("terrain.source", e.target.value)}>
                <option value="glo90">90 m grid (smaller)</option>
                <option value="glo30">30 m grid (sharper)</option>
              </select>
            </Field>
            <Field label="Frequency (MHz)"><input type="number" step="0.001" value={s.terrain.freq_mhz} onChange={(e) => set("terrain.freq_mhz", Number(e.target.value))} /></Field>
            <Field label="Bandwidth (kHz)">
              <select value={s.terrain.bandwidth_khz} onChange={(e) => set("terrain.bandwidth_khz", Number(e.target.value))}>
                {[7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125, 250, 500].map((b) => <option key={b} value={b}>{b}</option>)}
              </select>
            </Field>
            <Field label="Spreading factor">
              <select value={s.terrain.spreading_factor} onChange={(e) => set("terrain.spreading_factor", Number(e.target.value))}>
                {[5, 6, 7, 8, 9, 10, 11, 12].map((f) => <option key={f} value={f}>SF{f}</option>)}
              </select>
            </Field>
            <Field label="Transmit power (dBm)"><input type="number" value={s.terrain.tx_power_dbm} onChange={(e) => set("terrain.tx_power_dbm", Number(e.target.value))} /></Field>
            <Field label="Antenna gain (dBi)"><input type="number" step="0.5" value={s.terrain.antenna_gain_dbi} onChange={(e) => set("terrain.antenna_gain_dbi", Number(e.target.value))} /></Field>
            <Field label="Cable loss (dB)"><input type="number" step="0.5" value={s.terrain.cable_loss_db} onChange={(e) => set("terrain.cable_loss_db", Number(e.target.value))} /></Field>
            <Field label="Receiver noise figure (dB)"><input type="number" step="0.5" value={s.terrain.noise_figure_db} onChange={(e) => set("terrain.noise_figure_db", Number(e.target.value))} /></Field>
            <Field label={`Repeater antenna height (${heightUnit(units)})`}><input type="number" step={units === "imperial" ? 1 : 0.5} value={heightIn(s.terrain.repeater_height_m, units)} onChange={(e) => set("terrain.repeater_height_m", heightOut(Number(e.target.value), units))} /></Field>
            <Field label={`Handheld antenna height (${heightUnit(units)})`}><input type="number" step={units === "imperial" ? 1 : 0.5} value={heightIn(s.terrain.client_height_m, units)} onChange={(e) => set("terrain.client_height_m", heightOut(Number(e.target.value), units))} /></Field>
            <Field label="Calibrate from measured links" hint="Shifts predictions by the median error on links the station has heard.">
              <input type="checkbox" checked={!!s.terrain.use_calibration} onChange={(e) => set("terrain.use_calibration", e.target.checked)} />
            </Field>
          </div>
        </div>
      </div>
      </section>
      <section className="setsec" id="set-sharing" aria-labelledby="set-sharing-h">
        <h2 className="sechead" id="set-sharing-h">Sharing and integrations</h2>
      <div className="grid2">
        <div className="card">
          <h2>Public map</h2>
          <div className="small muted" style={{ marginBottom: 10 }}>
            A read-only map at <a href="/public">/public</a> that works without signing in. It shows repeaters with rounded positions and nothing about your fleet.
          </div>
          <div className="formgrid">
            <Field label="Public map"><input type="checkbox" checked={!!s.integrations.public_map} onChange={(e) => set("integrations.public_map", e.target.checked)} /></Field>
            <Field label="Title"><input value={s.integrations.public_title} placeholder={s.station.name} onChange={(e) => set("integrations.public_title", e.target.value)} /></Field>
            <Field label="Position detail">
              <select value={s.integrations.public_precision} onChange={(e) => set("integrations.public_precision", Number(e.target.value))}>
                {[10000, 1000, 100, 10].map((m, i) => <option key={m} value={i + 1}>About {fmtDistance(m, units)}</option>)}
              </select>
            </Field>
            <Field label="Show companions too"><input type="checkbox" checked={!!s.integrations.public_show_companions} onChange={(e) => set("integrations.public_show_companions", e.target.checked)} /></Field>
          </div>
        </div>

        <div className="card">
          <h2>Live network stats</h2>
          <div className="small muted" style={{ marginBottom: 10 }}>
            Every 15 minutes, sends 7-day counts (repeaters, nodes, countries, links) to a stats relay for a public "live network" page.
            Only a short key prefix and country code per node are sent, never names, positions, messages or fleet data. Ignored nodes are left out.
          </div>
          <div className="formgrid">
            <Field label="Share stats"><input type="checkbox" checked={!!s.integrations.stats_push} onChange={(e) => set("integrations.stats_push", e.target.checked)} /></Field>
            <Field label="Relay address"><input value={s.integrations.stats_push_url} placeholder="https://stats.meshmayhem.org/v1/push" onChange={(e) => set("integrations.stats_push_url", e.target.value)} /></Field>
            <Field label="Relay token"><Secret isSet={s.integrations.stats_push_token_set} value={statsTok} onChange={setStatsTok} placeholder="24+ characters, from the relay owner" /></Field>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button type="button" onClick={() => api<any>("/api/integrations/stats/push", { method: "POST" }).then((r) => setIntMsg(`Sent: ${r.sent.repeaters} repeaters, ${r.sent.nodes} nodes, ${r.sent.countries} countries, ${r.sent.links} links.`)).catch((e) => setIntMsg(e.message))}>Send now</button>
            <span className="small faint">Save first.</span>
          </div>
        </div>

        <div className="card">
          <h2>Exports and metrics</h2>
          <div className="small muted" style={{ marginBottom: 10 }}>Download what the station knows, or let Prometheus read it.</div>
          <div className="row" style={{ marginBottom: 10 }}>
            <a className="btn" href="/api/export/nodes.geojson">Nodes (GeoJSON)</a>
            <a className="btn" href="/api/export/links.geojson">Links (GeoJSON)</a>
            <a className="btn" href="/api/export/nodes.gpx">Nodes (GPX)</a>
          </div>
          <Field label="Prometheus" hint={me?.accounts_on ? "With accounts on, Prometheus needs this token as a bearer token." : "Open to anyone who can reach the dashboard. Turn accounts on to protect it."}>
            <span className="mono small">{location.origin}/metrics</span>
          </Field>
          <Field label="Metrics token">
            <Secret isSet={s.integrations.metrics_token_set} value={metricsTok} onChange={setMetricsTok} placeholder="12+ characters" />
          </Field>
        </div>

        <div className="card">
          <h2>Home Assistant</h2>
          <div className="small muted" style={{ marginBottom: 10 }}>
            Publishes station sensors and one device per fleet repeater (online, battery, noise floor, SNR) to the MQTT broker Home Assistant uses. Discovery adds them automatically.
          </div>
          <div className="formgrid">
            <Field label="Turned on"><input type="checkbox" checked={!!s.homeassistant.enabled} onChange={(e) => set("homeassistant.enabled", e.target.checked)} /></Field>
            <Field label="Broker address"><input value={s.homeassistant.host} placeholder="homeassistant.local" onChange={(e) => set("homeassistant.host", e.target.value)} /></Field>
            <Field label="Port"><input type="number" value={s.homeassistant.port} onChange={(e) => set("homeassistant.port", Number(e.target.value))} /></Field>
            <Field label="Username"><input value={s.homeassistant.username} autoComplete="off" onChange={(e) => set("homeassistant.username", e.target.value)} /></Field>
            <Field label="Password"><Secret isSet={s.homeassistant.password_set} value={haPw} onChange={setHaPw} placeholder="broker password" /></Field>
            <Field label="TLS"><input type="checkbox" checked={!!s.homeassistant.tls} onChange={(e) => set("homeassistant.tls", e.target.checked)} /></Field>
            <Field label="Discovery prefix"><input value={s.homeassistant.prefix} onChange={(e) => set("homeassistant.prefix", e.target.value)} /></Field>
            <Field label="Seconds between updates"><input type="number" min={15} value={s.homeassistant.interval_s} onChange={(e) => set("homeassistant.interval_s", Number(e.target.value))} /></Field>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button type="button" onClick={() => api<any>("/api/integrations/homeassistant/test", { method: "POST" }).then((r) => setIntMsg(`Sent ${r.sent} sensors to Home Assistant.`)).catch((e) => setIntMsg(e.message))}>Send now</button>
            <span className="small faint">Save first.</span>
          </div>
        </div>

        <div className="card">
          <h2>PostgreSQL archive</h2>
          <div className="small muted" style={{ marginBottom: 10 }}>
            Copies packets, messages, fleet history and alerts to PostgreSQL every minute, so history outlasts this station's own retention. Needs <span className="mono">pip install "notoryx-ops[postgres]"</span>.
          </div>
          <div className="formgrid">
            <Field label="Turned on"><input type="checkbox" checked={!!s.postgres.enabled} onChange={(e) => set("postgres.enabled", e.target.checked)} /></Field>
            <Field label="Connection"><Secret isSet={s.postgres.dsn_set} value={pgDsn} onChange={setPgDsn} placeholder="postgresql://user:pass@host/db" /></Field>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button type="button" onClick={() => api<any>("/api/integrations/postgres/run", { method: "POST" }).then((r) => setIntMsg(`Copied ${r.copied} new rows to PostgreSQL.`)).catch((e) => setIntMsg(e.message))}>Copy now</button>
            <span className="small faint">Save first.</span>
          </div>
          {intMsg && <div className="small" role="status" style={{ marginTop: 6 }}>{intMsg}</div>}
        </div>
      </div>
      </section>
      <section className="setsec" id="set-access" aria-labelledby="set-access-h">
        <h2 className="sechead" id="set-access-h">Access and accounts</h2>
      <div className="grid2">
        <div className="card">
          <h2>Access</h2>
          <div className="small muted" style={{ marginBottom: 10 }}>
            Let other computers on your network open this dashboard. Changes from them need the admin token. Takes effect after a restart.
          </div>
          <div className="seg" role="radiogroup" aria-label="Who can open the dashboard">
            {[["127.0.0.1", "This computer only"], ["0.0.0.0", "My network"]].map(([v, l]) => (
              <button key={v} type="button" role="radio" aria-checked={s.station.host === v} className={s.station.host === v ? "on" : ""} onClick={() => set("station.host", v)}>{l}</button>
            ))}
          </div>
          <div className="formgrid" style={{ marginTop: 10 }}>
            <Field label="Port"><input type="number" value={s.station.port} onChange={(e) => set("station.port", Number(e.target.value))} /></Field>
          </div>
          <Field label="Admin token" hint="At least 12 characters. Saving it here also remembers it in this browser.">
            <Secret isSet={s.station.admin_token_set} value={token} onChange={setTok} placeholder="A long random phrase" />
          </Field>
          {s.station.admin_token_set && (
            <form className="row" style={{ marginTop: 8 }} onSubmit={(e) => { e.preventDefault(); const v = (e.currentTarget.elements.namedItem("t") as HTMLInputElement).value; setToken(v.trim()); setMsg("Token remembered in this browser."); }}>
              <input name="t" type="password" placeholder="Enter the token to make changes from here" style={{ flex: 1 }} autoComplete="off" defaultValue={getToken()} />
              <button type="submit">Remember</button>
            </form>
          )}
        </div>
      </div>
      <Accounts me={me} />
      </section>

    </>
  );
}

import { ReactNode, useEffect, useState } from "react";
import { ago, api, getToken, setToken, unignoreNode, useApi } from "../api";
import { LookGallery } from "../components/ThemePicker";

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

const CHANNEL_HELP = "Public, a #hashtag channel, or a private channel as Name=32 hex digits.";

export default function Settings() {
  const [s, setS] = useState<S | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ports, setPorts] = useState<any[] | null>(null);
  const [ble, setBle] = useState<any[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [newChan, setNewChan] = useState("");
  const [token, setTok] = useState<string | null>(null);
  const [discord, setDiscord] = useState<string | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [mqttPw, setMqttPw] = useState<Record<number, string | null>>({});

  const load = () =>
    api("/api/settings")
      .then((d) => (setS(d), setTok(null), setDiscord(null), setSecret(null), setMqttPw({})))
      .catch((e) => setErr(e.message));
  useEffect(() => {
    load();
  }, []);

  if (!s) return err ? <div className="err">{err}</div> : <div className="empty">Loading…</div>;
  const set = (path: string, value: unknown) => {
    const [a, b] = path.split(".");
    setS((x: S) => ({ ...x, [a]: b ? { ...x[a], [b]: value } : value }));
    setMsg(null);
    setErr(null);
  };

  function scanPorts() {
    api<any[]>("/api/settings/serial-ports").then(setPorts).catch((e) => setErr(e.message));
  }
  function scanBle() {
    setScanning(true);
    api<any[]>("/api/settings/ble-scan")
      .then(setBle)
      .catch((e) => setErr(e.message))
      .finally(() => setScanning(false));
  }

  function save() {
    setBusy(true);
    const body = {
      station: { ...s.station, admin_token: token },
      radio: s.radio,
      channels: s.channels,
      fleet: s.fleet,
      alerts: { webhook_url: s.alerts.webhook_url, discord_webhook: discord, webhook_secret: secret },
      mqtt: s.mqtt.map((m: any, i: number) => ({ ...m, password: mqttPw[i] === undefined ? null : mqttPw[i] })),
    };
    api("/api/settings", { method: "PUT", body })
      .then((r) => {
        if (token !== null) setToken(token);
        setS(r.settings);
        setTok(null);
        setDiscord(null);
        setSecret(null);
        setMqttPw({});
        setMsg(r.restart_needed.length
          ? "Saved. The new address or port takes effect after you restart the station."
          : "Saved and applied.");
      })
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(false));
  }

  const r = s.radio;
  return (
    <>
      <div className="top">
        <h1>Settings</h1>
        <span style={{ flex: 1 }} />
        <button onClick={load} disabled={busy}>Undo changes</button>
        <button className="primary" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
      </div>
      {msg && <div className="note ok-note" role="status" style={{ marginBottom: 12 }}>{msg}</div>}
      {err && <div className="note err-note" role="alert" style={{ marginBottom: 12 }}>{err}</div>}
      {!getToken() && s.station.admin_token_set && (
        <div className="note" style={{ marginBottom: 12 }}>This station has an admin token. Enter it under Access to save changes from this browser.</div>
      )}

      <div className="card">
        <h2>Look</h2>
        <div className="small muted" style={{ marginBottom: 10 }}>Changes right away and is saved on this computer.</div>
        <LookGallery />
      </div>

      <div className="grid2">
        <div className="card">
          <h2>Radio</h2>
          <div className="small muted" style={{ marginBottom: 10 }}>The MeshCore companion radio this station listens with and uses to reach your repeaters. Only one app can use a Bluetooth radio at a time.</div>
          <div className="seg" role="radiogroup" aria-label="Connection">
            {[["none", "None"], ["serial", "USB"], ["ble", "Bluetooth"], ["tcp", "Wi-Fi / TCP"]].map(([v, l]) => (
              <button key={v} type="button" role="radio" aria-checked={r.transport === v} className={r.transport === v ? "on" : ""} onClick={() => set("radio.transport", v)}>{l}</button>
            ))}
          </div>
          {r.transport === "serial" && (
            <div className="stack" style={{ marginTop: 10 }}>
              <Field label="USB port">
                <div className="row">
                  <input style={{ flex: 1 }} className="mono" value={r.serial_port} onChange={(e) => set("radio.serial_port", e.target.value)} />
                  <button type="button" onClick={scanPorts}>Find ports</button>
                </div>
              </Field>
              {ports && (
                <div className="picklist">
                  {ports.length === 0 && <div className="small muted">No USB serial ports found.</div>}
                  {ports.map((p) => (
                    <button key={p.device} type="button" className={p.device === r.serial_port ? "on" : ""} onClick={() => set("radio.serial_port", p.device)}>
                      <span className="mono">{p.device}</span>
                      <span className="small faint">{[p.description, p.manufacturer].filter(Boolean).join(" · ")}</span>
                    </button>
                  ))}
                </div>
              )}
              <Field label="Baud rate"><input type="number" value={r.baudrate} onChange={(e) => set("radio.baudrate", Number(e.target.value))} /></Field>
            </div>
          )}
          {r.transport === "ble" && (
            <div className="stack" style={{ marginTop: 10 }}>
              <Field label="Bluetooth radio" hint="Leave empty to use the first MeshCore radio found.">
                <div className="row">
                  <input style={{ flex: 1 }} className="mono" value={r.ble_address} onChange={(e) => set("radio.ble_address", e.target.value)} />
                  <button type="button" onClick={scanBle} disabled={scanning}>{scanning ? "Scanning…" : "Scan"}</button>
                </div>
              </Field>
              {ble && (
                <div className="picklist">
                  {ble.length === 0 && <div className="small muted">No MeshCore radios heard. Make sure it's on and not connected to another app.</div>}
                  {ble.map((d) => (
                    <button key={d.address} type="button" className={d.address === r.ble_address ? "on" : ""} onClick={() => set("radio.ble_address", d.address)}>
                      <span>{d.name}</span>
                      <span className="small faint mono">{d.address} · {d.rssi} dBm</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {r.transport === "tcp" && (
            <div className="row" style={{ marginTop: 10, alignItems: "flex-start" }}>
              <Field label="Address"><input value={r.tcp_host} placeholder="192.168.1.50" onChange={(e) => set("radio.tcp_host", e.target.value)} /></Field>
              <Field label="Port"><input type="number" style={{ width: 100 }} value={r.tcp_port} onChange={(e) => set("radio.tcp_port", Number(e.target.value))} /></Field>
            </div>
          )}
          <div style={{ marginTop: 10 }}>
            <Field label="Observer name" hint="How this radio shows up on the Observers page.">
              <input value={r.observer_name} onChange={(e) => set("radio.observer_name", e.target.value)} />
            </Field>
          </div>
        </div>

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
      <div className="row" style={{ justifyContent: "flex-end", marginTop: 4 }}>
        {msg && <span className="small" style={{ color: "var(--ok)" }} role="status">{msg}</span>}
        {err && <span className="small" style={{ color: "var(--bad)" }} role="alert">{err}</span>}
        <button onClick={load} disabled={busy}>Undo changes</button>
        <button className="primary" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </>
  );
}

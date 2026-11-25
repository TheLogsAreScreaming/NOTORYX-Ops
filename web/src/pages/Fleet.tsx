import { useMemo, useState } from "react";
import Chart from "../components/Chart";
import { Node, ago, api, duration, fmt, useApi, useLive } from "../api";
import { currentTheme } from "../themes";
import { useLook } from "../components/ThemePicker";

type Rep = {
  id: number;
  public_key: string;
  name: string;
  notes: string;
  enabled: number;
  has_password: boolean;
  firmware: string | null;
  last_heard: number | null;
  last_poll: any;
  last_ok: any;
  alerts: { kind: string; severity: string; message: string }[];
  node: { prefix: string } | null;
  watch_only: number;
  relay: { packets_24h: number; last_relay: number | null; neighbours: { prefix: string; packets: number }[] };
};

function statusOf(r: Rep): [string, string] {
  if (!r.enabled) return ["plain", "Paused"];
  if (r.watch_only) return r.alerts.find((a) => a.kind === "quiet") ? ["bad", "Quiet"] : r.last_heard ? ["info", "Watching"] : ["plain", "Not heard yet"];
  const sev = (k: string) => r.alerts.find((a) => a.kind === k);
  if (sev("quiet")) return ["bad", "Quiet"];
  if (sev("low_battery")) return ["warn", "Low battery"];
  if (sev("battery_falling")) return ["warn", "Battery falling"];
  if (sev("firmware")) return ["info", "Firmware behind"];
  if (r.last_poll && !r.last_poll.ok) return ["warn", "Last poll failed"];
  if (!r.last_ok) return ["plain", "Not polled yet"];
  return ["ok", "Healthy"];
}

function battPct(mv: number | null | undefined) {
  if (!mv) return null;
  return Math.max(0, Math.min(100, ((mv - 3300) / (4200 - 3300)) * 100));
}

/** A repeater the station only listens to: what it heard without asking. */
function Watched({ rep }: { rep: Rep }) {
  const r = rep.relay;
  return (
    <div className="card">
      <h2>{rep.name} <span className="pill info">Watch only</span></h2>
      <table>
        <tbody>
          <tr><th>Last heard</th><td>{ago(rep.last_heard)}</td></tr>
          <tr><th>Packets it passed on (24 h)</th><td>{r?.packets_24h ?? 0}</td></tr>
          <tr><th>Last one</th><td>{ago(r?.last_relay)}</td></tr>
          <tr><th>Next to it in paths</th><td className="mono">{r?.neighbours?.length ? r.neighbours.map((n) => `${n.prefix.toUpperCase()} (${n.packets})`).join(", ") : "—"}</td></tr>
        </tbody>
      </table>
      <div className="small faint" style={{ marginTop: 8 }}>
        Worked out from the paths of packets the station heard. Short prefixes can be shared by more than one repeater, so these are estimates. Nothing is ever sent to this repeater.
      </div>
    </div>
  );
}

function History({ rep }: { rep: Rep }) {
  const [days, setDays] = useState(7);
  const { data } = useApi<any[]>(`/api/fleet/${rep.id}/history?days=${days}`);
  const look = useLook();
  const ok = (data || []).filter((r) => r.ok);
  const x = useMemo(() => ok.map((r) => r.polled_at), [data]); // eslint-disable-line react-hooks/exhaustive-deps
  const batt = useMemo(() => [{ label: "Battery (V)", color: currentTheme().chart[0], values: ok.map((r) => (r.battery_mv ? r.battery_mv / 1000 : null)) }], [data, look]); // eslint-disable-line react-hooks/exhaustive-deps
  const noise = useMemo(() => [{ label: "Noise floor (dBm)", color: currentTheme().chart[1], values: ok.map((r) => r.noise_floor) }], [data, look]); // eslint-disable-line react-hooks/exhaustive-deps
  const air = useMemo(() => {
    const vals = ok.map((r, i) => {
      if (i === 0 || r.airtime_s == null || ok[i - 1].airtime_s == null) return null;
      const dt = r.polled_at - ok[i - 1].polled_at;
      const d = r.airtime_s - ok[i - 1].airtime_s;
      return d >= 0 && dt > 0 ? (d / dt) * 100 : null;
    });
    return [{ label: "Transmit airtime (%)", color: currentTheme().chart[2], values: vals }];
  }, [data, look]); // eslint-disable-line react-hooks/exhaustive-deps
  const fails = (data || []).filter((r) => !r.ok);
  const nb = ok.slice().reverse().find((r) => r.neighbours)?.neighbours as any[] | undefined;
  return (
    <div className="card">
      <h2>
        {rep.name} history
        <span style={{ flex: 1 }} />
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="History range">
          <option value={1}>1 day</option>
          <option value={7}>7 days</option>
          <option value={30}>30 days</option>
          <option value={365}>1 year</option>
        </select>
      </h2>
      <div className="grid2">
        <Chart x={x} series={batt} />
        <Chart x={x} series={noise} />
        <Chart x={x} series={air} />
        <div>
          <div className="small muted" style={{ marginBottom: 6 }}>Neighbours from the last check</div>
          {!nb?.length && <div className="empty small">Not checked yet. Neighbours are read every 6 hours.</div>}
          {nb && nb.length > 0 && (
            <table>
              <thead><tr><th>Key</th><th>Heard</th><th>SNR</th></tr></thead>
              <tbody>
                {nb.map((n) => (
                  <tr key={n.pubkey}><td className="mono">{n.pubkey}</td><td>{duration(n.secs_ago)} ago</td><td>{n.snr}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="small muted" style={{ marginTop: 10 }}>
            {fails.length} failed polls in this range{fails.length ? `, last: ${fails[fails.length - 1].error}` : ""}
          </div>
        </div>
      </div>
    </div>
  );
}

function AddRepeater({ known, onDone }: { known: Node[]; onDone: () => void }) {
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [watch, setWatch] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="card">
      <h2>Add a repeater</h2>
      <form
        className="stack"
        onSubmit={(e) => {
          e.preventDefault();
          if (!/^[0-9a-fA-F]{64}$/.test(key.trim())) return setErr("Pick a heard repeater or paste its 64-character public key.");
          if (!name.trim()) return setErr("Give it a name.");
          api("/api/fleet", { method: "POST", body: { public_key: key.trim().toLowerCase(), name: name.trim(), password: watch ? "" : pw, watch_only: watch } })
            .then(() => (setKey(""), setName(""), setPw(""), setWatch(false), setErr(null), onDone()))
            .catch((e2) => setErr(e2.message));
        }}
      >
        <select
          aria-label="Heard repeaters"
          value=""
          onChange={(e) => {
            const n = known.find((k) => k.public_key === e.target.value);
            if (n) {
              setKey(n.public_key);
              setName(n.name || "");
              setErr(null);
            }
          }}
        >
          <option value="">Pick from repeaters the station has heard…</option>
          {known.map((n) => (
            <option key={n.public_key} value={n.public_key}>
              {n.name || n.public_key.slice(0, 8)} ({n.prefix.toUpperCase()}, {ago(n.last_seen)})
            </option>
          ))}
        </select>
        <input className="mono" placeholder="Public key (64 hex characters)" value={key} onChange={(e) => (setKey(e.target.value), setErr(null))} aria-label="Public key" />
        <div className="row">
          <input style={{ flex: 1 }} placeholder="Name" value={name} onChange={(e) => (setName(e.target.value), setErr(null))} aria-label="Name" />
          {!watch && <input style={{ flex: 1 }} type="password" placeholder="Admin password (optional)" value={pw} onChange={(e) => setPw(e.target.value)} aria-label="Admin password" autoComplete="new-password" />}
          <button type="submit" className="primary">Add</button>
        </div>
        <label className="row small" style={{ gap: 6 }}>
          <input type="checkbox" checked={watch} onChange={(e) => setWatch(e.target.checked)} />
          Watch only: it isn't mine, never send it anything
        </label>
        <div className="small faint">
          {watch
            ? "The station only listens: when it was last heard, how many packets it passed on and its neighbours. You still get an alert if it goes quiet."
            : "For a repeater you own. The password stays in this station's database and is only sent to the repeater over the mesh. Without it you get status only, no firmware check or settings."}
        </div>
        {err && <div className="err">{err}</div>}
      </form>
    </div>
  );
}

function Bulk({ reps }: { reps: Rep[] }) {
  const { data: keys } = useApi<string[]>("/api/fleet/bulk/settings");
  const [ids, setIds] = useState<number[]>([]);
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [preview, setPreview] = useState<any[] | null>(null);
  const [result, setResult] = useState<any[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toggle = (id: number) => setIds((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const reset = () => (setPreview(null), setResult(null), setErr(null));
  const usable = reps.filter((r) => r.has_password && r.enabled);
  return (
    <div className="card">
      <h2>Bulk settings</h2>
      <div className="small muted" style={{ marginBottom: 8 }}>
        Change one setting on several repeaters. You'll see each current value before anything is sent. Only repeaters with a saved admin password are listed.
      </div>
      <div className="row" style={{ marginBottom: 8 }}>
        {usable.map((r) => (
          <label key={r.id} className="row small" style={{ gap: 4 }}>
            <input type="checkbox" checked={ids.includes(r.id)} onChange={() => (toggle(r.id), reset())} />
            {r.name}
          </label>
        ))}
        {usable.length === 0 && <span className="faint small">No repeaters with a saved password yet.</span>}
      </div>
      <div className="row">
        <select value={key} onChange={(e) => (setKey(e.target.value), reset())} aria-label="Setting">
          <option value="">Setting…</option>
          {(keys || []).map((k) => <option key={k}>{k}</option>)}
        </select>
        <input value={value} onChange={(e) => (setValue(e.target.value), reset())} placeholder="New value" aria-label="New value" />
        <button
          disabled={busy}
          onClick={() => {
            if (!ids.length || !key || !value.trim()) return setErr("Pick repeaters, a setting and a value first.");
            setBusy(true);
            api<any[]>("/api/fleet/bulk/preview", { method: "POST", body: { ids, key, value } })
              .then((p) => (setPreview(p), setErr(null)))
              .catch((e) => setErr(e.message))
              .finally(() => setBusy(false));
          }}
        >
          {busy && !preview ? "Reading current values…" : "Preview"}
        </button>
      </div>
      {err && <div className="err" style={{ marginTop: 6 }}>{err}</div>}
      {preview && (
        <div style={{ marginTop: 10 }}>
          <table>
            <thead><tr><th>Repeater</th><th>Now</th><th>After</th><th></th></tr></thead>
            <tbody>
              {preview.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td className="mono">{p.error ? <span className="err">{p.error}</span> : p.current}</td>
                  <td className="mono">{p.new}</td>
                  <td>{p.error ? "" : p.changes ? <span className="pill warn">Changes</span> : <span className="pill plain">Same</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!result && (
            <div className="row" style={{ marginTop: 8 }}>
              <button
                className="primary"
                disabled={busy || !preview.some((p) => p.changes)}
                onClick={() => {
                  const go = preview.filter((p) => p.changes).map((p) => p.id);
                  setBusy(true);
                  api<any[]>("/api/fleet/bulk/apply", { method: "POST", body: { ids: go, key, value, confirm: true } })
                    .then(setResult)
                    .catch((e) => setErr(e.message))
                    .finally(() => setBusy(false));
                }}
              >
                {busy ? "Sending…" : `Send to ${preview.filter((p) => p.changes).length} repeaters`}
              </button>
              <button onClick={reset}>Cancel</button>
            </div>
          )}
        </div>
      )}
      {result && (
        <div className="stack" style={{ marginTop: 8 }}>
          {result.map((r) => (
            <div key={r.id} className="row small">
              <span className={`pill ${r.ok ? "ok" : "bad"}`}>{r.ok ? "Done" : "Failed"}</span>
              {r.name}: <span className="mono">{r.reply || r.error}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Fleet() {
  const { data, reload, error } = useApi<Rep[]>("/api/fleet", 30000);
  const { data: info, reload: reloadInfo } = useApi<any>("/api/station", 10000);
  const { data: nodes } = useApi<Node[]>("/api/nodes");
  const [sel, setSel] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useLive((ev) => {
    if (ev.type === "fleet_status" || ev.type === "fleet") (reload(), reloadInfo());
  });
  const reps = data || [];
  const owned = new Set(reps.map((r) => r.public_key));
  const known = (nodes || []).filter((n) => (n.node_type === "repeater" || n.node_type === "room") && !owned.has(n.public_key));
  const fl = info?.fleet;
  const counts = {
    healthy: reps.filter((r) => statusOf(r)[1] === "Healthy").length,
    quiet: reps.filter((r) => statusOf(r)[1] === "Quiet").length,
    battery: reps.filter((r) => ["Low battery", "Battery falling"].includes(statusOf(r)[1])).length,
  };
  const selected = reps.find((r) => r.id === sel);

  return (
    <>
      <div className="top">
        <h1>My fleet</h1>
        <span className="small muted">
          {fl?.polling ? "Polling now…" : `Last poll ${ago(fl?.last_round)} · next ${fl?.next_round ? new Date(fl.next_round * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "soon"} · ${fl?.stagger_seconds ?? "?"} s between repeaters`}
        </span>
        <span style={{ flex: 1 }} />
        <button
          disabled={fl?.polling}
          onClick={() =>
            api("/api/fleet/poll", { method: "POST" })
              .then((r) => setMsg(r.started ? "Poll started." : "A poll is already running."))
              .catch((e) => setMsg(e.message))
          }
        >
          Poll all now
        </button>
      </div>
      {msg && <div className="note" style={{ marginBottom: 10 }}>{msg}</div>}
      {error && <div className="err">{error}</div>}
      <div className="tiles">
        <div className="tile"><div className="k">Repeaters</div><div className="v">{reps.length}</div></div>
        <div className="tile"><div className="k">Healthy</div><div className="v" style={{ color: "var(--ok)" }}>{counts.healthy}</div></div>
        <div className="tile"><div className="k">Quiet</div><div className="v" style={{ color: counts.quiet ? "var(--bad)" : undefined }}>{counts.quiet}</div></div>
        <div className="tile"><div className="k">Battery alerts</div><div className="v" style={{ color: counts.battery ? "var(--warn)" : undefined }}>{counts.battery}</div></div>
      </div>
      <div className="card tablewrap" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Repeater</th><th>Status</th><th>Battery</th><th>Noise</th><th>Last SNR</th><th>Uptime</th><th>Firmware</th><th>Last heard</th><th></th>
            </tr>
          </thead>
          <tbody>
            {reps.map((r) => {
              const [cls, label] = statusOf(r);
              const mv = r.last_ok?.battery_mv;
              const pct = battPct(mv);
              return (
                <tr key={r.id} className={`click ${sel === r.id ? "sel" : ""}`} onClick={() => setSel(r.id)}>
                  <td>
                    <div>{r.name}</div>
                    <div className="small faint mono">{(r.node?.prefix || r.public_key.slice(0, 2)).toUpperCase()} · {r.public_key.slice(0, 8)}</div>
                  </td>
                  <td><span className={`pill ${cls}`}>{label}</span></td>
                  <td>
                    {mv ? `${(mv / 1000).toFixed(2)} V` : "—"}
                    {pct != null && (
                      <div className="bar"><i style={{ width: `${pct}%`, background: pct < 25 ? "var(--bad)" : pct < 50 ? "var(--warn)" : "var(--ok)" }} /></div>
                    )}
                  </td>
                  <td>{fmt(r.last_ok?.noise_floor)}</td>
                  <td>{fmt(r.last_ok?.last_snr, 1)}</td>
                  <td>{duration(r.last_ok?.uptime_s)}</td>
                  <td className="mono small">{r.watch_only ? <span className="faint">relayed {r.relay?.packets_24h ?? 0} today</span> : r.firmware || "—"}</td>
                  <td>{ago(r.last_heard)}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <div className="row" style={{ flexWrap: "nowrap" }}>
                      <button
                        onClick={() => api(`/api/fleet/${r.id}`, { method: "PATCH", body: { enabled: !r.enabled } }).then(reload).catch((e) => setMsg(e.message))}
                      >
                        {r.enabled ? "Pause" : "Resume"}
                      </button>
                      {!r.watch_only && <button
                        onClick={() => {
                          const pw = window.prompt(`Admin password for ${r.name} (leave empty to remove it)`);
                          if (pw === null) return;
                          api(`/api/fleet/${r.id}`, { method: "PATCH", body: { password: pw } }).then(reload).catch((e) => setMsg(e.message));
                        }}
                      >
                        Password
                      </button>}
                      <button
                        className="danger"
                        onClick={() => {
                          if (window.confirm(`Remove ${r.name} and its history from your fleet?`))
                            api(`/api/fleet/${r.id}`, { method: "DELETE" }).then(() => (setSel(null), reload())).catch((e) => setMsg(e.message));
                        }}
                      >
                        Remove
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {reps.length === 0 && <div className="empty" style={{ padding: 16 }}>No repeaters yet. Add the ones you own below.</div>}
      </div>
      {selected && (selected.watch_only ? <Watched rep={selected} /> : <History rep={selected} />)}
      {selected?.alerts.length ? (
        <div className="card">
          <h2>Open alerts for {selected.name}</h2>
          {selected.alerts.map((a) => <div key={a.kind} className="row"><span className={`pill ${a.severity === "danger" ? "bad" : a.severity === "warning" ? "warn" : "info"}`}>{a.kind.replace("_", " ")}</span>{a.message}</div>)}
        </div>
      ) : null}
      <div className="grid2">
        <AddRepeater known={known} onDone={reload} />
        <Bulk reps={reps} />
      </div>
    </>
  );
}

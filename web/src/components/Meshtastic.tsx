import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, clock, useApi, useLive } from "../api";
import { useMe } from "../auth";

export type Mesh = "meshcore" | "meshtastic";
const BASE = "/api/addons/meshtastic";

/** Whether the Meshtastic add-on is installed and loaded on this station. */
export function useMeshtasticAddon(): boolean {
  const { data } = useApi<any[]>("/api/addons");
  return !!data?.some((a) => a.id === "meshtastic");
}

/** MeshCore / Meshtastic tabs for a page, kept in the address (?mesh=meshtastic) so they can be linked. */
export function useMeshTab(): [Mesh, (m: Mesh) => void, boolean] {
  const has = useMeshtasticAddon();
  const [params, setParams] = useSearchParams();
  const mesh: Mesh = has && params.get("mesh") === "meshtastic" ? "meshtastic" : "meshcore";
  const set = (m: Mesh) => {
    const p = new URLSearchParams(params);
    if (m === "meshtastic") p.set("mesh", "meshtastic");
    else p.delete("mesh");
    setParams(p, { replace: true });
  };
  return [mesh, set, has];
}

export function MeshTabs({ mesh, onChange, counts }: { mesh: Mesh; onChange: (m: Mesh) => void; counts?: Partial<Record<Mesh, number>> }) {
  const tab = (m: Mesh, label: string) => (
    <button type="button" role="tab" aria-selected={mesh === m} className={mesh === m ? "on" : ""} onClick={() => onChange(m)}>
      {label}{counts?.[m] != null ? ` (${counts[m]})` : ""}
    </button>
  );
  return (
    <div className="seg" role="tablist" aria-label="Mesh">
      {tab("meshcore", "MeshCore")}
      {tab("meshtastic", "Meshtastic")}
    </div>
  );
}

const ago = (t?: number | null) => {
  if (!t) return "—";
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
};

function RadioState() {
  const { data } = useApi<any>(`${BASE}/status`, 10000);
  const r = data?.radio;
  if (!r || r.state === "connected") return null;
  return (
    <div className="note" style={{ marginBottom: 10 }}>
      The station's Meshtastic radio is {r.state === "off" ? "not set up" : r.state}
      {r.error ? ` (${r.error})` : ""}. Set it up on the <a href="/addons/meshtastic/">Meshtastic page</a>.
    </div>
  );
}

/** Every Meshtastic node the station's Meshtastic radio knows about. */
export function MeshtasticNodes({ query }: { query: string }) {
  const { data, error } = useApi<any[]>(`${BASE}/nodes`, 30000);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data || []).filter((n) => !q || [n.long_name, n.short_name, n.id, n.hw_model].some((v) => (v || "").toLowerCase().includes(q)));
  }, [data, query]);
  return (
    <>
      <RadioState />
      {error && <div className="err">{error}</div>}
      <div className="card tablewrap" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr><th>Name</th><th>ID</th><th>Hardware</th><th>Role</th><th>Heard</th><th>SNR</th><th>Hops</th><th>Key</th><th>Position</th><th></th></tr>
          </thead>
          <tbody>
            {rows.length === 0 && <tr><td colSpan={10} className="empty">No Meshtastic nodes heard yet.</td></tr>}
            {rows.map((n) => (
              <tr key={n.num}>
                <td><div>{n.long_name || n.id}</div>{n.short_name && <div className="small faint">{n.short_name}{n.favorite ? " · ★" : ""}</div>}</td>
                <td className="mono">{n.id}</td>
                <td className="small">{n.hw_model || "—"}</td>
                <td className="small">{n.role}</td>
                <td>{ago(n.last_heard)}</td>
                <td>{n.snr ?? "—"}</td>
                <td>{n.hops ?? "—"}</td>
                <td>{n.has_key ? <span className="pill ok">PKI</span> : <span className="pill plain">none</span>}</td>
                <td className="small">{n.lat != null ? "known" : <span className="faint">none</span>}</td>
                <td><a href="/addons/meshtastic/"><button type="button" className="small-btn">Manage</button></a></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

type MtMsg = { id: number; received_at: number; channel: number; from_name: string | null; from_id: string; text: string; hops: number | null; snr: number | null; sent: number };

/** Meshtastic channels and their messages, with a box to send. */
export function MeshtasticChannels() {
  const { data: chans } = useApi<any[]>(`${BASE}/channels`, 30000);
  const [sel, setSel] = useState<number | null>(null);
  const channel = sel ?? chans?.[0]?.index ?? null;
  const [msgs, setMsgs] = useState<MtMsg[]>([]);
  const end = useRef<HTMLDivElement>(null);

  const load = () => {
    if (channel == null) return;
    api<MtMsg[]>(`${BASE}/messages?channel=${channel}&limit=300`).then((m) => setMsgs(m.reverse())).catch(() => {});
  };
  useEffect(load, [channel]);
  useLive((ev) => {
    if (ev.type === "mt_message" && (ev as any).channel === channel) load();
  });
  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [msgs.length]);

  const name = chans?.find((c) => c.index === channel)?.name;
  return (
    <>
      <RadioState />
      <div className="grid2" style={{ gridTemplateColumns: "minmax(200px, 260px) minmax(0, 1fr)" }}>
        <div className="card" style={{ padding: 6, alignSelf: "start" }}>
          {(chans || []).length === 0 && <div className="small muted" style={{ padding: 8 }}>No channels yet. They appear once the Meshtastic radio is connected.</div>}
          {(chans || []).map((c) => (
            <button
              key={c.index}
              onClick={() => setSel(c.index)}
              aria-pressed={c.index === channel}
              style={{ display: "block", width: "100%", textAlign: "left", border: 0, marginBottom: 2,
                background: c.index === channel ? "var(--accent-bg)" : "transparent", color: c.index === channel ? "var(--accent)" : undefined }}
            >
              <div>{c.name}</div>
              <div className="small faint">Channel {c.index}{c.primary ? " · primary" : ""}{c.messages ? ` · ${c.messages} messages` : ""}</div>
            </button>
          ))}
        </div>
        <div className="card">
          <h2>{name || "No channel"} <span className="small faint">Meshtastic</span></h2>
          <div className="chat" aria-live="polite">
            {msgs.length === 0 && <div className="empty">No messages on this channel yet. Messages show up here from the moment the station hears them.</div>}
            {msgs.map((m) => (
              <div className="msg" key={m.id}>
                <div className="row">
                  <span className="who">{m.sent ? "This station" : m.from_name || m.from_id}</span>
                  <span className="faint small">{clock(m.received_at)}{m.hops != null ? ` · ${m.hops} hops` : ""}{m.snr != null ? ` · SNR ${m.snr}` : ""}</span>
                </div>
                <div>{m.text}</div>
              </div>
            ))}
            <div ref={end} />
          </div>
          {channel != null && <MtCompose channel={channel} name={name} onSent={load} />}
        </div>
      </div>
    </>
  );
}

const MAX_BYTES = 200;

function MtCompose({ channel, name, onSent }: { channel: number; name?: string; onSent: () => void }) {
  const me = useMe();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  if (!me?.can_change) return null;
  const bytes = new TextEncoder().encode(text.trim()).length;
  return (
    <form
      className="compose"
      onSubmit={(e) => {
        e.preventDefault();
        if (!text.trim() || bytes > MAX_BYTES || busy) return;
        setBusy(true);
        setNote(null);
        api(`${BASE}/send`, { method: "POST", body: { channel, text: text.trim() } })
          .then(() => (setText(""), setNote({ ok: true, text: "Sent." }), onSent()))
          .catch((err) => setNote({ ok: false, text: err.message }))
          .finally(() => setBusy(false));
      }}
    >
      <div className="row" style={{ alignItems: "flex-end" }}>
        <label className="field" style={{ flex: 1, minWidth: 200 }}>
          <span className="flabel">Send on {name || `channel ${channel}`}</span>
          <input value={text} maxLength={400} placeholder="Message" onChange={(e) => setText(e.target.value)} />
        </label>
        <button type="submit" className="primary" disabled={busy || !text.trim() || bytes > MAX_BYTES}>{busy ? "Sending…" : "Send"}</button>
      </div>
      <div className="row small" style={{ marginTop: 6 }}>
        <span className="faint">Goes out through the station's Meshtastic radio. Everyone with this channel's key can read it.</span>
        <span className={bytes > MAX_BYTES ? "err" : "faint"} style={{ marginLeft: "auto" }}>{bytes}/{MAX_BYTES}</span>
      </div>
      {note && <div className={`small ${note.ok ? "" : "err"}`} style={{ marginTop: 4 }}>{note.text}</div>}
    </form>
  );
}

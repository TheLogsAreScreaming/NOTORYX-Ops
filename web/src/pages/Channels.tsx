import { useEffect, useState } from "react";
import { ago, api, clock, useApi, useLive } from "../api";

type Msg = { id?: number; channel: string; sender: string | null; text: string; received_at: number; hops: number };

export default function Channels() {
  const { data } = useApi<any>("/api/channels", 30000);
  const [sel, setSel] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const channel = sel ?? data?.channels?.[0]?.name ?? null;

  useEffect(() => {
    if (!channel) return;
    api<Msg[]>(`/api/messages?channel=${encodeURIComponent(channel)}&limit=300`).then((m) => setMsgs(m.reverse()));
  }, [channel]);

  useLive((ev) => {
    if (ev.type === "message" && ev.channel === channel) setMsgs((m) => [...m, ev as unknown as Msg].slice(-500));
  });

  return (
    <>
      <div className="top">
        <h1>Channels</h1>
        <span className="muted small">Group messages for channels whose keys are in the station settings.</span>
      </div>
      <div className="grid2" style={{ gridTemplateColumns: "minmax(200px, 260px) minmax(0, 1fr)" }}>
        <div>
          <div className="card" style={{ padding: 6 }}>
            {(data?.channels || []).map((c: any) => (
              <button
                key={c.name}
                onClick={() => setSel(c.name)}
                aria-pressed={c.name === channel}
                style={{
                  display: "block",
                  width: "100%",
                  textAlign: "left",
                  border: 0,
                  marginBottom: 2,
                  background: c.name === channel ? "var(--accent-bg)" : "transparent",
                  color: c.name === channel ? "var(--accent)" : undefined,
                }}
              >
                <div>{c.name}</div>
                <div className="small faint">{c.messages} messages · {ago(c.last)}</div>
              </button>
            ))}
          </div>
          {data?.unknown?.length > 0 && (
            <div className="card">
              <h2>Other channels heard</h2>
              <div className="small muted" style={{ marginBottom: 6 }}>No key for these. Add one in the station settings to read them.</div>
              {data.unknown.map((u: any) => (
                <div key={u.hash} className="row small">
                  <span className="mono">hash {u.hash}</span>
                  <span className="faint">{u.packets_24h} packets today</span>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="card">
          <h2>{channel || "No channels"}</h2>
          <div className="chat" aria-live="polite">
            {msgs.length === 0 && <div className="empty">No messages on this channel yet.</div>}
            {msgs.map((m, i) => (
              <div className="msg" key={m.id ?? `${m.received_at}-${i}`}>
                <div className="row">
                  <span className="who">{m.sender || "Unknown"}</span>
                  <span className="faint small">{clock(m.received_at)} · {m.hops} hops</span>
                </div>
                <div>{m.text}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

import { useEffect, useState } from "react";
import { ago, api, clock, useApi, useLive } from "../api";
import { decodeMessage, encode, isSarneg, loadKeys, parseKeys, SARNEG_MARKER, saveKeys, wordFor, type SarnegKeys } from "../sarneg";
import { useMe } from "../auth";

type Msg = { id?: number; channel: string; sender: string | null; text: string; received_at: number; hops: number };

export default function Channels() {
  const { data } = useApi<any>("/api/channels", 30000);
  const [sel, setSel] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const channel = sel ?? data?.channels?.[0]?.name ?? null;
  const [keys, setKeys] = useState<SarnegKeys | null>(() => loadKeys());

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
          <SarnegCard keys={keys} onChange={(k) => (saveKeys(k), setKeys(k))} />
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
                {isSarneg(m.text) && <SarnegLine text={m.text} at={m.received_at} keys={keys} />}
              </div>
            ))}
          </div>
          {channel && <Compose channel={channel} keys={keys} />}
        </div>
      </div>
    </>
  );
}

const MAX_BYTES = 140;

/** Send a message on the selected channel through the station radio, optionally as SARNEG numbers. */
function Compose({ channel, keys }: { channel: string; keys: SarnegKeys | null }) {
  const me = useMe();
  const [text, setText] = useState("");
  const [asSarneg, setAsSarneg] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  if (!me?.can_change) return null;

  const word = keys ? wordFor(keys, Date.now()) : null;
  const out = asSarneg && word ? `${SARNEG_MARKER} ${encode(text.trim(), word)}` : text.trim();
  const bytes = new TextEncoder().encode(out).length;
  const tooLong = bytes > MAX_BYTES;

  function send(e: React.FormEvent) {
    e.preventDefault();
    if (!out || tooLong || busy) return;
    setBusy(true);
    setNote(null);
    api("/api/channels/send", { method: "POST", body: { channel, text: out } })
      .then(() => {
        setText("");
        setNote({ ok: true, text: "Sent. It shows up above when a repeater passes it back to the station." });
      })
      .catch((err) => setNote({ ok: false, text: err.message }))
      .finally(() => setBusy(false));
  }

  return (
    <form className="compose" onSubmit={send}>
      <div className="row" style={{ alignItems: "flex-end" }}>
        <label className="field" style={{ flex: 1, minWidth: 200 }}>
          <span className="flabel">Send on {channel}</span>
          <input value={text} maxLength={300} placeholder={asSarneg ? "Numbers to code, e.g. 17S 4823" : "Message"} onChange={(e) => setText(e.target.value)} />
        </label>
        <button type="submit" className="primary" disabled={busy || !out || tooLong}>{busy ? "Sending…" : "Send"}</button>
      </div>
      <div className="row small" style={{ marginTop: 6 }}>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={asSarneg} disabled={!word} onChange={(e) => setAsSarneg(e.target.checked)} />
          Send numbers as SARNEG
        </label>
        {!word && <span className="faint">Set a code word in the SARNEG card to use this.</span>}
        <span className={tooLong ? "err" : "faint"} style={{ marginLeft: "auto" }}>{bytes}/{MAX_BYTES}</span>
      </div>
      {asSarneg && word && text.trim() && <div className="small faint mono" style={{ marginTop: 4 }}>Goes out as: {out}</div>}
      {note && <div className={`small ${note.ok ? "" : "err"}`} style={{ marginTop: 4 }}>{note.text}</div>}
      <div className="small faint" style={{ marginTop: 4 }}>Everyone with this channel's key can read it. SARNEG hides numbers from casual readers but is not encryption.</div>
    </form>
  );
}

/** Under a "[SARNEG]" message: click to show its numbers. */
function SarnegLine({ text, at, keys }: { text: string; at: number; keys: SarnegKeys | null }) {
  const [open, setOpen] = useState(false);
  const decoded = keys ? decodeMessage(text, wordFor(keys, at)) : null;
  const label = !open ? "SARNEG · click to decode" : decoded == null ? "SARNEG · set a code word to decode" : `Decoded: ${decoded}`;
  return (
    <button
      onClick={() => setOpen((o) => !o)}
      aria-label={open ? label : "Decode SARNEG numbers"}
      className={open && decoded != null ? "mono small" : "small"}
      style={{ border: 0, background: "transparent", padding: "2px 0", color: "var(--accent)", textAlign: "left" }}
    >
      {label}
    </button>
  );
}

/** The SARNEG code word for decoding, kept in this browser only. */
function SarnegCard({ keys, onChange }: { keys: SarnegKeys | null; onChange: (k: SarnegKeys | null) => void }) {
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [shown, setShown] = useState(false);
  const today = keys ? wordFor(keys, new Date()) : null;
  return (
    <div className="card">
      <h2>SARNEG code word</h2>
      <div className="small muted" style={{ marginBottom: 6 }}>
        Decodes numbers sent as SARNEG letters by NOTORYX apps. Kept in this browser only, never sent to the
        station. SARNEG is not encryption.
      </div>
      {keys ? (
        <>
          <div className="row small">
            <span>Today: <span className="mono">{shown ? today : "••••••••••"}</span></span>
            <button onClick={() => setShown((v) => !v)}>{shown ? "Hide" : "Show"}</button>
            <button onClick={() => (onChange(null), setShown(false))}>Forget</button>
          </div>
          {Object.keys(keys.daily).length > 0 && (
            <div className="small faint">{Object.keys(keys.daily).length} dated words</div>
          )}
        </>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            try {
              onChange(parseKeys(text));
              setText("");
              setErr(null);
            } catch (x) {
              setErr((x as Error).message);
            }
          }}
        >
          <label className="small" htmlFor="sarneg-word">Word or shared text (SARNEG1:…)</label>
          <input
            id="sarneg-word"
            className="mono"
            autoComplete="off"
            spellCheck={false}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="BLACKHORSE"
            style={{ width: "100%" }}
          />
          {err && <div className="small" role="alert" style={{ color: "var(--bad, #e5484d)" }}>{err}</div>}
          <button type="submit" style={{ marginTop: 6 }}>Use this word</button>
        </form>
      )}
    </div>
  );
}

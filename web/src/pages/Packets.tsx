import { useEffect, useState } from "react";
import { Packet, TYPE_LABEL, TYPE_PILL, api, clock, useApi, useLive } from "../api";
import { IgnoreButton } from "../components/NodeTags";

const TYPES = ["", "advert", "group_text", "text", "request", "response", "anon_request", "ack", "path", "trace"];

function HexDump({ hex }: { hex: string }) {
  const bytes = hex.match(/../g) || [];
  const lines: string[] = [];
  for (let i = 0; i < bytes.length; i += 16) {
    const chunk = bytes.slice(i, i + 16);
    const ascii = chunk.map((b) => {
      const c = parseInt(b, 16);
      return c >= 32 && c < 127 ? String.fromCharCode(c) : ".";
    });
    lines.push(`${i.toString(16).padStart(4, "0")}  ${chunk.join(" ").padEnd(47)}  ${ascii.join("")}`);
  }
  return <div className="hex">{lines.join("\n")}</div>;
}

function Detail({ hash, onClose }: { hash: string; onClose: () => void }) {
  const { data, error } = useApi<any>(`/api/packets/${hash}`);
  const [ignoredMsg, setIgnoredMsg] = useState<string | null>(null);
  return (
    <aside className="drawer" aria-label="Packet detail">
      <div className="row" style={{ marginBottom: 10 }}>
        <strong>Packet {hash}</strong>
        <span style={{ flex: 1 }} />
        <button onClick={onClose}>Close</button>
      </div>
      {error && <div className="err">{error}</div>}
      {data && (
        <div className="stack">
          {data.message && (
            <div className="note">
              <strong>{data.message.channel}</strong> · {data.message.sender}: {data.message.text}
            </div>
          )}
          <table>
            <tbody>
              {[
                ["Type", TYPE_LABEL[data.decoded.payload_type] || data.decoded.payload_type],
                ["Route", data.decoded.route],
                ["Hops", data.decoded.hops],
                ["Hash size", `${data.decoded.hash_size} byte`],
                ["Path", data.decoded.path.join(" > ") || "—"],
                ["Channel", data.decoded.channel_hash],
                ["From / to", data.decoded.src_hash ? `${data.decoded.src_hash} > ${data.decoded.dest_hash}` : null],
                ["Transport codes", data.decoded.transport_codes?.join(", ")],
                ["Advert", data.decoded.advert ? `${data.decoded.advert.name ?? "?"} (${data.decoded.advert.node_type})` : null],
                ["Position", data.decoded.advert?.lat != null ? `${data.decoded.advert.lat}, ${data.decoded.advert.lon}` : null],
                ["Public key", data.decoded.advert?.public_key],
              ]
                .filter(([, v]) => v != null && v !== "")
                .map(([k, v]) => (
                  <tr key={k as string}>
                    <th>{k}</th>
                    <td className="mono" style={{ wordBreak: "break-all" }}>{String(v)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
          {data.decoded.advert?.public_key && (
            <div className="row small">
              <IgnoreButton small publicKey={data.decoded.advert.public_key} name={data.decoded.advert.name} onChange={(on) => setIgnoredMsg(on ? "Ignored. Its packets are hidden from now on." : "Shown again.")} />
              {ignoredMsg && <span className="muted">{ignoredMsg}</span>}
            </div>
          )}
          <h3 style={{ margin: "8px 0 0", fontSize: 14 }}>Heard {data.receptions.length} times</h3>
          <table>
            <thead>
              <tr><th>Time</th><th>Observer</th><th>Path</th><th>SNR</th><th>RSSI</th></tr>
            </thead>
            <tbody>
              {data.receptions.map((r: Packet) => (
                <tr key={r.id}>
                  <td className="mono">{clock(r.received_at)}</td>
                  <td>{r.observer}</td>
                  <td className="mono">{r.path.join(" ") || "direct"}</td>
                  <td>{r.snr ?? "—"}</td>
                  <td>{r.rssi ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3 style={{ margin: "8px 0 0", fontSize: 14 }}>Raw bytes</h3>
          <HexDump hex={data.decoded.raw} />
        </div>
      )}
    </aside>
  );
}

function Decoder() {
  const [raw, setRaw] = useState("");
  const [out, setOut] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="card">
      <h2>Decode a packet</h2>
      <form
        className="row"
        onSubmit={(e) => {
          e.preventDefault();
          if (!raw.trim()) return setErr("Paste a packet as hex first.");
          api("/api/decode", { method: "POST", body: { raw: raw.trim() } })
            .then((d) => (setOut(d), setErr(null)))
            .catch((e2) => (setOut(null), setErr(e2.message)));
        }}
      >
        <input
          className="mono"
          style={{ flex: 1 }}
          placeholder="1101a2..."
          value={raw}
          aria-label="Packet hex"
          onChange={(e) => (setRaw(e.target.value), setErr(null))}
        />
        <button type="submit">Decode</button>
      </form>
      {err && <div className="err" style={{ marginTop: 6 }}>{err}</div>}
      {out && <pre className="hex" style={{ marginTop: 8 }}>{JSON.stringify(out, null, 2)}</pre>}
    </div>
  );
}

export default function Packets() {
  const [type, setType] = useState("");
  const [observer, setObserver] = useState("");
  const [rows, setRows] = useState<Packet[]>([]);
  const [paused, setPaused] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { data: observers } = useApi<any[]>("/api/observers");

  const qs = new URLSearchParams({ limit: "200", ...(type && { type }), ...(observer && { observer }) }).toString();
  useEffect(() => {
    api<Packet[]>(`/api/packets?${qs}`).then((r) => (setRows(r), setErr(null))).catch((e) => setErr(e.message));
  }, [qs]);

  useLive((ev) => {
    if (ev.type !== "packet" || paused) return;
    const p = ev as unknown as Packet;
    if ((type && p.ptype !== type) || (observer && p.observer !== observer)) return;
    setRows((r) => [p, ...r].slice(0, 500));
  });

  function older() {
    const last = rows[rows.length - 1];
    if (!last) return;
    api<Packet[]>(`/api/packets?${qs}&before=${last.received_at}`).then((r) => setRows((x) => [...x, ...r]));
  }

  return (
    <>
      <div className="top">
        <h1>Packets</h1>
        <span style={{ flex: 1 }} />
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Packet type">
          {TYPES.map((t) => (
            <option key={t} value={t}>{t ? TYPE_LABEL[t] : "All types"}</option>
          ))}
        </select>
        <select value={observer} onChange={(e) => setObserver(e.target.value)} aria-label="Observer">
          <option value="">All observers</option>
          {(observers || []).map((o) => (
            <option key={o.name} value={o.name}>{o.name}</option>
          ))}
        </select>
        <button onClick={() => setPaused((p) => !p)} aria-pressed={paused}>{paused ? "Resume" : "Pause"}</button>
      </div>
      {err && <div className="err">{err}</div>}
      <div className="card tablewrap" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Time</th><th>Type</th><th>Summary</th><th>Hops</th><th>Path</th><th>Observer</th><th>SNR</th><th>RSSI</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id} className={`click ${sel === p.hash ? "sel" : ""}`} onClick={() => setSel(p.hash)}>
                <td className="mono">{clock(p.received_at)}</td>
                <td>
                  <span className={`pill ${TYPE_PILL[p.ptype] || "plain"}`}>{TYPE_LABEL[p.ptype] || p.ptype}</span>
                  {p.repeat && <span className="faint small"> again</span>}
                </td>
                <td className="ell" style={{ maxWidth: 280 }}>{p.summary}</td>
                <td>{p.hops}</td>
                <td className="mono small ell" style={{ maxWidth: 160 }}>{p.path.join(" ")}</td>
                <td className="ell" style={{ maxWidth: 140 }}>{p.observer}</td>
                <td>{p.snr ?? "—"}</td>
                <td>{p.rssi ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <div className="empty" style={{ padding: 16 }}>No packets yet.</div>}
      </div>
      {rows.length >= 200 && <button onClick={older}>Load older</button>}
      <div style={{ height: 14 }} />
      <Decoder />
      {sel && <Detail hash={sel} onClose={() => setSel(null)} />}
    </>
  );
}

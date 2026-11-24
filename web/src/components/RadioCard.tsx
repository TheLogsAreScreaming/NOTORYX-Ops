import { ReactNode, useEffect, useRef, useState } from "react";
import { api } from "../api";

type Radio = any;
type Live = { transport: string; state: string; name?: string | null; error?: string | null };
type Found = { address: string; name: string; rssi?: number | null };

const TRANSPORTS: [string, string][] = [["none", "None"], ["serial", "USB"], ["ble", "Bluetooth"], ["tcp", "Wi-Fi / TCP"]];

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="flabel">{label}</span>
      {children}
      {hint && <span className="fhint">{hint}</span>}
    </label>
  );
}

/** Live state of the station radio, refreshed every few seconds. */
function useLive(): [Live | null, () => void] {
  const [live, setLive] = useState<Live | null>(null);
  const tick = useRef<() => void>(() => {});
  tick.current = () => api<any>("/api/station").then((s) => setLive(s.radio)).catch(() => {});
  useEffect(() => {
    tick.current();
    const t = setInterval(() => tick.current(), 4000);
    return () => clearInterval(t);
  }, []);
  return [live, () => tick.current()];
}

function statusText(live: Live | null): [string, string] {
  if (!live) return ["var(--text-3)", "Checking the radio…"];
  if (live.transport === "none") return ["var(--text-3)", "No radio. Pick how to connect one below."];
  if (live.state === "connected") return ["var(--ok)", `Connected to ${live.name || "the radio"}`];
  const why = live.error ? ` (${live.error})` : "";
  return ["var(--warn)", `Looking for the radio…${why}`];
}

/**
 * The MeshCore radio part of Settings: shows what is connected, finds radios nearby
 * and connects to one with a single click.
 */
export default function RadioCard({ r, set, onSaved, onError }: {
  r: Radio;
  set: (path: string, value: unknown) => void;
  onSaved: (radio: Radio) => void;
  onError: (m: string | null) => void;
}) {
  const [live, refresh] = useLive();
  const [found, setFound] = useState<Found[] | null>(null);
  const [ports, setPorts] = useState<any[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const autoScanned = useRef(false);

  const connected = live?.state === "connected";
  const bleConnected = connected && live?.transport === "ble";

  function scanBle() {
    setScanning(true);
    onError(null);
    api<Found[]>("/api/settings/ble-scan")
      .then(setFound)
      .catch((e) => onError(e.message))
      .finally(() => setScanning(false));
  }
  function findPorts() {
    api<any[]>("/api/settings/serial-ports").then(setPorts).catch((e) => onError(e.message));
  }

  // Look for radios as soon as a connection type is picked, so there is something to choose from.
  useEffect(() => {
    if (!live || autoScanned.current) return;
    if (r.transport === "ble" && !bleConnected) {
      autoScanned.current = true;
      scanBle();
    }
    if (r.transport === "serial" && ports === null) findPorts();
  }, [r.transport, live?.state]);

  // Swap a "Connecting…" note for the result once the radio answers.
  useEffect(() => {
    if (!note.startsWith("Connecting") || !live) return;
    if (live.state === "connected") setNote(`Connected to ${live.name || "the radio"}.`);
    else if (live.error) setNote(`Not connected yet: ${live.error}. Still trying.`);
  }, [live?.state, live?.error, live?.name]);

  function connect(patch: Partial<Radio>, message: string) {
    setBusy(true);
    onError(null);
    api<any>("/api/settings", { method: "PUT", body: { radio: { ...r, ...patch } } })
      .then((res) => {
        onSaved(res.settings.radio);
        setNote(message);
        setTimeout(refresh, 1500);
        setTimeout(refresh, 6000);
      })
      .catch((e) => onError(e.message))
      .finally(() => setBusy(false));
  }

  // The connected radio stops advertising, so a scan can't see it: list it ourselves.
  const radios: (Found & { now?: boolean })[] = [];
  if (bleConnected) radios.push({ address: r.ble_address, name: live?.name || "This radio", now: true });
  for (const d of found || []) if (!radios.some((x) => x.address === d.address)) radios.push(d);

  const [dot, text] = statusText(live);
  return (
    <div className="card">
      <h2>Radio</h2>
      <div className="small muted" style={{ marginBottom: 10 }}>
        The MeshCore companion radio this station listens with and uses to reach your repeaters. A Bluetooth radio
        works with one app at a time, so close it on your phone first.
      </div>
      <div className="row" style={{ marginBottom: 10 }} role="status" aria-live="polite">
        <span className="dot" style={{ background: dot }} aria-hidden="true" />
        <span>{text}</span>
        {live && live.transport !== "none" && live.transport !== "demo" && (
          <button type="button" disabled={busy} onClick={() => connect({ transport: "none" }, "Disconnected. The radio is free for other apps.")}>
            Disconnect
          </button>
        )}
      </div>

      <div className="seg" role="radiogroup" aria-label="Connection">
        {TRANSPORTS.map(([v, l]) => (
          <button key={v} type="button" role="radio" aria-checked={r.transport === v} className={r.transport === v ? "on" : ""}
            onClick={() => { set("radio.transport", v); setNote(""); autoScanned.current = false; }}>{l}</button>
        ))}
      </div>

      {r.transport === "ble" && (
        <div className="stack" style={{ marginTop: 10 }}>
          <div className="row">
            <span className="flabel" style={{ flex: 1 }}>Bluetooth radios</span>
            <button type="button" onClick={scanBle} disabled={scanning}>{scanning ? "Looking…" : "Find radios"}</button>
          </div>
          <div className="picklist">
            {radios.map((d) => (
              <div key={d.address || "auto"} className={`pickrow ${d.address === r.ble_address || d.now ? "on" : ""}`}>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <div>{d.name}</div>
                  <div className="small faint mono" style={{ overflowWrap: "anywhere" }}>
                    {d.address || "found automatically"}{d.rssi != null ? ` · ${d.rssi} dBm` : ""}
                  </div>
                </span>
                {d.now
                  ? <span className="pill ok">Connected</span>
                  : <button type="button" className="primary" disabled={busy}
                      onClick={() => connect({ transport: "ble", ble_address: d.address }, `Connecting to ${d.name}…`)}>Connect</button>}
              </div>
            ))}
            {scanning && radios.length === 0 && <div className="small muted">Looking for MeshCore radios nearby…</div>}
            {!scanning && found && radios.length === 0 && (
              <div className="small muted">No MeshCore radios heard. Make sure it's switched on and not connected to your phone or another app, then Find radios again.</div>
            )}
          </div>
          {bleConnected && (
            <div className="small faint">A connected radio stops advertising, so others only show up here. To switch radios, Disconnect first and then Find radios.</div>
          )}
          <details>
            <summary className="small">Type an address instead</summary>
            <Field label="Bluetooth address" hint="Leave empty to use the first MeshCore radio found.">
              <div className="row">
                <input style={{ flex: 1 }} className="mono" value={r.ble_address} onChange={(e) => set("radio.ble_address", e.target.value)} />
                <button type="button" disabled={busy} onClick={() => connect({ transport: "ble" }, "Connecting…")}>Connect</button>
              </div>
            </Field>
          </details>
        </div>
      )}

      {r.transport === "serial" && (
        <div className="stack" style={{ marginTop: 10 }}>
          <div className="row">
            <span className="flabel" style={{ flex: 1 }}>USB radios</span>
            <button type="button" onClick={findPorts}>Find ports</button>
          </div>
          <div className="picklist">
            {ports?.length === 0 && <div className="small muted">No USB radios found. Plug it in with a data cable, then Find ports again.</div>}
            {(ports || []).map((p) => (
              <div key={p.device} className={`pickrow ${p.device === r.serial_port ? "on" : ""}`}>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <div className="mono" style={{ overflowWrap: "anywhere" }}>{p.device}</div>
                  <div className="small faint">{[p.description, p.manufacturer].filter(Boolean).join(" · ")}</div>
                </span>
                {connected && live?.transport === "serial" && p.device === r.serial_port
                  ? <span className="pill ok">Connected</span>
                  : <button type="button" className="primary" disabled={busy}
                      onClick={() => connect({ transport: "serial", serial_port: p.device }, `Connecting on ${p.device}…`)}>Connect</button>}
              </div>
            ))}
          </div>
          <div className="small faint">USB needs MeshCore USB companion firmware. A radio with Bluetooth firmware connects with Bluetooth instead.</div>
          <Field label="Baud rate"><input type="number" value={r.baudrate} onChange={(e) => set("radio.baudrate", Number(e.target.value))} /></Field>
        </div>
      )}

      {r.transport === "tcp" && (
        <div className="row" style={{ marginTop: 10, alignItems: "flex-end" }}>
          <Field label="Address"><input value={r.tcp_host} placeholder="192.168.1.50" onChange={(e) => set("radio.tcp_host", e.target.value)} /></Field>
          <Field label="Port"><input type="number" style={{ width: 100 }} value={r.tcp_port} onChange={(e) => set("radio.tcp_port", Number(e.target.value))} /></Field>
          <button type="button" className="primary" disabled={busy || !r.tcp_host} onClick={() => connect({ transport: "tcp" }, "Connecting…")}>Connect</button>
        </div>
      )}

      {note && <div className="small" style={{ marginTop: 8 }}>{note}</div>}

      <div style={{ marginTop: 10 }}>
        <Field label="Observer name" hint="How this radio shows up on the Observers page.">
          <input value={r.observer_name} onChange={(e) => set("radio.observer_name", e.target.value)} />
        </Field>
      </div>
    </div>
  );
}

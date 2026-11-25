import { useEffect, useState } from "react";
import { api } from "../api";

type Addon = { id: string; name: string; about: string; license: string; available: string; installed: string | null; newer: boolean };
type Summary = {
  current: string;
  latest: { tag: string; version: string; name: string; url: string; notes: string; published: string } | null;
  newer: boolean;
  mac_asset: string | null;
  addons: Addon[];
  releases_url: string;
};

const inMacApp = () => !!(window as any).webkit?.messageHandlers?.notoryxUpdate;

/** First lines of the release notes, without the download table. */
function shortNotes(md: string): string[] {
  const lines = md.split("\n");
  const start = lines.findIndex((l) => l.startsWith("## New in"));
  const out: string[] = [];
  for (const l of lines.slice(start < 0 ? 0 : start + 1)) {
    if (l.startsWith("## ")) break;
    if (l.trim().startsWith("- ")) out.push(l.trim().slice(2).replace(/\*\*/g, ""));
  }
  return out.slice(0, 5);
}

/** Restarts the station and reloads the page once it answers again. */
export async function restartStation(onNote: (m: string) => void) {
  onNote("Restarting the station…");
  await api("/api/station/restart", { method: "POST" }).catch(() => {});
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 1500));
    try {
      await api("/api/station");
      location.reload();
      return;
    } catch {
      // still restarting
    }
  }
  onNote("The station didn't come back. If you started it by hand, start it again.");
}

export default function UpdatesCard() {
  const [s, setS] = useState<Summary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [needRestart, setNeedRestart] = useState(false);

  const load = (force = false) => {
    setErr(null);
    return api<Summary>(`/api/updates${force ? "?force=true" : ""}`).then(setS).catch((e) => setErr(e.message));
  };
  useEffect(() => {
    load();
  }, []);

  const [dl, setDl] = useState<{ state: string; bytes?: number; total?: number | null; version?: string; path?: string; error?: string } | null>(null);

  async function updateApp() {
    setBusy("app");
    setErr(null);
    setNote("");
    try {
      let p = await api<any>("/api/updates/download", { method: "POST" });
      setDl(p);
      while (p.state === "downloading" || p.state === "checking") {
        await new Promise((r) => setTimeout(r, 400));
        p = await api<any>("/api/updates/progress");
        setDl(p);
      }
      if (p.state !== "ready") throw new Error(p.error || "The download didn't finish.");
      setNote(`Installing ${p.version}. NOTORYX Ops will close and open again in a moment.`);
      (window as any).webkit.messageHandlers.notoryxUpdate.postMessage(p.path);
    } catch (e: any) {
      setErr(e.message);
      setDl(null);
      setBusy(null);
    }
  }

  const mb = (n?: number | null) => (n ? (n / 1048576).toFixed(1) : "0");
  const pct = dl?.total ? Math.min(100, Math.round(((dl.bytes || 0) / dl.total) * 100)) : null;

  async function addon(a: Addon, action: "install" | "remove") {
    setBusy(a.id);
    setErr(null);
    setNote(action === "install" ? `Installing ${a.name} ${a.available}…` : `Removing ${a.name}…`);
    try {
      await api(`/api/addons/${a.id}/${action}`, { method: "POST" });
      setNote(action === "install" ? `${a.name} ${a.available} is installed. Restart the station to start it.` : `${a.name} is removed. Restart the station to finish.`);
      setNeedRestart(true);
      await load(true);
    } catch (e: any) {
      setErr(e.message);
      setNote("");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="card" id="set-updates">
      <h2>Updates and add-ons</h2>
      <div className="small muted" style={{ marginBottom: 10 }}>
        From the NOTORYX Ops releases page on GitHub. Every download is checked against the release's checksums
        before it is used, and nothing is installed until you press a button.
      </div>
      {err && <div className="err" style={{ marginBottom: 8 }}>{err}</div>}
      {!s && !err && <div className="small muted">Checking for updates…</div>}
      {s && (
        <div className="stack">
          <div className="row">
            <span style={{ flex: 1 }}>
              This station runs <b>{s.current}</b>.{" "}
              {s.latest && !s.newer && <span className="pill ok">Up to date</span>}
              {s.newer && s.latest && <span className="pill warn">{s.latest.version} is out</span>}
            </span>
            <button type="button" disabled={!!busy} onClick={() => load(true)}>Check again</button>
          </div>
          {s.newer && s.latest && (
            <div className="subcard">
              <b>{s.latest.name}</b>
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                {shortNotes(s.latest.notes).map((l, i) => <li key={i}>{l}</li>)}
              </ul>
              <div className="row">
                {inMacApp() && s.mac_asset
                  ? <button type="button" className="primary" disabled={!!busy} onClick={updateApp}>{busy === "app" ? "Updating…" : `Update to ${s.latest.version}`}</button>
                  : <a href={s.latest.url} target="_blank" rel="noreferrer"><button type="button" className="primary">Open the release page</button></a>}
                <a className="small" href={s.latest.url} target="_blank" rel="noreferrer">Full notes</a>
              </div>
              {dl && dl.state !== "idle" && (
                <div className="stack" style={{ gap: 4 }}>
                  <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined} aria-label="Update download">
                    <i style={{ width: `${dl.state === "ready" ? 100 : pct ?? 5}%` }} className={pct == null && dl.state === "downloading" ? "busy" : ""} />
                  </div>
                  <div className="small faint">
                    {dl.state === "downloading" && `Downloading ${dl.version}: ${mb(dl.bytes)}${dl.total ? ` of ${mb(dl.total)} MB (${pct}%)` : " MB"}`}
                    {dl.state === "checking" && "Checking the download against its checksum…"}
                    {dl.state === "ready" && "Checked. Installing…"}
                  </div>
                </div>
              )}
              {inMacApp() && <div className="small faint">Add-ons stay installed. The app closes and opens again on its own.</div>}
            </div>
          )}
          <div className="flabel" style={{ marginTop: 6 }}>Add-ons</div>
          {s.addons.length === 0 && <div className="small faint">The newest release has no add-ons.</div>}
          {s.addons.map((a) => (
            <div key={a.id} className="pickrow">
              <span style={{ flex: 1, minWidth: 0 }}>
                <div>
                  {a.name} <span className="small faint">{a.license}</span>{" "}
                  {a.installed ? <span className="pill ok">Installed {a.installed}</span> : <span className="pill">Not installed</span>}
                </div>
                <div className="small faint">{a.about}</div>
              </span>
              {!a.installed && <button type="button" className="primary" disabled={!!busy} onClick={() => addon(a, "install")}>{busy === a.id ? "Installing…" : `Install ${a.available}`}</button>}
              {a.installed && a.newer && <button type="button" className="primary" disabled={!!busy} onClick={() => addon(a, "install")}>{busy === a.id ? "Updating…" : `Update to ${a.available}`}</button>}
              {a.installed && <button type="button" disabled={!!busy} onClick={() => addon(a, "remove")}>Remove</button>}
            </div>
          ))}
          {note && <div className="small">{note}</div>}
          {needRestart && <div><button type="button" className="primary" onClick={() => restartStation(setNote)}>Restart the station</button></div>}
        </div>
      )}
    </div>
  );
}

import { useEffect, useMemo, useRef, useState } from "react";
import { GeoJSONSource, Map as MlMap, MapMouseEvent } from "maplibre-gl";
import MapView, { fitTo, onGlobe } from "../components/MapView";
import { ago, api, copyText, Node, useApi, useLive } from "../api";
import { useMe } from "../auth";
import { currentTheme, onTheme } from "../themes";

type Pt = { lat: number; lon: number };
const KIND_ICON: Record<string, string> = {
  flood: "🌊", tree: "🌳", power: "⚡", blocked: "⛔", fire: "🔥", medical: "🩹", hazard: "⚠️", ok: "✅",
};

export default function Field() {
  const me = useMe();
  const canAct = !!me?.can_change;
  const { data: kinds } = useApi<Record<string, string>>("/api/field/kinds");
  const { data: reports, reload: reloadReports } = useApi<any[]>("/api/field/reports?hours=24", 60000);
  const { data: teams, reload: reloadTeams } = useApi<any[]>("/api/field/teams");
  const { data: chans } = useApi<any>("/api/channels");
  const { data: nodes } = useApi<Node[]>("/api/nodes");
  const [teamView, setTeamView] = useState<number | "all" | "none">("none");
  const { data: tracks, reload: reloadTracks } = useApi<any[]>(
    teamView === "none" ? null : `/api/field/tracks?hours=12${teamView === "all" ? "" : `&team_id=${teamView}`}`, 60000);
  useLive((e) => {
    if (e.type === "report") reloadReports();
    if (e.type === "position") reloadTracks();
  });

  const channelNames: string[] = useMemo(() => (chans?.channels || []).map((c: any) => c.name), [chans]);
  const [kind, setKind] = useState("flood");
  const [pos, setPos] = useState<Pt | null>(null);
  const [note, setNote] = useState("");
  const [send, setSend] = useState(true);
  const [channel, setChannel] = useState("Public");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const [teamName, setTeamName] = useState("");
  const [toRadio, setToRadio] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const map = useRef<MlMap | null>(null);
  const fitted = useRef(false);
  const [ready, setReady] = useState(false);
  const [look, setLook] = useState(0);
  useEffect(() => onTheme(() => setLook((x) => x + 1)), []);

  async function act<T>(f: () => Promise<T>, ok: (r: T) => string) {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const r = await f();
      setMsg(ok(r));
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  function here() {
    if (!navigator.geolocation) return setErr("This browser can't share its location.");
    navigator.geolocation.getCurrentPosition(
      (p) => setPos({ lat: +p.coords.latitude.toFixed(5), lon: +p.coords.longitude.toFixed(5) }),
      () => setErr("Location isn't available here. Click the map instead (browsers only share location over https or on this computer)."),
      { enableHighAccuracy: true, timeout: 15000 },
    );
  }

  function submitReport() {
    if (!pos) return;
    act(() => api<any>("/api/field/reports", { method: "POST", body: { kind, lat: pos.lat, lon: pos.lon, note, send, channel } }), (r) => {
      setNote("");
      setPos(null);
      reloadReports();
      return r.text ? `Sent on ${channel}: ${r.text}` : "Saved on the station (not sent).";
    });
  }

  function clearReport(id: number) {
    act(() => api(`/api/field/reports/${id}/clear`, { method: "POST", body: { send, channel } }), () => {
      reloadReports();
      return send ? `All-clear sent on ${channel}.` : "Cleared on the station.";
    });
  }

  function addTeam() {
    act(() => api<any>("/api/field/teams", { method: "POST", body: { name: teamName, add_to_radio: toRadio } }), (t) => {
      setTeamName("");
      reloadTeams();
      return t.radio_slot != null
        ? `Team created. Its channel ${t.channel} is in slot ${t.radio_slot} on the station radio.`
        : `Team created with channel ${t.channel}. Share its key with the team.`;
    });
  }

  function saveMembers(id: number) {
    act(() => api(`/api/field/teams/${id}/members`, { method: "PUT", body: { members: [...picked].map((k) => ({ public_key: k })) } }), () => {
      setEditing(null);
      reloadTeams();
      return "Members saved.";
    });
  }

  // ---- map ------------------------------------------------------------------
  function draw(m: MlMap) {
    const t = currentTheme();
    const set = (id: string, d: any) => {
      const s = m.getSource(id) as GeoJSONSource | undefined;
      if (s) s.setData(d);
      else m.addSource(id, { type: "geojson", data: d });
    };
    const open = (reports || []).filter((r) => !r.cleared_at);
    set("tracks", {
      type: "FeatureCollection",
      features: (tracks || []).filter((tr) => tr.points.length > 1).map((tr) => ({
        type: "Feature", geometry: { type: "LineString", coordinates: tr.points.map((p: number[]) => [p[0], p[1]]) }, properties: { name: tr.name },
      })),
    });
    set("track-ends", {
      type: "FeatureCollection",
      features: (tracks || []).map((tr) => {
        const p = tr.points[tr.points.length - 1];
        return { type: "Feature", geometry: { type: "Point", coordinates: [p[0], p[1]] }, properties: { name: tr.name } };
      }),
    });
    set("reports", {
      type: "FeatureCollection",
      features: open.map((r) => ({ type: "Feature", geometry: { type: "Point", coordinates: [r.lon, r.lat] }, properties: { id: r.id } })),
    });
    set("pick", { type: "FeatureCollection", features: pos ? [{ type: "Feature", geometry: { type: "Point", coordinates: [pos.lon, pos.lat] }, properties: {} }] : [] });
    if (!m.getLayer("reports")) {
      m.addLayer({ id: "tracks", type: "line", source: "tracks", paint: { "line-width": 2.5, "line-opacity": 0.8 } });
      m.addLayer({ id: "track-ends", type: "circle", source: "track-ends", paint: { "circle-radius": 6, "circle-stroke-width": 2 } });
      m.addLayer({ id: "reports", type: "circle", source: "reports", paint: { "circle-radius": 9, "circle-stroke-width": 3 } });
      m.addLayer({ id: "pick", type: "circle", source: "pick", paint: { "circle-radius": 8, "circle-stroke-width": 3, "circle-color": "rgba(0,0,0,0)" } });
    }
    m.setPaintProperty("tracks", "line-color", t.ui.blue);
    m.setPaintProperty("track-ends", "circle-color", t.ui.blue);
    m.setPaintProperty("track-ends", "circle-stroke-color", t.ui.bg);
    m.setPaintProperty("reports", "circle-color", t.ui.warn);
    m.setPaintProperty("reports", "circle-stroke-color", t.ui.bad);
    m.setPaintProperty("pick", "circle-stroke-color", t.ui.accent);
    const pts: [number, number][] = [
      ...open.map((r) => [r.lon, r.lat] as [number, number]),
      ...(nodes || []).filter((n) => n.lat != null).map((n) => [n.lon as number, n.lat as number] as [number, number]),
    ].filter(onGlobe);
    if (!fitted.current && pts.length) {
      fitted.current = true;
      fitTo(m, pts);
    }
  }

  useEffect(() => {
    if (ready && map.current) draw(map.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, reports, tracks, pos, look, nodes]);

  const companions = (nodes || []).filter((n) => n.node_type === "companion" || n.node_type === "sensor");
  const openReports = (reports || []).filter((r) => !r.cleared_at);
  const cleared = (reports || []).filter((r) => r.cleared_at);

  return (
    <>
      <div className="top">
        <h1>Field work</h1>
        <span className="sp" />
        <a className="btn" href="/survey">Open survey mode</a>
        <a className="btn" href="/api/export/reports.geojson">Reports (GeoJSON)</a>
      </div>
      <div className="note" style={{ marginBottom: 12 }}>
        Reports go out as ordinary channel messages that any MeshCore app can read, like <span className="mono">⚑ flood 44.26012,-72.57501 Water over the road</span>.
        Messages in that form from anyone on the mesh show up here too.
      </div>
      <div className="fieldgrid">
        <div className="card" style={{ padding: 0, overflow: "hidden", minHeight: 380, position: "relative", display: "flex" }}>
          <div className="mapwrap" style={{ minHeight: 380 }}>
            <MapView
              onRetheme={draw}
              onReady={(m) => {
                map.current = m;
                m.on("click", (e: MapMouseEvent) => setPos({ lat: +e.lngLat.lat.toFixed(5), lon: +e.lngLat.lng.toFixed(5) }));
                m.getCanvas().style.cursor = "crosshair";
                draw(m);
                setReady(true);
              }}
            />
          </div>
        </div>

        <div className="stack">
          <div className="card">
            <h2>New report</h2>
            {!canAct && <div className="small faint">Sending reports needs the operator role (or the admin token).</div>}
            <div className="stack small">
              <label className="field">
                <span className="flabel">What</span>
                <select value={kind} onChange={(e) => setKind(e.target.value)}>
                  {Object.entries(kinds || {}).map(([k, l]) => <option key={k} value={k}>{KIND_ICON[k]} {l}</option>)}
                </select>
              </label>
              <div className="row">
                <span className="mono">{pos ? `${pos.lat.toFixed(5)}, ${pos.lon.toFixed(5)}` : "Click the map to place it"}</span>
                <button type="button" onClick={here}>Use my location</button>
              </div>
              <label className="field">
                <span className="flabel">Note ({80 - note.length} left)</span>
                <input maxLength={80} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Water over the road at the bridge" />
              </label>
              <div className="row">
                <label><input type="checkbox" checked={send} onChange={(e) => setSend(e.target.checked)} /> Send over the mesh on</label>
                <select aria-label="Channel" value={channel} onChange={(e) => setChannel(e.target.value)} disabled={!send}>
                  {channelNames.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
              <button className="primary" disabled={!pos || busy || !canAct} onClick={submitReport}>{send ? "Send report" : "Save report"}</button>
            </div>
            {msg && <div className="small" role="status" style={{ color: "var(--ok)", marginTop: 6 }}>{msg}</div>}
            {err && <div className="err" role="alert" style={{ marginTop: 6 }}>{err}</div>}
          </div>

          <div className="card">
            <h2>Open reports <span className="pill plain">{openReports.length}</span></h2>
            {openReports.length === 0 && <div className="small faint">Nothing open.</div>}
            {openReports.map((r) => (
              <div key={r.id} className="row chanrow small">
                <span style={{ flex: 1 }}>
                  {KIND_ICON[r.kind]} <strong>{kinds?.[r.kind] || r.kind}</strong> {r.note && <span>· {r.note}</span>}
                  <div className="faint">{r.source === "station" ? `from ${r.sender}` : `heard from ${r.sender || "the mesh"}`} · {ago(r.created_at)}{r.source === "station" && !r.sent ? " · not sent" : ""}</div>
                </span>
                <button type="button" onClick={() => map.current?.flyTo({ center: [r.lon, r.lat], zoom: 14 })}>Show</button>
                {canAct && <button type="button" onClick={() => clearReport(r.id)}>Clear</button>}
              </div>
            ))}
            {cleared.length > 0 && <div className="small faint" style={{ marginTop: 6 }}>{cleared.length} cleared in the last day.</div>}
          </div>
        </div>
      </div>

      <div className="grid2">
        <div className="card">
          <h2>Teams</h2>
          <div className="small muted" style={{ marginBottom: 8 }}>
            Each team gets its own private channel. Share the key with the team so their radios can join it; the station reads it too.
            Positions come from members' adverts and from <span className="mono">⚑ pos lat,lon</span> messages.
          </div>
          <div className="row small" style={{ marginBottom: 8 }}>
            <span>Show tracks (12 h):</span>
            <select value={String(teamView)} onChange={(e) => setTeamView(e.target.value === "all" || e.target.value === "none" ? (e.target.value as any) : Number(e.target.value))}>
              <option value="none">None</option>
              <option value="all">Every node that moved</option>
              {(teams || []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
          {(teams || []).map((t) => (
            <div key={t.id} className="chanrow" style={{ padding: "6px 0" }}>
              <div className="row small">
                <strong style={{ flex: 1 }}>{t.name}</strong>
                <span className="mono faint">{t.channel}</span>
                {canAct && <button type="button" onClick={() => act(() => api<any>(`/api/field/teams/${t.id}/radio`, { method: "POST" }), (r) => `On the station radio in slot ${r.radio_slot}.`)}>Add to radio</button>}
                {canAct && <button type="button" onClick={() => { setEditing(t.id); setPicked(new Set(t.members.map((m: any) => m.public_key))); }}>Members</button>}
                {canAct && <button type="button" className="danger" onClick={() => window.confirm(`Remove team ${t.name}?`) && act(() => api<any>(`/api/field/teams/${t.id}`, { method: "DELETE" }), (r) => (reloadTeams(), r.note))}>Remove</button>}
              </div>
              {t.channel_key && (
                <div className="small" style={{ marginTop: 4 }}>
                  Key <span className="mono" style={{ wordBreak: "break-all" }}>{t.channel_key}</span>{" "}
                  <button type="button" className="small" onClick={() => copyText(t.channel_key).then(() => setMsg("Key copied.")).catch((e) => setMsg(e.message))}>Copy</button>
                </div>
              )}
              <div className="small faint" style={{ marginTop: 2 }}>
                {t.members.length ? t.members.map((m: any) => `${m.name || m.public_key.slice(0, 8)}${m.last_seen ? ` (${ago(m.last_seen)})` : ""}`).join(", ") : "No members yet."}
              </div>
              {editing === t.id && (
                <div className="picklist" style={{ marginTop: 6, maxHeight: 200, overflowY: "auto" }}>
                  {companions.map((n) => (
                    <label key={n.public_key} className="row small">
                      <input type="checkbox" checked={picked.has(n.public_key)} onChange={(e) => {
                        const s = new Set(picked);
                        if (e.target.checked) s.add(n.public_key);
                        else s.delete(n.public_key);
                        setPicked(s);
                      }} />
                      {n.name || n.prefix} <span className="faint">{ago(n.last_seen)}</span>
                    </label>
                  ))}
                  <div className="row"><button className="primary" onClick={() => saveMembers(t.id)}>Save members</button><button onClick={() => setEditing(null)}>Cancel</button></div>
                </div>
              )}
            </div>
          ))}
          {canAct && (
            <form className="row" style={{ marginTop: 8 }} onSubmit={(e) => { e.preventDefault(); addTeam(); }}>
              <input aria-label="Team name" placeholder="North crew" value={teamName} onChange={(e) => setTeamName(e.target.value)} />
              <label className="small"><input type="checkbox" checked={toRadio} onChange={(e) => setToRadio(e.target.checked)} /> Add to the station radio</label>
              <button type="submit" disabled={teamName.trim().length < 2 || busy}>Create team</button>
            </form>
          )}
        </div>

        <SurveyImport canAct={canAct} />
      </div>
    </>
  );
}

function SurveyImport({ canAct }: { canAct: boolean }) {
  const { data, reload } = useApi<any[]>("/api/field/surveys?days=30");
  const [msg, setMsg] = useState("");
  async function upload(f: File) {
    const fmt = /\.gpx$/i.test(f.name) ? "gpx" : "csv";
    try {
      const r = await api<any>("/api/field/surveys/import", { method: "POST", body: { format: fmt, text: await f.text() } });
      setMsg(`Imported ${r.added} points${r.skipped ? `, skipped ${r.skipped}` : ""}.`);
      reload();
    } catch (e: any) {
      setMsg(e.message);
    }
  }
  const heard = (data || []).filter((s) => s.heard === 1).length;
  return (
    <div className="card">
      <h2>Field surveys</h2>
      <div className="small muted" style={{ marginBottom: 8 }}>
        Walk or drive the area with <a href="/survey">survey mode</a> on a phone: it saves each spot (heard or not, SNR if you have it) even with no signal and uploads when it can.
        Points show on the Coverage map. You can also import a CSV (lat, lon, and optionally time, heard, snr, rssi, via, note) or a GPX file.
      </div>
      <div className="small">{data ? `${data.length} points in the last 30 days, ${heard} heard.` : "…"}</div>
      {canAct && (
        <label className="row small" style={{ marginTop: 8 }}>
          Import <input type="file" accept=".csv,.gpx,text/csv,application/gpx+xml" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </label>
      )}
      {msg && <div className="small" role="status" style={{ marginTop: 6 }}>{msg}</div>}
    </div>
  );
}

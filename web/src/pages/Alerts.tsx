import { useState } from "react";
import { ago, api, useApi, useLive } from "../api";

const SEV: Record<string, string> = { danger: "bad", warning: "warn", info: "info" };
const KIND: Record<string, string> = {
  quiet: "Quiet",
  low_battery: "Low battery",
  battery_falling: "Battery falling",
  firmware: "Firmware behind",
  prefix_clash: "Prefix clash",
  observer_quiet: "Observer quiet",
};

export default function Alerts() {
  const [showAll, setShowAll] = useState(false);
  const { data, reload, error } = useApi<any[]>(`/api/alerts${showAll ? "" : "?open=true"}`, 30000);
  const { data: info } = useApi<any>("/api/station");
  const [msg, setMsg] = useState<string | null>(null);
  useLive((ev) => {
    if (ev.type === "alert" || ev.type === "alert_resolved") reload();
  });
  return (
    <>
      <div className="top">
        <h1>Alerts</h1>
        <span style={{ flex: 1 }} />
        <label className="row small">
          <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show resolved
        </label>
        <button onClick={() => api("/api/alerts/check", { method: "POST" }).then(() => (reload(), setMsg("Checked."))).catch((e) => setMsg(e.message))}>
          Check now
        </button>
      </div>
      {msg && <div className="note" style={{ marginBottom: 10 }}>{msg}</div>}
      <div className="note" style={{ marginBottom: 14 }}>
        New alerts go to Discord or your webhook when one is set in the station settings
        {info ? ` (${info.config_path})` : ""}. Alerts clear on their own once the problem is gone.
      </div>
      {error && <div className="err">{error}</div>}
      <div className="card tablewrap" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>When</th><th>Kind</th><th>What happened</th><th>Sent</th><th>State</th><th></th></tr></thead>
          <tbody>
            {(data || []).map((a) => (
              <tr key={a.id}>
                <td>{ago(a.created_at)}</td>
                <td><span className={`pill ${SEV[a.severity] || "plain"}`}>{KIND[a.kind] || a.kind}</span></td>
                <td>{a.message}</td>
                <td>{a.delivered ? "Yes" : <span className="faint">No</span>}</td>
                <td>{a.resolved_at ? `Cleared ${ago(a.resolved_at)}` : "Open"}</td>
                <td>{!a.resolved_at && <button onClick={() => api(`/api/alerts/${a.id}/resolve`, { method: "POST" }).then(reload).catch((e) => setMsg(e.message))}>Clear</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {data?.length === 0 && <div className="empty" style={{ padding: 16 }}>{showAll ? "No alerts yet." : "Nothing needs attention."}</div>}
      </div>
    </>
  );
}

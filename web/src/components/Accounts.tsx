import { FormEvent, useState } from "react";
import { ago, api, useApi } from "../api";
import { Me, refreshMe } from "../auth";

const ROLES: [string, string][] = [
  ["viewer", "Viewer: can look"],
  ["operator", "Operator: also fleet, alerts, field work"],
  ["admin", "Admin: also settings and accounts"],
];

/** Settings card: turn accounts on, then add, change and remove them. */
export default function Accounts({ me }: { me: Me | null }) {
  const on = !!me?.accounts_on;
  const { data: users, reload } = useApi<any[]>(on && me?.is_admin ? "/api/users" : null);
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [role, setRole] = useState("viewer");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  function done(m: string) {
    setMsg(m);
    setErr("");
    setName("");
    setPw("");
    reload();
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const call = on
      ? api("/api/users", { method: "POST", body: { username: name, password: pw, role } })
      : api("/api/auth/setup", { method: "POST", body: { username: name, password: pw } });
    call.then(() => (refreshMe(), done(on ? `Added ${name}.` : "Accounts are on. You're signed in as an admin."))).catch((x) => setErr(x.message));
  }

  function patch(id: number, body: object, m: string) {
    api(`/api/users/${id}`, { method: "PATCH", body }).then(() => done(m)).catch((x) => setErr(x.message));
  }

  return (
    <div className="card">
      <h2>Accounts</h2>
      {!on ? (
        <div className="small muted" style={{ marginBottom: 10 }}>
          Off: anyone who can open the dashboard can look; changes need the admin token or this computer. Create an admin account to
          require a sign-in everywhere except the public map. The admin token still works for scripts.
        </div>
      ) : (
        <div className="small muted" style={{ marginBottom: 10 }}>
          Everyone signs in. Viewers can look, operators can also run the fleet, alerts and field work, and admins can change settings and accounts.
        </div>
      )}
      {on && !me?.is_admin && <div className="small faint">Only admins can manage accounts.</div>}
      {on && me?.is_admin && (
        <div className="tablewrap" style={{ marginBottom: 10 }}>
          <table className="small" style={{ width: "100%" }}>
            <thead><tr><th>User</th><th>Role</th><th>Last sign-in</th><th /></tr></thead>
            <tbody>
              {(users || []).map((u) => (
                <tr key={u.id}>
                  <td>{u.username}{u.disabled ? <span className="pill bad" style={{ marginLeft: 6 }}>off</span> : null}{me?.user?.id === u.id ? <span className="faint"> (you)</span> : null}</td>
                  <td>
                    <select aria-label={`Role for ${u.username}`} value={u.role} onChange={(e) => patch(u.id, { role: e.target.value }, `${u.username} is now ${e.target.value}.`)}>
                      {ROLES.map(([r]) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </td>
                  <td className="faint">{u.last_login ? ago(u.last_login) : "never"}</td>
                  <td className="row" style={{ justifyContent: "flex-end" }}>
                    <button type="button" onClick={() => {
                      const p = window.prompt(`New password for ${u.username} (10+ characters)`);
                      if (p) patch(u.id, { password: p }, `Password changed for ${u.username}. Their other sign-ins were ended.`);
                    }}>Password</button>
                    <button type="button" onClick={() => patch(u.id, { disabled: !u.disabled }, `${u.username} ${u.disabled ? "can sign in again" : "can't sign in now"}.`)}>
                      {u.disabled ? "Turn on" : "Turn off"}
                    </button>
                    <button type="button" className="danger" onClick={() => {
                      if (window.confirm(`Remove ${u.username}?`)) api(`/api/users/${u.id}`, { method: "DELETE" }).then(() => done(`Removed ${u.username}.`)).catch((x) => setErr(x.message));
                    }}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(!on || me?.is_admin) && (
        <form className="row" onSubmit={submit}>
          <input aria-label="Username" placeholder="username" autoComplete="off" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 140 }} />
          <input aria-label="Password" type="password" placeholder="password (10+ characters)" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} style={{ width: 200 }} />
          {on && (
            <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value)}>
              {ROLES.map(([r, l]) => <option key={r} value={r}>{l}</option>)}
            </select>
          )}
          <button type="submit" className="primary" disabled={!name || pw.length < 10}>{on ? "Add account" : "Create admin and turn accounts on"}</button>
        </form>
      )}
      {msg && <div className="small" style={{ color: "var(--ok)", marginTop: 6 }} role="status">{msg}</div>}
      {err && <div className="err" style={{ marginTop: 6 }} role="alert">{err}</div>}
    </div>
  );
}

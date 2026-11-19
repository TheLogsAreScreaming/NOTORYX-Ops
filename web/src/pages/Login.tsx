import { FormEvent, useState } from "react";
import { api } from "../api";
import { refreshMe } from "../auth";

export default function Login({ publicMap }: { publicMap: boolean }) {
  const [username, setU] = useState("");
  const [password, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    api("/api/auth/login", { method: "POST", body: { username, password } })
      .then(() => refreshMe())
      .catch((x) => setErr(x.message))
      .finally(() => setBusy(false));
  }

  return (
    <div className="signin">
      <form className="card" onSubmit={submit} aria-labelledby="signin-title">
        <div className="brand" style={{ padding: "0 0 10px" }}>
          <img src="/icon.svg" alt="" />
          <span>NOTORYX Ops</span>
        </div>
        <h1 id="signin-title" style={{ fontSize: 18, margin: "0 0 12px" }}>Sign in</h1>
        <label className="field">
          <span className="flabel">Username</span>
          <input autoFocus autoComplete="username" value={username} onChange={(e) => setU(e.target.value)} />
        </label>
        <label className="field" style={{ marginTop: 8 }}>
          <span className="flabel">Password</span>
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setP(e.target.value)} />
        </label>
        {err && <div className="err" role="alert" style={{ marginTop: 8 }}>{err}</div>}
        <button className="primary" type="submit" disabled={busy || !username || !password} style={{ marginTop: 12, width: "100%" }}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {publicMap && (
          <div className="small muted" style={{ marginTop: 12 }}>
            No account? <a href="/public">Open the public map</a>.
          </div>
        )}
      </form>
    </div>
  );
}

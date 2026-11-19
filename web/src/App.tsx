import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useApi, useConnected } from "./api";
import { LookButton } from "./components/ThemePicker";
import LiveMap from "./pages/LiveMap";
import Packets from "./pages/Packets";
import Nodes from "./pages/Nodes";
import Channels from "./pages/Channels";
import Fleet from "./pages/Fleet";
import Observers from "./pages/Observers";
import Coverage from "./pages/Coverage";
import Analytics from "./pages/Analytics";
import Alerts from "./pages/Alerts";
import Station from "./pages/Station";
import Settings from "./pages/Settings";

const NAV: [string, string][] = [
  ["/", "Live map"],
  ["/packets", "Packets"],
  ["/nodes", "Nodes"],
  ["/channels", "Channels"],
  ["/fleet", "My fleet"],
  ["/observers", "Observers"],
  ["/coverage", "Coverage"],
  ["/analytics", "Analytics"],
  ["/alerts", "Alerts"],
  ["/station", "Station"],
  ["/settings", "Settings"],
];

export default function App() {
  const live = useConnected();
  const { data: open } = useApi<any[]>("/api/alerts?open=true", 30000);
  const { data: info } = useApi<any>("/api/station", 15000);
  const loc = useLocation();
  const full = loc.pathname === "/" || loc.pathname === "/coverage";
  return (
    <div className="shell">
      <nav className="side" aria-label="Pages">
        <div className="brand">
          <img src="/icon.svg" alt="" />
          <span>NOTORYX Ops</span>
        </div>
        {NAV.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"}>
            {label}
            {to === "/alerts" && open && open.length > 0 && <span className="count pill warn">{open.length}</span>}
          </NavLink>
        ))}
        <div className="foot">
          <LookButton />
          <div style={{ marginTop: 8 }}>{info?.name || "Station"}</div>
          <div>
            <span className="dot" style={{ background: live ? "var(--ok)" : "var(--bad)" }} />
            {live ? "Live" : "Reconnecting"}
            {info?.demo ? " · demo data" : ""}
          </div>
          <div>v{info?.version || ""}</div>
        </div>
      </nav>
      <main className={full ? "main full" : "main"}>
        <Routes>
          <Route path="/" element={<LiveMap />} />
          <Route path="/packets" element={<Packets />} />
          <Route path="/nodes" element={<Nodes />} />
          <Route path="/channels" element={<Channels />} />
          <Route path="/fleet" element={<Fleet />} />
          <Route path="/observers" element={<Observers />} />
          <Route path="/coverage" element={<Coverage />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/station" element={<Station />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<div className="empty">That page doesn't exist.</div>} />
        </Routes>
      </main>
    </div>
  );
}

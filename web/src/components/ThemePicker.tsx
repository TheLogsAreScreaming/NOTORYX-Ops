import { useEffect, useRef, useState } from "react";
import { THEMES, Theme, applyTheme, currentTheme, onTheme } from "../themes";

export function useLook(): Theme {
  const [t, setT] = useState(currentTheme());
  useEffect(() => onTheme(setT), []);
  return t;
}

function Chip({ t }: { t: Theme }) {
  return (
    <span className="chip" aria-hidden="true">
      <i style={{ background: t.ui.bg }} />
      <i style={{ background: t.ui.accent }} />
      <i style={{ background: t.ui.accent2 }} />
    </span>
  );
}

/** Small picker for the sidebar: a button showing the current look that opens a list. */
export function LookButton() {
  const t = useLook();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return (
    <div className="lookpick" ref={box}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", background: "transparent" }}
      >
        <Chip t={t} />
        <span>{t.name}</span>
      </button>
      {open && (
        <div className="lookpop" role="listbox" aria-label="Look">
          {THEMES.map((x) => (
            <button
              key={x.id}
              role="option"
              aria-selected={x.id === t.id}
              className={x.id === t.id ? "on" : ""}
              onClick={() => {
                applyTheme(x.id);
                setOpen(false);
              }}
            >
              <Chip t={x} />
              <span>
                <div>{x.name}</div>
                <div className="small faint">{x.blurb}</div>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Gallery of looks with a small preview of each, for the Station page. */
export function LookGallery() {
  const t = useLook();
  return (
    <div className="looks" role="radiogroup" aria-label="Look">
      {THEMES.map((x) => (
        <button
          key={x.id}
          role="radio"
          aria-checked={x.id === t.id}
          className={`look ${x.id === t.id ? "on" : ""}`}
          onClick={() => applyTheme(x.id)}
        >
          <div
            className="sw"
            style={{
              background: `radial-gradient(120px 60px at 85% 0%, ${x.ui.accent2}33, transparent), radial-gradient(140px 70px at 0% 100%, ${x.ui.accent}33, transparent), ${x.map.land}`,
            }}
          >
            <svg viewBox="0 0 150 64" width="100%" height="64" aria-hidden="true" style={{ position: "absolute", inset: 0 }}>
              <path d="M0 48 C30 40 40 58 75 50 S120 30 150 38 L150 64 L0 64 Z" fill={x.map.water} />
              <polyline points="22,20 62,34 104,18 128,40" fill="none" stroke={x.routes.advert} strokeWidth="2.5" strokeLinecap="round" />
              <polyline points="62,34 80,52" fill="none" stroke={x.routes.group} strokeWidth="2" strokeDasharray="3 3" />
              <circle cx="22" cy="20" r="4" fill={x.nodes.companion} />
              <circle cx="62" cy="34" r="5" fill={x.nodes.repeater} />
              <circle cx="62" cy="34" r="9" fill="none" stroke={x.ui.accent} strokeWidth="1.5" />
              <circle cx="104" cy="18" r="5" fill={x.nodes.repeater} />
              <circle cx="128" cy="40" r="4" fill={x.nodes.room} />
              <circle cx="80" cy="52" r="3.5" fill={x.nodes.sensor} />
            </svg>
          </div>
          <div className="meta" style={{ background: x.ui.panel, color: x.ui.text }}>
            <b>{x.name}</b>
            <span style={{ color: x.ui.text3 }}>{x.blurb}</span>
          </div>
        </button>
      ))}
    </div>
  );
}

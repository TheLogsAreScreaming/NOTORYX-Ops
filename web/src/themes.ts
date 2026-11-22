/** NOTORYX Ops looks. Each theme sets the page colours, the map's own colours, node and
 *  route colours, the signal scale and chart colours, so the whole dashboard changes. */

export type Theme = {
  id: string;
  name: string;
  blurb: string;
  mode: "dark" | "light";
  ui: {
    bg: string; panel: string; panel2: string; line: string; lineStrong: string;
    text: string; text2: string; text3: string;
    accent: string; accentBg: string; accent2: string; onAccent: string;
    blue: string; blueBg: string; ok: string; okBg: string; warn: string; warnBg: string;
    bad: string; badBg: string; info: string; infoBg: string;
  };
  map: { land: string; water: string; park: string; road: string; roadMajor: string; building: string; label: string; halo: string; boundary: string };
  nodes: { repeater: string; room: string; companion: string; sensor: string; unknown: string };
  routes: { advert: string; group: string; dm: string; ack: string; trace: string };
  /** SNR colours, best first: >=5, 0..5, -5..0, -10..-5, < -10 */
  snr: [string, string, string, string, string];
  chart: [string, string, string];
};

export const THEMES: Theme[] = [
  {
    // NOTORYX BLACKOUT: matte, charcoal, gunmetal, steel, frost. No brand colour; the colours
    // that carry meaning (status, node types, routes, signal scale, charts) stay coloured.
    id: "blackout", name: "BLACKOUT", blurb: "Matte black and frost steel", mode: "dark",
    ui: { bg: "#080808", panel: "#242424", panel2: "#1a1a1a", line: "#484848", lineStrong: "#5c5c5c", text: "#e8e8e8", text2: "#b8b8b8", text3: "#969696", accent: "#e8e8e8", accentBg: "#2e2e2e", accent2: "#888888", onAccent: "#080808", blue: "#9fb4c8", blueBg: "#1f262c", ok: "#9cc69b", okBg: "#1c2a1c", warn: "#d9b45a", warnBg: "#30291a", bad: "#e8806f", badBg: "#341e1b", info: "#b7a6d6", infoBg: "#25212e" },
    map: { land: "#0e0e0e", water: "#030303", park: "#121212", road: "#262626", roadMajor: "#383838", building: "#151515", label: "#888888", halo: "#080808", boundary: "#484848" },
    nodes: { repeater: "#e8e8e8", room: "#c9a227", companion: "#9fb4c8", sensor: "#b7a6d6", unknown: "#888888" },
    routes: { advert: "#e8e8e8", group: "#9fb4c8", dm: "#c9a227", ack: "#5c5c5c", trace: "#b7a6d6" },
    snr: ["#9cc69b", "#c6c98a", "#d9b45a", "#d9925a", "#e8806f"],
    chart: ["#e8e8e8", "#c9a227", "#9fb4c8"],
  },
  {
    id: "aurora", name: "Aurora", blurb: "Violet night with a pink glow", mode: "dark",
    ui: { bg: "#0e0c1d", panel: "#16132b", panel2: "#1e1a38", line: "#2b2650", lineStrong: "#3d3770", text: "#eeedff", text2: "#b3aed8", text3: "#7f7aab", accent: "#a98bff", accentBg: "#2a2150", accent2: "#ff8fb1", onAccent: "#140f2b", blue: "#7cc4ff", blueBg: "#182a48", ok: "#6ee7b7", okBg: "#123329", warn: "#ffc46b", warnBg: "#3a2a12", bad: "#ff7a8a", badBg: "#3d1823", info: "#c9b0ff", infoBg: "#2a2150" },
    map: { land: "#13112a", water: "#0a0820", park: "#17153a", road: "#29244f", roadMajor: "#3b3373", building: "#1b1838", label: "#8f88c6", halo: "#0e0c1d", boundary: "#4d4290" },
    nodes: { repeater: "#a98bff", room: "#ff8fb1", companion: "#7cc4ff", sensor: "#ffc46b", unknown: "#7f7aab" },
    routes: { advert: "#a98bff", group: "#7cc4ff", dm: "#ff8fb1", ack: "#6f6a99", trace: "#ffc46b" },
    snr: ["#6ee7b7", "#b8f28c", "#ffc46b", "#ff9b6b", "#ff7a8a"],
    chart: ["#a98bff", "#ff8fb1", "#7cc4ff"],
  },
  {
    id: "ember", name: "Ember", blurb: "Warm coals and copper", mode: "dark",
    ui: { bg: "#140d0a", panel: "#1d1410", panel2: "#271b15", line: "#3a2a21", lineStrong: "#523c2f", text: "#f8ede5", text2: "#cdb5a6", text3: "#93796b", accent: "#ff9248", accentBg: "#3d2214", accent2: "#ffd166", onAccent: "#1d0e05", blue: "#8fb8de", blueBg: "#1f2a35", ok: "#a3d977", okBg: "#1f2e16", warn: "#ffd166", warnBg: "#3a2e10", bad: "#ff4d6d", badBg: "#401424", info: "#e0a6ff", infoBg: "#34203d" },
    map: { land: "#18100c", water: "#0c0807", park: "#1f1712", road: "#3a2a21", roadMajor: "#5c3f2b", building: "#22180f", label: "#ad8e7b", halo: "#140d0a", boundary: "#6e4c37" },
    nodes: { repeater: "#ff9248", room: "#ffd166", companion: "#8fb8de", sensor: "#e0a6ff", unknown: "#93796b" },
    routes: { advert: "#ff9248", group: "#ffd166", dm: "#e0a6ff", ack: "#7a6457", trace: "#8fb8de" },
    snr: ["#a3d977", "#d8e07a", "#ffd166", "#ff9248", "#ff4d6d"],
    chart: ["#ff9248", "#ffd166", "#8fb8de"],
  },
  {
    id: "nebula", name: "Nebula", blurb: "Deep space, electric magenta", mode: "dark",
    ui: { bg: "#07070d", panel: "#0f0f1a", panel2: "#161627", line: "#24243d", lineStrong: "#34345a", text: "#f2f2ff", text2: "#ababd3", text3: "#71719b", accent: "#ff4fd8", accentBg: "#3a1235", accent2: "#4f8bff", onAccent: "#1a0517", blue: "#4f8bff", blueBg: "#142247", ok: "#4fffb0", okBg: "#0e3326", warn: "#ffd24f", warnBg: "#3a3010", bad: "#ff4f6d", badBg: "#40121c", info: "#b388ff", infoBg: "#261a47" },
    map: { land: "#0b0b16", water: "#05050b", park: "#0f0f1e", road: "#1f1f3a", roadMajor: "#30305c", building: "#121220", label: "#7d7db4", halo: "#07070d", boundary: "#3e2d72" },
    nodes: { repeater: "#ff4fd8", room: "#b388ff", companion: "#4f8bff", sensor: "#ffd24f", unknown: "#71719b" },
    routes: { advert: "#ff4fd8", group: "#4f8bff", dm: "#b388ff", ack: "#56567f", trace: "#ffd24f" },
    snr: ["#4fffb0", "#b9ff4f", "#ffd24f", "#ff9a4f", "#ff4f6d"],
    chart: ["#ff4fd8", "#4f8bff", "#ffd24f"],
  },
  {
    id: "graphite", name: "Graphite", blurb: "Quiet greys, bone white", mode: "dark",
    ui: { bg: "#111111", panel: "#181818", panel2: "#202020", line: "#2c2c2c", lineStrong: "#3d3d3d", text: "#efece4", text2: "#b4b0a6", text3: "#7c7972", accent: "#e8e3d3", accentBg: "#2b2a26", accent2: "#c9a227", onAccent: "#141414", blue: "#9fb4c8", blueBg: "#20262c", ok: "#9cc69b", okBg: "#1d2a1d", warn: "#d9b45a", warnBg: "#30291a", bad: "#e07a6a", badBg: "#341e1b", info: "#b7a6d6", infoBg: "#25212e" },
    map: { land: "#161616", water: "#0c0c0c", park: "#1a1a1a", road: "#2a2a2a", roadMajor: "#3b3b3b", building: "#1c1c1c", label: "#8e8a82", halo: "#111111", boundary: "#4a4740" },
    nodes: { repeater: "#e8e3d3", room: "#c9a227", companion: "#9fb4c8", sensor: "#b7a6d6", unknown: "#7c7972" },
    routes: { advert: "#e8e3d3", group: "#9fb4c8", dm: "#c9a227", ack: "#5e5b55", trace: "#b7a6d6" },
    snr: ["#9cc69b", "#c6c98a", "#d9b45a", "#d9925a", "#e07a6a"],
    chart: ["#e8e3d3", "#c9a227", "#9fb4c8"],
  },
  {
    id: "sandstone", name: "Sandstone", blurb: "Desert paper and terracotta", mode: "light",
    ui: { bg: "#f5efe6", panel: "#fffbf5", panel2: "#f8f1e7", line: "#e6dccd", lineStrong: "#d4c6b2", text: "#2b2118", text2: "#5e4e40", text3: "#887868", accent: "#c2552d", accentBg: "#f6e2d8", accent2: "#2e7d6b", onAccent: "#ffffff", blue: "#2f6690", blueBg: "#e2ecf3", ok: "#3d7a3a", okBg: "#e5f0dc", warn: "#9a610c", warnBg: "#f8ebd2", bad: "#b3261e", badBg: "#f7dfdc", info: "#7a4ea0", infoBg: "#efe5f6" },
    map: { land: "#f3ece1", water: "#c8dadf", park: "#e4e5cf", road: "#fffaf2", roadMajor: "#ead8c0", building: "#e8dfd2", label: "#76665a", halo: "#f3ece1", boundary: "#b9a48b" },
    nodes: { repeater: "#c2552d", room: "#7a4ea0", companion: "#2f6690", sensor: "#2e7d6b", unknown: "#887868" },
    routes: { advert: "#c2552d", group: "#2f6690", dm: "#7a4ea0", ack: "#a39482", trace: "#2e7d6b" },
    snr: ["#3d7a3a", "#7f8f2a", "#b7860f", "#cf6a2a", "#b3261e"],
    chart: ["#c2552d", "#2f6690", "#2e7d6b"],
  },
];

const KEY = "notoryx-ops-look";
// Resolved colours of the current look, read by add-on pages so they match the dashboard.
export const VARS_KEY = "notoryx-ops-look-vars";
export const DEFAULT_THEME = "blackout";
let current: Theme = THEMES.find((x) => x.id === DEFAULT_THEME) || THEMES[0];

export function currentTheme(): Theme {
  return current;
}

function css(t: Theme): Record<string, string> {
  const u = t.ui;
  return {
    "--bg": u.bg, "--panel": u.panel, "--panel-2": u.panel2, "--line": u.line, "--line-strong": u.lineStrong,
    "--text": u.text, "--text-2": u.text2, "--text-3": u.text3,
    "--accent": u.accent, "--accent-bg": u.accentBg, "--accent-2": u.accent2, "--on-accent": u.onAccent,
    "--blue": u.blue, "--blue-bg": u.blueBg, "--ok": u.ok, "--ok-bg": u.okBg, "--warn": u.warn, "--warn-bg": u.warnBg,
    "--bad": u.bad, "--bad-bg": u.badBg, "--info": u.info, "--info-bg": u.infoBg,
  };
}

export function applyTheme(id: string, save = true) {
  // A saved look that no longer exists (or a bad value) falls back to the default look.
  const t = THEMES.find((x) => x.id === id) || THEMES.find((x) => x.id === DEFAULT_THEME) || THEMES[0];
  current = t;
  const root = document.documentElement;
  for (const [k, v] of Object.entries(css(t))) root.style.setProperty(k, v);
  root.dataset.mode = t.mode;
  root.style.colorScheme = t.mode;
  try {
    localStorage.setItem(VARS_KEY, JSON.stringify({ id: t.id, mode: t.mode, vars: css(t) }));
  } catch {
    /* storage blocked: add-on pages keep their default look */
  }
  if (save) {
    try {
      localStorage.setItem(KEY, t.id);
    } catch {
      /* storage blocked: the look lasts for this visit */
    }
  }
  window.dispatchEvent(new CustomEvent("notoryx-theme", { detail: t.id }));
}

export function restoreTheme() {
  let id = DEFAULT_THEME;
  try {
    id = localStorage.getItem(KEY) || DEFAULT_THEME;
  } catch {
    /* storage blocked: default look */
  }
  applyTheme(id, false);
  if (current.id !== id) {
    // The saved look was removed: remember the fallback so the old value is gone.
    try {
      localStorage.setItem(KEY, current.id);
    } catch {
      /* storage blocked */
    }
  }
}

export function onTheme(fn: (t: Theme) => void): () => void {
  const h = () => fn(current);
  window.addEventListener("notoryx-theme", h);
  return () => window.removeEventListener("notoryx-theme", h);
}

export function snrColor(snr: number | null | undefined): string {
  const s = current.snr;
  if (snr == null) return current.nodes.unknown;
  if (snr >= 5) return s[0];
  if (snr >= 0) return s[1];
  if (snr >= -5) return s[2];
  if (snr >= -10) return s[3];
  return s[4];
}

export function nodeColor(type: string | null | undefined): string {
  const n = current.nodes as Record<string, string>;
  return n[type || "unknown"] || n.unknown;
}

export function routeColor(ptype: string): string {
  const r = current.routes;
  if (ptype === "advert") return r.advert;
  if (ptype === "group_text" || ptype === "group_data") return r.group;
  if (ptype === "ack" || ptype === "path") return r.ack;
  if (ptype === "trace") return r.trace;
  return r.dm;
}

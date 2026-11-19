import { useEffect, useRef, useState, useCallback } from "react";

export type Packet = {
  id: number;
  hash: string;
  received_at: number;
  observer: string;
  source: string;
  route: string;
  ptype: string;
  hops: number;
  hash_size: number;
  path: string[];
  snr: number | null;
  rssi: number | null;
  raw: string;
  channel_hash: string | null;
  src_hash: string | null;
  dest_hash: string | null;
  node_key: string | null;
  summary: string;
  repeat?: boolean;
  advert?: { name: string | null; node_type: string; lat: number | null; lon: number | null };
};

export type Node = {
  public_key: string;
  prefix: string;
  name: string | null;
  node_type: string | null;
  lat: number | null;
  lon: number | null;
  first_seen: number;
  last_seen: number;
  adverts: number;
  best_snr: number | null;
  direct_observers: string[];
  /** Not heard for longer than the station's "Mark nodes stale after" setting. */
  stale?: boolean;
  /** On the station's ignore list (only returned with include_ignored=true). */
  ignored?: boolean;
};

/** Hide a node everywhere on this station. Its packets are still stored. */
export function ignoreNode(publicKey: string, name?: string | null) {
  return api("/api/ignored", { method: "POST", body: { public_key: publicKey, name: name ?? null } });
}

export function unignoreNode(publicKey: string) {
  return api(`/api/ignored/${publicKey}`, { method: "DELETE" });
}

export type LiveEvent = { type: string; [k: string]: any };

const TOKEN_KEY = "notoryx-ops-token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setToken(t: string) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage blocked: the token only lasts for this page */
  }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const tok = getToken();
  if (tok && opts.method && opts.method !== "GET") headers["Authorization"] = `Bearer ${tok}`;
  const r = await fetch(path, {
    method: opts.method || "GET",
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* body was not JSON */
    }
    throw new Error(msg);
  }
  return r.json();
}

/** Load a JSON endpoint, reload it every `everyMs`, and on demand. */
export function useApi<T>(path: string | null, everyMs = 0): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    api<T>(path)
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e) => alive && setError(String(e.message || e)));
    return () => {
      alive = false;
    };
  }, [path, tick]);
  useEffect(() => {
    if (!everyMs) return;
    const t = setInterval(reload, everyMs);
    return () => clearInterval(t);
  }, [everyMs, reload]);
  return { data, error, reload };
}

type Listener = (e: LiveEvent) => void;
const listeners = new Set<Listener>();
let sock: WebSocket | null = null;
let live = false;
const liveListeners = new Set<(v: boolean) => void>();

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  sock = new WebSocket(`${proto}://${location.host}/ws`);
  sock.onopen = () => {
    live = true;
    liveListeners.forEach((f) => f(true));
  };
  sock.onmessage = (m) => {
    try {
      const ev = JSON.parse(m.data) as LiveEvent;
      listeners.forEach((f) => f(ev));
    } catch {
      /* ignore malformed frames */
    }
  };
  sock.onclose = () => {
    live = false;
    liveListeners.forEach((f) => f(false));
    setTimeout(connect, 3000);
  };
}

/** Subscribe to live station events for the life of a component. */
export function useLive(fn: Listener) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!sock) connect();
    const l: Listener = (e) => ref.current(e);
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
}

export function useConnected(): boolean {
  const [v, setV] = useState(live);
  useEffect(() => {
    if (!sock) connect();
    liveListeners.add(setV);
    return () => {
      liveListeners.delete(setV);
    };
  }, []);
  return v;
}

export function ago(t: number | null | undefined, now = Date.now() / 1000): string {
  if (!t) return "never";
  const s = Math.max(0, Math.round(now - t));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${(s / 3600).toFixed(1)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function clock(t: number): string {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function duration(s: number | null | undefined): string {
  if (s == null) return "—";
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 86400) return `${(s / 3600).toFixed(1)} h`;
  return `${Math.round(s / 86400)} d`;
}

export function fmt(n: number | null | undefined, digits = 0): string {
  return n == null ? "—" : n.toFixed(digits);
}

export const TYPE_PILL: Record<string, string> = {
  advert: "ok",
  group_text: "blue",
  group_data: "blue",
  text: "info",
  request: "info",
  response: "info",
  anon_request: "warn",
  ack: "plain",
  path: "plain",
  trace: "warn",
};

export const TYPE_LABEL: Record<string, string> = {
  advert: "Advert",
  group_text: "Group",
  group_data: "Group data",
  text: "DM",
  request: "Request",
  response: "Response",
  anon_request: "Login",
  ack: "Ack",
  path: "Path",
  trace: "Trace",
  multipart: "Multipart",
  control: "Control",
  raw_custom: "Custom",
};

/** Find the node a path hash points at, when only one node with a position matches. */
export function makeResolver(nodes: Node[]) {
  const by = new Map<string, Node[]>();
  for (const n of nodes) {
    const p = n.prefix.slice(0, 2).toLowerCase();
    if (!by.has(p)) by.set(p, []);
    by.get(p)!.push(n);
  }
  return (hash: string | null | undefined): Node | null => {
    if (!hash) return null;
    const c = by.get(hash.slice(0, 2).toLowerCase()) || [];
    const reps = c.filter((n) => n.node_type === "repeater" || n.node_type === "room");
    const pick = reps.length ? reps : c;
    return pick.length === 1 ? pick[0] : null;
  };
}

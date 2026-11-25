/** Distance units for the dashboard: kilometres and metres, or miles and feet.
 * The station always works in metres; this only changes what people see and type. */
import { useEffect, useState } from "react";

export type Units = "metric" | "imperial";

export const UNITS_KEY = "notoryx-ops-units";
export const MILE_M = 1609.344;
export const FOOT_M = 0.3048;
const KM2_PER_MI2 = 2.589988110336;

const listeners = new Set<(u: Units) => void>();

/** Miles where the browser's region uses them (US, Liberia, Myanmar), otherwise kilometres. */
export function defaultUnits(lang: string | undefined = typeof navigator === "undefined" ? undefined : navigator.language): Units {
  const region = (lang || "").split("-")[1]?.toUpperCase();
  return region === "US" || region === "LR" || region === "MM" ? "imperial" : "metric";
}

export function getUnits(): Units {
  try {
    const v = localStorage.getItem(UNITS_KEY);
    if (v === "metric" || v === "imperial") return v;
  } catch {
    /* storage blocked: fall back to the browser's region */
  }
  return defaultUnits();
}

export function setUnits(u: Units): void {
  try {
    localStorage.setItem(UNITS_KEY, u);
  } catch {
    /* not saved, but still applied for this visit */
  }
  current = u;
  listeners.forEach((fn) => fn(u));
}

let current: Units = getUnits();

/** The chosen units; re-renders when they change. */
export function useUnits(): Units {
  const [u, setU] = useState<Units>(current);
  useEffect(() => {
    listeners.add(setU);
    return () => void listeners.delete(setU);
  }, []);
  return u;
}

function trim(n: number, digits: number): string {
  return String(Number(n.toFixed(digits)));
}

/** A ground distance given in metres: "850 m" / "2.4 km", or "900 ft" / "1.5 mi". */
export function fmtDistance(m: number | null | undefined, u: Units): string {
  if (m == null || !isFinite(m)) return "—";
  if (u === "imperial") {
    const mi = m / MILE_M;
    if (mi < 0.1) return `${Math.round(m / FOOT_M)} ft`;
    return `${trim(mi, mi >= 10 ? 0 : 1)} mi`;
  }
  return m >= 1000 ? `${trim(m / 1000, m >= 10000 ? 0 : 1)} km` : `${Math.round(m)} m`;
}

/** A height or elevation given in metres: "120 m" or "394 ft". */
export function fmtHeight(m: number | null | undefined, u: Units): string {
  if (m == null || !isFinite(m)) return "—";
  return u === "imperial" ? `${Math.round(m / FOOT_M)} ft` : `${trim(m, m < 10 ? 1 : 0)} m`;
}

/** An area given in km²: "12.5 km²" or "4.8 mi²". */
export function fmtArea(km2: number | null | undefined, u: Units): string {
  if (km2 == null || !isFinite(km2)) return "—";
  const v = u === "imperial" ? km2 / KM2_PER_MI2 : km2;
  return `${trim(v, v >= 100 ? 0 : 1)} ${u === "imperial" ? "mi²" : "km²"}`;
}

/** The unit shown next to height inputs. */
export function heightUnit(u: Units): string {
  return u === "imperial" ? "ft" : "m";
}

/** Metres → the number shown in a height input. */
export function heightIn(m: number, u: Units): number {
  return u === "imperial" ? Number((m / FOOT_M).toFixed(1)) : m;
}

/** The number typed in a height input → metres. */
export function heightOut(v: number, u: Units): number {
  return u === "imperial" ? Number((v * FOOT_M).toFixed(2)) : v;
}

/** Search radius choices, as [km value sent to the station, label]. */
export function radiusChoices(u: Units, max: "large" | "small" = "large"): [number, string][] {
  if (u === "imperial") {
    const mi = max === "large" ? [3, 6, 10, 15, 25] : [3, 6, 10, 15];
    return mi.map((r) => [Number((r * MILE_M / 1000).toFixed(3)), `${r} mi`]);
  }
  const km = max === "large" ? [5, 10, 15, 25, 40] : [5, 10, 15, 25];
  return km.map((r) => [r, `${r} km`]);
}

/** The choice closest to a radius in km, so a picker never shows a value it has no option for. */
export function nearestRadius(km: number, choices: [number, string][]): number {
  return choices.reduce((best, [v]) => (Math.abs(v - km) < Math.abs(best - km) ? v : best), choices[0][0]);
}

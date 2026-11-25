/** Range rings: step sizing and ring/spoke geometry around a point. Pure functions, no map code. */

export type Units = "metric" | "imperial";

/** The dashboard has no unit setting yet, so rings are metric. */
export const UNITS: Units = "metric";

const EARTH_M = 6371000;
const MILE_M = 1609.344;
export const RING_COUNT = 4;
/** Compass tags sit this far out, as a share of the outer ring. */
export const CARDINAL_AT = 1.08;

/** Great-circle distance in metres. */
export function distanceM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = Math.PI / 180;
  const p1 = lat1 * r;
  const p2 = lat2 * r;
  const dp = p2 - p1;
  const dl = (lon2 - lon1) * r;
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * EARTH_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** The point `dist` metres from (lat, lon) along `bearing` degrees from north. Returns [lon, lat]. */
export function destination(lat: number, lon: number, bearing: number, dist: number): [number, number] {
  const r = Math.PI / 180;
  const d = dist / EARTH_M;
  const b = bearing * r;
  const p1 = lat * r;
  const l1 = lon * r;
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [((l2 / r + 540) % 360) - 180, p2 / r];
}

/**
 * The ring spacing for a span: span / 4 rounded down to 1, 2 or 5 x 10^n.
 * Metric steps are metres and start at 10 m; imperial steps are miles and start at 0.1 mi.
 * Returns the step in metres and in the display unit.
 */
export function ringStep(spanM: number, units: Units = UNITS): { meters: number; value: number } {
  const perUnit = units === "metric" ? 1 : MILE_M;
  const min = units === "metric" ? 10 : 0.1;
  const raw = spanM / RING_COUNT / perUnit;
  let value = min;
  if (Number.isFinite(raw) && raw > min) {
    const exp = Math.floor(Math.log10(raw));
    const base = 10 ** exp;
    const m = [5, 2, 1].find((k) => k * base <= raw * (1 + 1e-9)) ?? 1;
    value = Number((m * base).toPrecision(6));
  }
  return { meters: value * perUnit, value };
}

/** A distance label for ring k (1-based) at a given step. */
export function ringLabel(k: number, step: { value: number }, units: Units = UNITS): string {
  const v = Number((k * step.value).toPrecision(6));
  if (units === "imperial") return `${v} mi`;
  return v >= 1000 ? `${Number((v / 1000).toPrecision(6))} km` : `${v} m`;
}

/** Rings, spokes and labels as GeoJSON, centred on (lat, lon). */
export function ringFeatures(lat: number, lon: number, step: { meters: number; value: number }, units: Units = UNITS) {
  const lines: GeoJSON.Feature[] = [];
  const labels: GeoJSON.Feature[] = [];
  const SEG = 96;
  for (let k = 1; k <= RING_COUNT; k++) {
    const dist = k * step.meters;
    const coords: [number, number][] = [];
    for (let i = 0; i <= SEG; i++) coords.push(destination(lat, lon, (i * 360) / SEG, dist));
    lines.push({ type: "Feature", geometry: { type: "LineString", coordinates: coords }, properties: { kind: "ring" } });
    labels.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: destination(lat, lon, 0, dist) },
      properties: { kind: "distance", text: ringLabel(k, step, units) },
    });
  }
  const outer = RING_COUNT * step.meters;
  const CARD: Record<number, string> = { 0: "N", 90: "E", 180: "S", 270: "W" };
  for (let b = 0; b < 360; b += 45) {
    lines.push({
      type: "Feature",
      geometry: { type: "LineString", coordinates: [[lon, lat], destination(lat, lon, b, outer)] },
      properties: { kind: "spoke" },
    });
    if (CARD[b]) {
      labels.push({
        type: "Feature",
        geometry: { type: "Point", coordinates: destination(lat, lon, b, outer * CARDINAL_AT) },
        properties: { kind: "cardinal", text: CARD[b] },
      });
    }
  }
  return {
    lines: { type: "FeatureCollection", features: lines } as GeoJSON.FeatureCollection,
    labels: { type: "FeatureCollection", features: labels } as GeoJSON.FeatureCollection,
  };
}

/** A screen rectangle in pixels, relative to the map container. */
export type Rect = { left: number; top: number; right: number; bottom: number };

/**
 * The part of the map that no panel or control covers. Each obstacle that overlaps the
 * free area is cut off along whichever side keeps the largest free rectangle, so the
 * result never overlaps any obstacle.
 */
export function freeRect(box: Rect, obstacles: Rect[]): Rect {
  let free = { ...box };
  const area = (r: Rect) => Math.max(0, r.right - r.left) * Math.max(0, r.bottom - r.top);
  // Biggest panels first, so a small one is never cut in a way a big one then undoes.
  for (const o of [...obstacles].sort((a, b) => area(b) - area(a))) {
    if (o.right <= free.left || o.left >= free.right || o.bottom <= free.top || o.top >= free.bottom) continue;
    const cuts: Rect[] = [
      { ...free, left: Math.max(free.left, o.right) },
      { ...free, right: Math.min(free.right, o.left) },
      { ...free, top: Math.max(free.top, o.bottom) },
      { ...free, bottom: Math.min(free.bottom, o.top) },
    ];
    free = cuts.reduce((best, c) => (area(c) > area(best) ? c : best));
  }
  return free;
}

/** Shrinks a rectangle by `pad` pixels on every side. */
export function inset(r: Rect, pad: number): Rect {
  return { left: r.left + pad, top: r.top + pad, right: r.right - pad, bottom: r.bottom - pad };
}

export function inRect(r: Rect, x: number, y: number): boolean {
  return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
}

/**
 * Where the line from `from` (inside r) towards `to` leaves r, or `to` itself when it is
 * inside. Null when `from` is outside r. Used to slide a compass tag back along its spoke.
 */
export function pullInside(r: Rect, from: [number, number], to: [number, number]): [number, number] | null {
  if (!inRect(r, from[0], from[1])) return null;
  if (inRect(r, to[0], to[1])) return to;
  const dx = to[0] - from[0];
  const dy = to[1] - from[1];
  let t = 1;
  if (dx > 0) t = Math.min(t, (r.right - from[0]) / dx);
  if (dx < 0) t = Math.min(t, (r.left - from[0]) / dx);
  if (dy > 0) t = Math.min(t, (r.bottom - from[1]) / dy);
  if (dy < 0) t = Math.min(t, (r.top - from[1]) / dy);
  return [from[0] + dx * t, from[1] + dy * t];
}

import { useEffect, useRef, useState } from "react";
import maplibregl, { Map as MlMap } from "maplibre-gl";
import { Theme, currentTheme, onTheme } from "../themes";

/** One clean base map, recoloured to the current theme so every look has its own map. */
const BASE_STYLE = "https://tiles.openfreemap.org/styles/positron";

type Layer = { id: string; type: string; source?: string; paint?: Record<string, unknown> };

/** The paint changes that give the base map a theme's colours. Our own data layers are skipped. */
function tintRules(layer: Layer, t: Theme): [string, unknown][] {
  const c = t.map;
  const id = layer.id;
  if (layer.source && layer.source !== "openmaptiles") return [];
  if (layer.type === "background") return [["background-color", c.land]];
  if (layer.type === "fill") {
    const color = /water/.test(id) ? c.water
      : /park|wood|grass|glacier|ice/.test(id) ? c.park
      : /building/.test(id) ? c.building
      : /aeroway|pier/.test(id) ? c.road
      : c.land;
    return [["fill-color", color], ["fill-outline-color", "rgba(0,0,0,0)"]];
  }
  if (layer.type === "line") {
    const color = /waterway/.test(id) ? c.water
      : /boundary/.test(id) ? c.boundary
      : /motorway|major|trunk|primary/.test(id) ? c.roadMajor
      : c.road;
    return [["line-color", color]];
  }
  if (layer.type === "symbol") return [["text-color", c.label], ["text-halo-color", c.halo], ["icon-opacity", 0.5]];
  return [];
}

/** Recolour a running map to a theme. */
export function tintMap(m: MlMap, t: Theme) {
  for (const layer of (m.getStyle().layers || []) as Layer[]) {
    for (const [prop, value] of tintRules(layer, t)) {
      try {
        m.setPaintProperty(layer.id, prop, value);
      } catch {
        /* layer has no such property */
      }
    }
  }
}

let baseStyle: Promise<any> | null = null;

/** Fetch the base style once and recolour it before the map draws, so there's no flash. */
async function themedStyle(t: Theme): Promise<any> {
  baseStyle ??= fetch(BASE_STYLE).then((r) => {
    if (!r.ok) throw new Error("style");
    return r.json();
  });
  const style = structuredClone(await baseStyle);
  for (const layer of style.layers as Layer[]) {
    for (const [prop, value] of tintRules(layer, t)) {
      if (prop === "icon-opacity" && layer.type !== "symbol") continue;
      layer.paint = { ...(layer.paint || {}), [prop]: value };
    }
  }
  return style;
}

/** A MapLibre map that calls onReady once its style has loaded, and onTheme when the look changes. */
export default function MapView({
  onReady,
  onRetheme,
  center = [-98, 39],
  zoom = 3,
}: {
  onReady: (m: MlMap) => void;
  onRetheme?: (m: MlMap) => void;
  center?: [number, number];
  zoom?: number;
}) {
  const el = useRef<HTMLDivElement>(null);
  const ready = useRef(onReady);
  ready.current = onReady;
  const retheme = useRef(onRetheme);
  retheme.current = onRetheme;
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!el.current) return;
    let m: MlMap | null = null;
    let gone = false;
    const off = onTheme((t) => {
      if (!m || !m.isStyleLoaded()) return;
      tintMap(m, t);
      retheme.current?.(m);
    });
    themedStyle(currentTheme())
      .catch(() => {
        setFailed(true);
        return BASE_STYLE;
      })
      .then((style) => {
        if (gone || !el.current) return;
        m = new maplibregl.Map({
          container: el.current,
          style,
          center,
          zoom,
          attributionControl: { compact: true },
        });
        m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
        m.on("style.load", () => {
          tintMap(m!, currentTheme());
          ready.current(m!);
        });
      });
    return () => {
      gone = true;
      off();
      m?.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <div ref={el} className="map" role="region" aria-label="Map" />
      {failed && (
        <div className="overlay" style={{ left: 12, bottom: 12 }}>
          Map tiles couldn't load. The station needs internet access for the base map.
        </div>
      )}
    </>
  );
}

/** Whether [lon, lat] is a real place. MapLibre throws on anything else and takes the page down. */
export function onGlobe(p: [number, number]): boolean {
  const [lon, lat] = p;
  return Number.isFinite(lon) && Number.isFinite(lat) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
}

export function fitTo(m: MlMap, all: [number, number][], padding: number | maplibregl.PaddingOptions = 60) {
  const pts = all.filter(onGlobe);
  if (!pts.length) return;
  const b = new maplibregl.LngLatBounds(pts[0], pts[0]);
  pts.forEach((p) => b.extend(p));
  const w = m.getContainer().clientWidth;
  const pad = typeof padding === "number" || w < 760 ? 40 : padding;
  m.fitBounds(b, { padding: pad, maxZoom: 13, duration: 0 });
}

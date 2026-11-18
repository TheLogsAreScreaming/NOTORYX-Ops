import { useEffect, useRef, useState } from "react";
import maplibregl, { Map as MlMap } from "maplibre-gl";

const STYLES = {
  light: "https://tiles.openfreemap.org/styles/positron",
  dark: "https://tiles.openfreemap.org/styles/dark",
  streets: "https://tiles.openfreemap.org/styles/liberty",
};
export type MapStyle = keyof typeof STYLES;

function prefersDark() {
  const t = document.documentElement.dataset.theme;
  if (t) return t === "dark";
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? true;
}

/** A MapLibre map that calls onReady once its style has loaded (and again after a style change). */
export default function MapView({
  onReady,
  style,
  center = [-98, 39],
  zoom = 3,
}: {
  onReady: (m: MlMap) => void;
  style?: MapStyle;
  center?: [number, number];
  zoom?: number;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const ready = useRef(onReady);
  ready.current = onReady;
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!el.current) return;
    const m = new maplibregl.Map({
      container: el.current,
      style: STYLES[style || (prefersDark() ? "dark" : "light")],
      center,
      zoom,
      attributionControl: { compact: true },
    });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    m.on("style.load", () => ready.current(m));
    m.on("error", (e) => {
      if (String(e?.error?.message || "").includes("style")) setFailed(true);
    });
    map.current = m;
    return () => m.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (map.current && style) map.current.setStyle(STYLES[style]);
  }, [style]);

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

export const TYPE_COLOR: Record<string, string> = {
  repeater: "#3fb8a0",
  room: "#9d8cf2",
  companion: "#5b9cf0",
  sensor: "#e3a33b",
  unknown: "#8a929c",
};

export function snrColor(snr: number | null | undefined): string {
  if (snr == null) return "#8a929c";
  if (snr >= 5) return "#2fb36b";
  if (snr >= 0) return "#9ccc4a";
  if (snr >= -5) return "#e3a33b";
  if (snr >= -10) return "#e8743b";
  return "#ef5d5d";
}

export function fitTo(m: MlMap, pts: [number, number][], padding: number | maplibregl.PaddingOptions = 60) {
  if (!pts.length) return;
  const b = new maplibregl.LngLatBounds(pts[0], pts[0]);
  pts.forEach((p) => b.extend(p));
  const w = m.getContainer().clientWidth;
  const pad = typeof padding === "number" || w < 760 ? 40 : padding;
  m.fitBounds(b, { padding: pad, maxZoom: 13, duration: 0 });
}

import { useEffect, useRef } from "react";
import uPlot from "uplot";

export type Series = { label: string; color: string; values: (number | null)[]; scale?: string };

/** A small time-series chart. x is in unix seconds. */
export default function Chart({ x, series, height = 180 }: { x: number[]; series: Series[]; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current || !x.length) return;
    const css = getComputedStyle(document.documentElement);
    const axisColor = css.getPropertyValue("--text-3").trim() || "#888";
    const gridColor = css.getPropertyValue("--line").trim() || "#333";
    const scales: Record<string, uPlot.Scale> = { x: { time: true } };
    const axes: uPlot.Axis[] = [{ stroke: axisColor, grid: { stroke: gridColor, width: 1 } }];
    const used = new Set<string>();
    series.forEach((s, i) => {
      const sc = s.scale || "y";
      if (used.has(sc)) return;
      used.add(sc);
      scales[sc] = {};
      axes.push({ scale: sc, stroke: s.color, side: i === 0 ? 3 : 1, grid: { show: i === 0, stroke: gridColor, width: 1 }, size: 50 });
    });
    const opts: uPlot.Options = {
      width: el.current.clientWidth || 400,
      height,
      scales,
      axes,
      legend: { show: false },
      cursor: { drag: { x: false, y: false } },
      series: [{}, ...series.map((s) => ({ label: s.label, stroke: s.color, width: 2, scale: s.scale || "y", spanGaps: true }))],
    };
    const plot = new uPlot(opts, [x, ...series.map((s) => s.values)] as uPlot.AlignedData, el.current);
    const ro = new ResizeObserver(() => plot.setSize({ width: el.current!.clientWidth, height }));
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      plot.destroy();
    };
  }, [x, series, height]);
  if (!x.length) return <div className="empty">No history yet.</div>;
  return (
    <div>
      <div className="row small" style={{ marginBottom: 4 }}>
        {series.map((s) => (
          <span key={s.label}>
            <span className="dot" style={{ background: s.color }} />
            {s.label}
          </span>
        ))}
      </div>
      <div ref={el} />
    </div>
  );
}

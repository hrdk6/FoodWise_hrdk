import { useId, useMemo } from "react";

import { fmtNum } from "@/lib/format";

type Props = {
  levels: number[];
  quantiles: number[];
  recommended: number;
  override?: number | null;
  lastWeek?: number | null;
  actual?: number | null;
  soldOut?: boolean;
  unit: string;
  height?: number;
  delay?: number;
  animate?: boolean;
  compact?: boolean;
};

const W = 320;

/** Catmull-Rom spline through points, as an SVG path (smooth but passes through every point). */
function smooth(points: [number, number][]) {
  if (points.length < 2) return "";
  let d = `M${points[0][0].toFixed(1)},${points[0][1].toFixed(1)}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] ?? points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] ?? p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}

/**
 * The forecast as a shape. Height at each quantity is the probability density implied by the
 * quantile grid, so a confident forecast is tall and narrow and an uncertain one is low and wide.
 * The darker band is the likely range (P10 to P90); the haldi line is what to cook.
 */
export function DemandShape({
  levels,
  quantiles,
  recommended,
  override,
  lastWeek,
  actual,
  soldOut,
  unit,
  height = 46,
  delay = 0,
  animate = true,
  compact = false,
}: Props) {
  const clipId = useId();
  const geo = useMemo(() => {
    const q = [...quantiles].sort((a, b) => a - b);
    const lo = Math.min(q[0], recommended, override ?? Infinity, actual ?? Infinity, lastWeek ?? Infinity);
    const hi = Math.max(q[q.length - 1], recommended, override ?? -Infinity, actual ?? -Infinity, lastWeek ?? -Infinity);
    const span = Math.max(hi - lo, Math.max(1, hi * 0.05));
    const min = Math.max(0, lo - span * 0.22);
    const max = hi + span * 0.22;
    const x = (v: number) => ((v - min) / (max - min)) * W;

    // Density between neighbouring quantiles, placed at their midpoint.
    const pts: [number, number][] = [];
    for (let i = 0; i < q.length - 1; i++) {
      const width = Math.max(q[i + 1] - q[i], span * 0.01);
      pts.push([(q[i] + q[i + 1]) / 2, (levels[i + 1] - levels[i]) / width]);
    }
    // Eleven empirical quantiles give a lumpy density; two light smoothing passes remove
    // artefacts (false second humps) without moving the mass.
    for (let pass = 0; pass < 2; pass++) {
      const d = pts.map((p) => p[1]);
      for (let i = 0; i < pts.length; i++) {
        pts[i][1] = 0.25 * (d[i - 1] ?? d[i]) + 0.5 * d[i] + 0.25 * (d[i + 1] ?? d[i]);
      }
    }
    const peak = Math.max(...pts.map((p) => p[1]));
    const top = 6;
    const base = height - 8;
    const y = (dens: number) => base - (dens / peak) * (base - top);
    const tailL = q[0] - (q[1] - q[0]) * 1.4;
    const tailR = q[q.length - 1] + (q[q.length - 1] - q[q.length - 2]) * 1.4;
    const line: [number, number][] = [
      [x(Math.max(min, tailL)), base],
      [x(q[0]), y(pts[0][1] * 0.45)],
      ...pts.map(([v, d]) => [x(v), y(d)] as [number, number]),
      [x(q[q.length - 1]), y(pts[pts.length - 1][1] * 0.45)],
      [x(Math.min(max, tailR)), base],
    ];
    const curve = smooth(line);
    const area = `${curve} L${line[line.length - 1][0].toFixed(1)},${base} L${line[0][0].toFixed(1)},${base} Z`;
    const iP10 = levels.indexOf(0.1);
    const iP90 = levels.indexOf(0.9);
    return { x, area, curve, base, p10: q[iP10 >= 0 ? iP10 : 1], p90: q[iP90 >= 0 ? iP90 : q.length - 2], min, max };
  }, [levels, quantiles, recommended, override, actual, lastWeek, height]);

  const plan = override ?? recommended;
  const label = `Likely ${fmtNum(geo.p10)} to ${fmtNum(geo.p90)} ${unit}; cook ${fmtNum(plan, 1)}${
    actual != null ? `; actual ${fmtNum(actual, 1)}${soldOut ? ", ran out" : ""}` : ""
  }`;

  return (
    <svg viewBox={`0 0 ${W} ${height}`} preserveAspectRatio="none" className="block h-full w-full overflow-visible" role="img" aria-label={label}>
      <defs>
        <clipPath id={clipId}>
          <rect x={geo.x(geo.p10)} y={0} width={Math.max(1, geo.x(geo.p90) - geo.x(geo.p10))} height={height} />
        </clipPath>
      </defs>
      <line x1={0} x2={W} y1={geo.base + 0.5} y2={geo.base + 0.5} stroke="var(--color-rim)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
      <g className={animate ? "animate-pour" : undefined} style={{ animationDelay: `${delay}ms` }}>
        <path d={geo.area} fill="var(--color-ink)" opacity={0.09} />
        <path d={geo.area} fill="var(--color-ink)" opacity={0.2} clipPath={`url(#${clipId})`} />
        <path d={geo.curve} fill="none" stroke="var(--color-ink-2)" strokeWidth={1.25} opacity={0.75} vectorEffect="non-scaling-stroke" />
      </g>
      {lastWeek != null && !compact ? (
        <g aria-hidden>
          <line x1={geo.x(lastWeek)} x2={geo.x(lastWeek)} y1={geo.base - 7} y2={geo.base + 4} stroke="var(--color-muted)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
        </g>
      ) : null}
      {override != null ? (
        <line x1={geo.x(recommended)} x2={geo.x(recommended)} y1={2} y2={geo.base} stroke="var(--color-haldi)" strokeWidth={1.5} strokeDasharray="3 3" opacity={0.8} vectorEffect="non-scaling-stroke" />
      ) : null}
      <g className={animate ? "animate-ladle" : undefined} style={{ animationDelay: `${delay + 380}ms` }} aria-hidden>
        <line x1={geo.x(plan)} x2={geo.x(plan)} y1={1} y2={geo.base + 3} stroke={override != null ? "var(--color-ink)" : "var(--color-haldi)"} strokeWidth={3} strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      </g>
      {actual != null ? (
        <g aria-hidden>
          <line x1={geo.x(actual)} x2={geo.x(actual)} y1={geo.base - 12} y2={geo.base + 5} stroke={soldOut ? "var(--color-chilli)" : "var(--color-leaf)"} strokeWidth={2.5} vectorEffect="non-scaling-stroke" />
        </g>
      ) : null}
    </svg>
  );
}

export function ShapeLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm text-muted">
      <span className="inline-flex items-center gap-2">
        <span className="inline-block h-3 w-7 rounded-sm bg-ink/20" /> Likely range (P10 to P90)
      </span>
      <span className="inline-flex items-center gap-2">
        <span className="inline-block h-4 w-[3px] rounded bg-haldi" /> Cook this much
      </span>
      <span className="inline-flex items-center gap-2">
        <span className="inline-block h-3 w-[2px] bg-muted" /> Same day last week
      </span>
      <span className="inline-flex items-center gap-2">
        <span className="inline-block h-3 w-[3px] bg-leaf" /> Served (on past days)
      </span>
    </div>
  );
}

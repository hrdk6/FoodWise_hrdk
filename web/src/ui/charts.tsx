import { Table2 } from "lucide-react";
import { type ReactNode, useState } from "react";

import { cx } from "./primitives";

/**
 * Chart parameters (validated with the dataviz palette checker against the plate surface):
 * blue and turmeric pass lightness, chroma, CVD and normal-vision separation. Turmeric is
 * under 3:1 against the surface, so every chart carries a legend, tooltips and a table view.
 */
export const C = {
  blue: "#3E6DB0",
  turmeric: "#C8880E",
  violet: "#7A5BA8",
  muted: "#A3B0AC",
  grid: "#E2E7E4",
  axis: "#5A6865",
  surface: "#F9FBFA",
  good: "#2F6E4A",
  bad: "#B83A2B",
};

export const axisProps = {
  stroke: C.grid,
  tick: { fill: C.axis, fontSize: 12, fontFamily: "inherit" },
  tickLine: false,
  axisLine: { stroke: C.grid },
} as const;

export function LegendItem({ color, label, kind = "line" }: { color: string; label: ReactNode; kind?: "line" | "bar" | "band" | "dot" }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-2">
      {kind === "line" ? <span className="inline-block h-[2px] w-5 rounded" style={{ background: color }} /> : null}
      {kind === "bar" ? <span className="inline-block size-3 rounded-[3px]" style={{ background: color }} /> : null}
      {kind === "band" ? <span className="inline-block h-3 w-5 rounded-[3px]" style={{ background: color, opacity: 0.18 }} /> : null}
      {kind === "dot" ? <span className="inline-block size-2.5 rounded-full" style={{ background: color }} /> : null}
      {label}
    </span>
  );
}

type TooltipRow = { color?: string; label: string; value: ReactNode };

export function TooltipBox({ title, rows }: { title: ReactNode; rows: TooltipRow[] }) {
  return (
    <div className="min-w-44 rounded-[8px] border border-rim bg-plate px-3 py-2 text-sm shadow-[0_6px_20px_rgb(28_42_74/0.14)]">
      <p className="mb-1 font-medium text-ink">{title}</p>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between gap-4 py-0.5">
          <span className="inline-flex items-center gap-1.5 text-muted">
            {r.color ? <span className="inline-block size-2 rounded-full" style={{ background: r.color }} /> : null}
            {r.label}
          </span>
          <span className="num font-medium text-ink">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

/** A chart with a "show as table" toggle, so no reading depends on colour or hover. */
export function ChartFrame({
  title,
  sub,
  legend,
  table,
  children,
  height = 260,
  className,
}: {
  title: ReactNode;
  sub?: ReactNode;
  legend?: ReactNode;
  table: { columns: string[]; rows: (string | number)[][] };
  children: ReactNode;
  height?: number;
  className?: string;
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    <figure className={cx("rim px-4 pb-4 pt-4 sm:px-5", className)}>
      <figcaption className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-[640] text-ink">{title}</p>
          {sub ? <p className="mt-0.5 text-sm text-muted">{sub}</p> : null}
        </div>
        <button
          onClick={() => setAsTable((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-muted hover:bg-steel-2 hover:text-ink"
          aria-pressed={asTable}
        >
          <Table2 className="size-3.5" aria-hidden /> {asTable ? "Show chart" : "Show table"}
        </button>
      </figcaption>
      {legend && !asTable ? <div className="mb-3 flex flex-wrap gap-x-5 gap-y-1.5">{legend}</div> : null}
      {asTable ? (
        <div className="max-h-[320px] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-plate text-left text-muted">
              <tr>
                {table.columns.map((c) => (
                  <th key={c} className="border-b border-rim px-2 py-1.5 font-medium">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((r, i) => (
                <tr key={i} className="border-b border-steel-2">
                  {r.map((v, j) => (
                    <td key={j} className={cx("num px-2 py-1.5", j > 0 && "text-right")}>
                      {v}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div style={{ height }} className="w-full">
          {children}
        </div>
      )}
    </figure>
  );
}

import { AlertTriangle, CheckCircle2, CircleDashed, Info, OctagonAlert } from "lucide-react";
import type { ReactNode } from "react";

import { cx } from "@/ui/primitives";

export type Level = "good" | "warning" | "critical" | "info" | "none";

const LEVEL: Record<Level, { icon: ReactNode; cls: string }> = {
  good: { icon: <CheckCircle2 className="size-4" aria-hidden />, cls: "bg-leaf-soft text-leaf" },
  warning: { icon: <AlertTriangle className="size-4" aria-hidden />, cls: "bg-haldi-soft text-haldi-deep" },
  critical: { icon: <OctagonAlert className="size-4" aria-hidden />, cls: "bg-chilli-soft text-chilli" },
  info: { icon: <Info className="size-4" aria-hidden />, cls: "bg-steel-2 text-ink-2" },
  none: { icon: <CircleDashed className="size-4" aria-hidden />, cls: "bg-steel-2 text-muted" },
};

/** Status always carries an icon and a word, never colour alone. */
export function Status({ level, children, className }: { level: Level; children: ReactNode; className?: string }) {
  const l = LEVEL[level];
  return (
    <span className={cx("inline-flex items-center gap-1.5 whitespace-nowrap rounded-[6px] px-2 py-0.5 text-sm font-medium", l.cls, className)}>
      {l.icon}
      {children}
    </span>
  );
}

export function Kpi({ label, value, sub, level }: { label: ReactNode; value: ReactNode; sub?: ReactNode; level?: Level }) {
  return (
    <div className="rim px-4 py-3">
      <p className="text-sm text-muted">{label}</p>
      <p className={cx("qty mt-1 text-[1.7rem] leading-none", level === "critical" ? "text-chilli" : level === "warning" ? "text-haldi-deep" : "text-ink")}>{value}</p>
      {sub ? <p className="mt-1.5 text-sm text-ink-2">{sub}</p> : null}
    </div>
  );
}

export function Panel({ title, sub, actions, children, className }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("mb-8", className)}>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-[650] text-ink">{title}</h2>
          {sub ? <p className="mt-0.5 text-sm text-muted">{sub}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function Table({ head, children, minWidth = 640 }: { head: { label: string; right?: boolean }[]; children: ReactNode; minWidth?: number }) {
  return (
    <div className="rim overflow-x-auto">
      <table className="w-full text-sm" style={{ minWidth }}>
        <thead>
          <tr className="border-b border-rim text-left text-muted">
            {head.map((h) => (
              <th key={h.label} className={cx("px-3 py-2 font-medium first:pl-4 last:pr-4", h.right && "text-right")}>
                {h.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export const td = "px-3 py-2 first:pl-4 last:pr-4 align-top";
export const tdNum = "num px-3 py-2 text-right first:pl-4 last:pr-4 align-top";

export function fmtMs(ms: number | null | undefined) {
  if (ms == null) return "–";
  if (ms >= 1000) return `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} s`;
  return `${Math.round(ms)} ms`;
}

export function fmtSecs(s: number | null | undefined) {
  if (s == null) return "–";
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 90) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

export function fmtAgo(iso: string | null | undefined) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function pct(v: number | null | undefined, digits = 1) {
  return v == null ? "–" : `${(v * 100).toFixed(digits)}%`;
}

export function timeLabel(iso: string) {
  return new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

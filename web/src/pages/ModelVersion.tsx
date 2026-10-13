import { ArrowLeft, Check, X } from "lucide-react";
import { useMemo } from "react";
import { Link, useParams } from "react-router";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { ApiError } from "@/api/client";
import { useModel, usePromote, useRollback } from "@/api/hooks";
import { useAuth } from "@/app/auth";
import { fmtDateTime, fmtDay, fmtKg, fmtNum, fmtPct } from "@/lib/format";
import { C, ChartFrame, LegendItem, TooltipBox, axisProps } from "@/ui/charts";
import { useToast } from "@/ui/overlays";
import { Badge, Button, ErrorState, Figure, KeyValue, Section, Skeleton } from "@/ui/primitives";

import { Leaderboard, STAGE } from "./Models";

type Point = { date: string; actual: number | null; p50: number | null; p10?: number | null; p90?: number | null; recommended?: number | null; prepared?: number | null };
type Trade = { level: number | null; label: string; waste_pct: number; short_rows_pct: number | null; short_units_pct: number | null };

const GATE_LABEL: Record<string, string> = {
  beats_baseline: "Beats the best baseline",
  beats_champion: "Beats the current champion",
  calibrated: "Ranges are calibrated",
  unbiased: "Not biased",
  cheaper: "Lower simulated cost of waste and shortage",
  enough_history: "Enough history for this kind of model",
};

function Backtest({ series }: { series: Point[] }) {
  const data = useMemo(
    () =>
      series
        .filter((p) => p.p10 != null)
        .map((p) => ({ ...p, day: fmtDay(p.date, { day: "numeric", month: "short" }), band: [p.p10, p.p90] as [number, number] })),
    [series],
  );
  if (!data.length) return null;
  return (
    <ChartFrame
      title="Backtest: what the model would have said, day by day"
      sub="Each day is forecast using only data from before it. Totals across all dishes, in kg."
      legend={
        <>
          <LegendItem color={C.blue} kind="band" label="Likely range (P10 to P90)" />
          <LegendItem color={C.blue} label="Actual demand" />
          <LegendItem color={C.turmeric} label="Would have cooked" />
          <LegendItem color={C.muted} label="Cooked by hand" />
        </>
      }
      table={{
        columns: ["Day", "Actual kg", "Forecast kg", "Would cook kg", "Cooked kg"],
        rows: data.map((d) => [d.day, fmtNum(d.actual), fmtNum(d.p50), fmtNum(d.recommended), fmtNum(d.prepared)]),
      }}
      height={300}
    >
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="day" {...axisProps} interval="preserveStartEnd" minTickGap={24} />
          <YAxis {...axisProps} width={48} tickFormatter={(v) => fmtNum(v)} />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const d = payload[0].payload as Point & { day: string };
              return (
                <TooltipBox
                  title={d.day}
                  rows={[
                    { label: "Actual", value: fmtKg(d.actual), color: C.blue },
                    { label: "Likely", value: `${fmtNum(d.p10)} to ${fmtNum(d.p90)} kg` },
                    { label: "Would cook", value: fmtKg(d.recommended), color: C.turmeric },
                    { label: "Cooked by hand", value: fmtKg(d.prepared), color: C.muted },
                  ]}
                />
              );
            }}
          />
          <Area dataKey="band" stroke="none" fill={C.blue} fillOpacity={0.12} isAnimationActive={false} />
          <Line dataKey="prepared" stroke={C.muted} strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="recommended" stroke={C.turmeric} strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="actual" stroke={C.blue} strokeWidth={2} dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

/** Waste against running short across service levels: the decision the cost setting makes. */
export function TradeOff({ points, current }: { points: Trade[]; current?: number | null }) {
  const levels = points.filter((p) => p.level != null);
  const logged = points.find((p) => p.level == null);
  if (!levels.length) return null;
  const data = levels.map((p) => ({ ...p, x: (p.short_rows_pct ?? 0) * 100, y: p.waste_pct * 100 }));
  return (
    <ChartFrame
      title="Choosing a service level"
      sub={`Cooking more cuts how often dishes run out but leaves more food. From this canteen's backtest.${logged ? ` By hand, ${fmtPct(logged.waste_pct)} was left over.` : ""}`}
      legend={
        <>
          <LegendItem color={C.blue} kind="dot" label="Service level (cook at percentile)" />
          {current != null ? <LegendItem color={C.turmeric} kind="dot" label="Current setting" /> : null}
        </>
      }
      table={{
        columns: ["Cook at", "Left over, % of demand", "Dishes that run out", "Shortfall, % of demand"],
        rows: [...(logged ? [["By hand", fmtPct(logged.waste_pct, 1), "–", "–"]] : []), ...levels.map((p) => [p.label, fmtPct(p.waste_pct, 1), fmtPct(p.short_rows_pct ?? 0), fmtPct(p.short_units_pct ?? 0, 1)])],
      }}
      height={260}
    >
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 16, right: 24, bottom: 12, left: -4 }}>
          <CartesianGrid stroke={C.grid} />
          <XAxis type="number" dataKey="x" {...axisProps} domain={[0, "dataMax + 5"]} tickFormatter={(v) => `${Math.round(v)}%`} label={{ value: "Dishes that run out", position: "insideBottom", offset: -6, fill: C.axis, fontSize: 12 }} />
          <YAxis type="number" dataKey="y" {...axisProps} width={48} domain={[0, "dataMax + 3"]} tickFormatter={(v) => `${Math.round(v)}%`} label={{ value: "Left over", angle: -90, position: "insideLeft", offset: 16, fill: C.axis, fontSize: 12 }} />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const p = payload[0].payload as Trade;
              return (
                <TooltipBox
                  title={`Cook at ${p.label}`}
                  rows={[
                    { label: "Left over", value: fmtPct(p.waste_pct, 1) },
                    { label: "Dishes that run out", value: fmtPct(p.short_rows_pct ?? 0) },
                    { label: "Shortfall", value: fmtPct(p.short_units_pct ?? 0, 1) },
                  ]}
                />
              );
            }}
          />
          <Line
            dataKey="y"
            stroke={C.blue}
            strokeWidth={2}
            isAnimationActive={false}
            dot={(props: { cx?: number; cy?: number; payload?: Trade; index?: number }) => {
              const isCurrent = current != null && props.payload?.level != null && Math.abs(props.payload.level - current) < 0.026;
              return (
                <g key={props.index}>
                  <circle cx={props.cx} cy={props.cy} r={isCurrent ? 7 : 4.5} fill={isCurrent ? C.turmeric : C.blue} stroke={C.surface} strokeWidth={2} />
                  <text x={(props.cx ?? 0) + 9} y={(props.cy ?? 0) - 8} fontSize={11} fill={C.axis}>
                    {props.payload?.label}
                  </text>
                </g>
              );
            }}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

export default function ModelVersion() {
  const { id } = useParams();
  const m = useModel(id);
  const { can } = useAuth();
  const promote = usePromote();
  const rollback = useRollback();
  const toast = useToast();
  if (m.isLoading) return <Skeleton className="h-96 w-full" />;
  if (m.error) return <ErrorState error={m.error} retry={() => m.refetch()} />;
  const v = m.data!;
  const metrics = v.metrics as Record<string, unknown>;
  const red = metrics.waste_reduction as { point?: number; lo?: number; hi?: number } | undefined;
  const ablation = v.feedback_ablation as { message?: string } | null;
  const window = v.data_window as { from?: string; to?: string; rows?: number; days?: number; folds?: { test_start: string; test_end: string }[]; trigger?: string };
  const series = ((v.backtest as { series?: Point[] }).series ?? []) as Point[];
  const act = async (fn: () => Promise<unknown>, msg: string) => {
    try {
      await fn();
      toast("ok", msg);
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "That didn't work.");
    }
  };
  return (
    <div>
      <Link to="/models" className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
        <ArrowLeft className="size-4" /> Forecast models
      </Link>
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <Badge tone={STAGE[v.stage]?.tone ?? "neutral"}>{STAGE[v.stage]?.label ?? v.stage}</Badge>
          <h1 className="mt-2 text-3xl font-[700]" style={{ fontStretch: "116%" }}>
            {v.label} v{v.version}
          </h1>
          <p className="mt-1 text-base text-muted">
            Trained {fmtDateTime(v.created_at)}
            {window.trigger ? ` (${window.trigger})` : ""}. Readiness tier {v.tier}.
          </p>
          {v.shadow && (v.shadow as { decision?: string }).decision ? <p className="mt-1 text-base text-ink-2">{(v.shadow as { decision?: string }).decision}</p> : null}
        </div>
        <div className="flex gap-2">
          {can("models:promote") && ["challenger", "retired", "rejected"].includes(v.stage) ? (
            <Button busy={promote.isPending} onClick={() => act(() => promote.mutateAsync(v.id), `v${v.version} is now the champion.`)}>
              Make champion now
            </Button>
          ) : null}
          {can("models:promote") && v.stage === "degraded" ? (
            <Button variant="danger" busy={rollback.isPending} onClick={() => act(() => rollback.mutateAsync(v.canteen_id), "Rolled back to the previous champion.")}>
              Roll back
            </Button>
          ) : null}
        </div>
      </header>

      <div className="mb-8 grid grid-cols-2 gap-6 md:grid-cols-5">
        <Figure value={fmtPct(metrics.wape as number, 1)} label="Backtest error (WAPE)" />
        <Figure value={metrics.skill != null ? fmtPct(metrics.skill as number, 0, true) : "–"} label={`Better than ${String(metrics.best_baseline ?? "baseline").replace("_", " ")}`} tone="leaf" />
        <Figure value={metrics.coverage80 != null ? fmtPct(metrics.coverage80 as number) : "–"} label="Actuals inside the 80% range" />
        <Figure value={fmtPct(metrics.bias as number, 1, true)} label="Bias" />
        <Figure value={red?.point != null ? fmtPct(red.point) : "–"} label={red?.lo != null ? `Less waste than by hand (${fmtPct(red.lo)} to ${fmtPct(red.hi)})` : "Less waste than by hand"} tone="leaf" />
      </div>

      <div className="grid gap-8 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-8">
          <Backtest series={series} />
          <TradeOff points={(metrics.tradeoff as Trade[]) ?? []} current={metrics.critical_ratio as number} />
        </div>
        <div className="min-w-0">
          <Section title="Promotion gates">
            <ul className="flex flex-col gap-2.5">
              {v.gates.map((g) => {
                const gate = g as { gate: string; passed: boolean; detail: string };
                return (
                  <li key={gate.gate} className="flex gap-2.5">
                    {gate.passed ? <Check className="mt-0.5 size-4.5 shrink-0 text-leaf" aria-label="Passed" /> : <X className="mt-0.5 size-4.5 shrink-0 text-chilli" aria-label="Failed" />}
                    <div>
                      <p className="text-base">{GATE_LABEL[gate.gate] ?? gate.gate}</p>
                      <p className="text-sm text-muted">{gate.detail}</p>
                    </div>
                  </li>
                );
              })}
              {!v.gates.length ? <p className="text-base text-muted">This version was not gated (it is a baseline or a rule-based plan).</p> : null}
            </ul>
          </Section>
          {ablation?.message ? (
            <Section title="Student feedback as a model input">
              <p className="text-base text-ink-2">{ablation.message}</p>
            </Section>
          ) : null}
          <Section title="Error by meal">
            <KeyValue items={Object.entries((metrics.by_meal as Record<string, number>) ?? {}).map(([k, val]) => [k.charAt(0).toUpperCase() + k.slice(1), fmtPct(val, 1)])} />
          </Section>
          <Section title="Reproducibility">
            <KeyValue
              items={[
                ["Training data", `${window.from} to ${window.to}, ${fmtNum(window.rows)} dish servings`],
                ["Test folds", (window.folds ?? []).map((f) => `${fmtDay(f.test_start, { day: "numeric", month: "short" })}–${fmtDay(f.test_end, { day: "numeric", month: "short" })}`).join(", ")],
                ["Features", `${v.feature_list.length}, hash ${v.feature_hash}`],
                ["Seed", String(v.seed)],
                ["Libraries", Object.entries(v.library_versions).map(([k, val]) => `${k} ${val}`).join(", ")],
              ]}
            />
          </Section>
        </div>
      </div>

      <Section title="Every model tried in this run" className="mt-8">
        <Leaderboard entries={v.leaderboard as Parameters<typeof Leaderboard>[0]["entries"]} />
      </Section>
    </div>
  );
}

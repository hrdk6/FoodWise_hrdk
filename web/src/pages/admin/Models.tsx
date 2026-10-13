import { Activity, RotateCcw } from "lucide-react";
import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ApiError } from "@/api/client";
import { useOpsAction, useOpsModels, useOpsVersion } from "@/api/hooks";
import { fmtDay, fmtNum } from "@/lib/format";
import { C, ChartFrame, LegendItem, TooltipBox, axisProps } from "@/ui/charts";
import { Dialog, useToast } from "@/ui/overlays";
import { Button, ErrorState, Segmented, Skeleton, cx } from "@/ui/primitives";

import { Kpi, type Level, Panel, Status, Table, fmtAgo, fmtSecs, pct, td, tdNum } from "./parts";

const PALETTE = [C.blue, C.turmeric, C.violet];

type Row = {
  tenant: string;
  canteen: string;
  canteen_id: string;
  tier: number;
  tier_name: string;
  days: number;
  champion: null | {
    id: string;
    version: number;
    algorithm: string;
    stage: string;
    trained_at: string;
    age_days: number;
    wape: number | null;
    skill: number | null;
    coverage80: number | null;
    bias: number | null;
    critical_ratio: number | null;
    training_seconds: number;
    rows: number | null;
    best_baseline: string | null;
  };
  monitoring: {
    status: string;
    live_wape: number | null;
    live_bias: number | null;
    live_coverage80: number | null;
    shortage_observed: number | null;
    shortage_target: number | null;
    psi_footfall: number | null;
    alerts: string[];
    checked_at: string | null;
  };
  challenger: null | { id: string; version: number; algorithm: string; wape: number | null; days_observed: number; days_needed: number };
  versions: number;
  last_training: null | { status: string; at: string; error: string | null; trigger: string | null };
  forecasts_today: number;
  forecasts_tomorrow: number;
};

function health(r: Row): { level: Level; label: string } {
  const s = r.monitoring.status;
  if (!r.champion) return { level: "none", label: r.tier === 0 ? "Rule-based (new)" : "No model" };
  if (r.champion.algorithm === "rule_prior") return { level: "info", label: "Rule-based plan" };
  if (s === "degraded") return { level: "critical", label: "Degraded" };
  if (s === "drift") return { level: "warning", label: "Input drift" };
  if (r.monitoring.alerts.length) return { level: "warning", label: "Alert" };
  if (s === "ok") return { level: "good", label: "Healthy" };
  return { level: "info", label: "Not checked" };
}

function within(v: number | null, target: number, tol: number) {
  return v == null ? "none" : Math.abs(v - target) <= tol ? "good" : "warning";
}

function VersionDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const v = useOpsVersion(id);
  return (
    <Dialog open onClose={onClose} title={v.data ? `${v.data.canteen}: v${v.data.version} ${v.data.algorithm}` : "Model version"} wide>
      {v.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : v.error ? (
        <ErrorState error={v.error} />
      ) : (
        (() => {
          const d = v.data!;
          const m = d.metrics as Record<string, number | null>;
          const lb = d.leaderboard as { label?: string; algorithm: string; status: string; error?: string | null; metrics: Record<string, number | null>; fit_seconds?: number }[];
          const gates = d.gates as { gate: string; detail: string; passed: boolean }[];
          return (
            <div className="flex flex-col gap-6">
              <p className="text-base text-ink-2">
                {d.tenant}. Trained {fmtAgo(d.created_at)} in {fmtSecs(d.training_seconds)} on {fmtNum((d.data_window as { rows?: number }).rows)} servings
                {(d.data_window as { from?: string }).from ? ` from ${fmtDay((d.data_window as { from: string }).from)} to ${fmtDay((d.data_window as { to: string }).to)}` : ""}. {d.feature_count} features. Stage: {d.stage}.
              </p>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Kpi label="Backtest error (WAPE)" value={pct(m.wape)} sub={`hold-out ${pct(m.holdout_wape)}`} />
                <Kpi label="Better than baseline" value={pct(m.skill)} />
                <Kpi label="80% range covered" value={pct(m.coverage80, 0)} level={within(m.coverage80, 0.8, 0.05) === "warning" ? "warning" : undefined} />
                <Kpi label="Bias" value={pct(m.bias)} level={m.bias != null && Math.abs(m.bias) > 0.05 ? "warning" : undefined} />
              </div>
              <div>
                <p className="mb-2 font-[640]">Promotion gates</p>
                <ul className="rim divide-y divide-rim">
                  {gates.map((g) => (
                    <li key={g.gate} className="flex flex-wrap items-center gap-3 px-4 py-2">
                      <Status level={g.passed ? "good" : "critical"}>{g.passed ? "Passed" : "Failed"}</Status>
                      <span className="font-medium">{g.gate.replace(/_/g, " ")}</span>
                      <span className="text-sm text-muted">{g.detail}</span>
                    </li>
                  ))}
                  {!gates.length ? <li className="px-4 py-2 text-sm text-muted">Not gated (baseline or rule-based plan).</li> : null}
                </ul>
              </div>
              <div>
                <p className="mb-2 font-[640]">Leaderboard (walk-forward backtest)</p>
                <Table head={[{ label: "Model" }, { label: "WAPE", right: true }, { label: "Coverage", right: true }, { label: "Bias", right: true }, { label: "Fit", right: true }, { label: "Result" }]} minWidth={560}>
                  {lb.map((e) => (
                    <tr key={e.algorithm} className="border-b border-steel-2 last:border-0">
                      <td className={td}>{e.label ?? e.algorithm}</td>
                      <td className={tdNum}>{pct(e.metrics?.wape)}</td>
                      <td className={tdNum}>{pct(e.metrics?.coverage80, 0)}</td>
                      <td className={tdNum}>{pct(e.metrics?.bias)}</td>
                      <td className={tdNum}>{fmtSecs(e.fit_seconds ?? null)}</td>
                      <td className={td}>{e.error ? <span className="text-chilli">{e.error}</span> : e.status}</td>
                    </tr>
                  ))}
                </Table>
              </div>
              <p className="text-xs text-muted">
                {Object.entries(d.library_versions)
                  .map(([k, val]) => `${k} ${val}`)
                  .join(", ")}
              </p>
            </div>
          );
        })()
      )}
    </Dialog>
  );
}

export default function Models() {
  const [days, setDays] = useState<"14" | "30" | "60">("30");
  const q = useOpsModels(Number(days));
  const act = useOpsAction();
  const toast = useToast();
  const [open, setOpen] = useState<string | null>(null);
  const [picked, setPicked] = useState<string[] | null>(null);
  const rows = (q.data?.rows ?? []) as Row[];
  const canteens = useMemo(() => q.data?.canteens ?? [], [q.data]);
  const selected = picked ?? canteens.slice(0, 3).map((c) => c.id);
  const colorOf = (id: string) => PALETTE[canteens.findIndex((c) => c.id === id) % PALETTE.length];
  const series: Record<string, number | string | null>[] = (q.data?.series ?? []).map((p) => ({ ...p, label: fmtDay(p.date as string, { day: "numeric", month: "short" }) }));

  if (q.isLoading) return <Skeleton className="h-[480px] w-full" />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;

  const run = async (kind: "retrain" | "monitor", canteenId: string, name: string) => {
    try {
      await act.mutateAsync({ kind, canteenId });
      toast("ok", kind === "retrain" ? `Retraining queued for ${name}. The organisation sees this in its audit trail.` : `Health check queued for ${name}.`);
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not queue that.");
    }
  };
  const toggle = (id: string) => {
    const cur = selected;
    if (cur.includes(id)) setPicked(cur.filter((x) => x !== id));
    else setPicked([...cur, id].slice(-3));
  };
  const healthy = rows.filter((r) => health(r).level === "good").length;

  return (
    <div>
      <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Canteens with a trained model" value={`${rows.filter((r) => r.champion).length} / ${rows.length}`} />
        <Kpi label="Healthy" value={fmtNum(healthy)} sub={`${rows.length - healthy} need a look`} level={rows.length - healthy ? "warning" : undefined} />
        <Kpi label="Challengers in shadow" value={fmtNum(rows.filter((r) => r.challenger).length)} />
        <Kpi label="No plan for tomorrow" value={fmtNum(rows.filter((r) => r.forecasts_tomorrow === 0).length)} level={rows.some((r) => r.forecasts_tomorrow === 0) ? "warning" : undefined} />
      </div>

      <Panel
        title="Live forecast error by day"
        sub="WAPE of the median forecast against servings. Dishes that sold out are left out, because their real demand is unknown."
        actions={<Segmented label="Days" size="sm" value={days} onChange={setDays} options={[{ value: "14", label: "14 days" }, { value: "30", label: "30 days" }, { value: "60", label: "60 days" }]} />}
      >
        {canteens.length > 3 ? (
          <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Canteens to compare (up to three)">
            {canteens.map((c) => (
              <button key={c.id} aria-pressed={selected.includes(c.id)} onClick={() => toggle(c.id)} className={cx("rounded-full border px-3 py-1 text-sm", selected.includes(c.id) ? "border-ink bg-ink text-steel" : "border-rim bg-plate text-ink-2")}>
                {c.name}
              </button>
            ))}
          </div>
        ) : null}
        {series.length ? (
          <ChartFrame
            title="Error per day"
            sub="Lower is better"
            legend={selected.map((id) => {
              const c = canteens.find((x) => x.id === id)!;
              return <LegendItem key={id} color={colorOf(id)} label={`${c.name}, ${c.tenant}`} />;
            })}
            table={{
              columns: ["Day", ...selected.map((id) => canteens.find((c) => c.id === id)?.name ?? id)],
              rows: series.map((p) => [String(p.label), ...selected.map((id) => (p[id] != null ? pct(p[id] as number) : "–"))]),
            }}
            height={260}
          >
            <ResponsiveContainer>
              <LineChart data={series} margin={{ top: 6, right: 12, bottom: 0, left: -12 }}>
                <CartesianGrid stroke={C.grid} vertical={false} />
                <XAxis dataKey="label" {...axisProps} minTickGap={28} />
                <YAxis {...axisProps} width={48} tickFormatter={(v) => `${Math.round(v * 100)}%`} domain={[0, "auto"]} />
                <ReferenceLine y={0.1} stroke={C.muted} strokeDasharray="4 4" label={{ value: "10%", position: "insideTopRight", fill: C.axis, fontSize: 11 }} />
                <Tooltip
                  cursor={{ stroke: C.axis, strokeDasharray: "3 3" }}
                  content={({ active, payload, label }) =>
                    active && payload?.length ? (
                      <TooltipBox
                        title={label}
                        rows={selected.map((id) => ({
                          color: colorOf(id),
                          label: canteens.find((c) => c.id === id)?.name ?? id,
                          value: payload[0].payload[id] != null ? pct(payload[0].payload[id]) : "–",
                        }))}
                      />
                    ) : null
                  }
                />
                {selected.map((id) => (
                  <Line key={id} type="monotone" dataKey={id} stroke={colorOf(id)} strokeWidth={2} dot={false} connectNulls activeDot={{ r: 4, strokeWidth: 2, stroke: C.surface }} isAnimationActive={false} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </ChartFrame>
        ) : (
          <p className="rim px-4 py-3 text-base text-muted">No forecasts matched to logged servings in this period yet.</p>
        )}
      </Panel>

      <Panel title="Every canteen" sub="Champion model, its live health, and the challenger waiting to replace it.">
        <Table
          head={[
            { label: "Canteen" },
            { label: "Health" },
            { label: "Champion" },
            { label: "Backtest", right: true },
            { label: "Live (28 d)", right: true },
            { label: "80% range", right: true },
            { label: "Bias", right: true },
            { label: "Ran short", right: true },
            { label: "Drift", right: true },
            { label: "Challenger" },
            { label: "" },
          ]}
          minWidth={1180}
        >
          {rows.map((r) => {
            const h = health(r);
            const m = r.monitoring;
            return (
              <tr key={r.canteen_id} className="border-b border-steel-2 align-top last:border-0">
                <td className={td}>
                  <p className="font-medium">{r.canteen}</p>
                  <p className="text-xs text-muted">
                    {r.tenant}. {r.tier_name}, {fmtNum(r.days)} days logged
                  </p>
                </td>
                <td className={td}>
                  <Status level={h.level}>{h.label}</Status>
                  {m.alerts.length ? (
                    <p className="mt-1 max-w-[240px] text-xs text-ink-2" title={m.alerts.join(" ")}>
                      {m.alerts[0]}
                    </p>
                  ) : null}
                </td>
                <td className={td}>
                  {r.champion ? (
                    <button onClick={() => setOpen(r.champion!.id)} className="text-left underline decoration-rim-strong underline-offset-4 hover:decoration-ink">
                      v{r.champion.version} {r.champion.algorithm.replace(/_/g, " ")}
                    </button>
                  ) : (
                    <span className="text-muted">none</span>
                  )}
                  {r.champion ? <p className="text-xs text-muted">trained {r.champion.age_days} d ago</p> : null}
                </td>
                <td className={tdNum}>
                  {pct(r.champion?.wape)}
                  {r.champion?.skill != null ? <p className="text-xs text-muted">+{pct(r.champion.skill, 0)} vs baseline</p> : null}
                </td>
                <td className={cx(tdNum, m.live_wape != null && r.champion?.wape != null && m.live_wape > r.champion.wape * 1.5 && "text-chilli")}>{pct(m.live_wape)}</td>
                <td className={tdNum}>{pct(m.live_coverage80, 0)}</td>
                <td className={cx(tdNum, m.live_bias != null && Math.abs(m.live_bias) > 0.08 && "text-haldi-deep")}>{pct(m.live_bias)}</td>
                <td className={cx(tdNum, m.shortage_observed != null && m.shortage_target != null && m.shortage_observed > m.shortage_target + 0.05 && "text-chilli")}>
                  {pct(m.shortage_observed, 0)}
                  {m.shortage_target != null ? <p className="text-xs text-muted">target {pct(m.shortage_target, 0)}</p> : null}
                </td>
                <td className={cx(tdNum, m.psi_footfall != null && m.psi_footfall > 0.25 && "text-haldi-deep")}>{m.psi_footfall != null ? m.psi_footfall.toFixed(2) : "–"}</td>
                <td className={td}>
                  {r.challenger ? (
                    <>
                      <button onClick={() => setOpen(r.challenger!.id)} className="text-left underline decoration-rim-strong underline-offset-4">
                        v{r.challenger.version} {r.challenger.algorithm.replace(/_/g, " ")}
                      </button>
                      <p className="text-xs text-muted">
                        shadow {r.challenger.days_observed}/{r.challenger.days_needed} days
                      </p>
                    </>
                  ) : (
                    <span className="text-muted">none</span>
                  )}
                  {r.last_training?.status === "dead" ? <p className="text-xs text-chilli">last training failed</p> : null}
                </td>
                <td className={cx(td, "whitespace-nowrap text-right")}>
                  <Button size="sm" variant="ghost" icon={<Activity className="size-3.5" />} onClick={() => run("monitor", r.canteen_id, r.canteen)}>
                    Check
                  </Button>
                  <Button size="sm" variant="ghost" icon={<RotateCcw className="size-3.5" />} onClick={() => run("retrain", r.canteen_id, r.canteen)}>
                    Retrain
                  </Button>
                </td>
              </tr>
            );
          })}
        </Table>
        <p className="mt-2 text-sm text-muted">
          Live figures are checked daily by the monitor job. Ran short: share of dishes that sold out, against the share the plan's service level expects. Drift: population stability index of footfall against the
          training period (above 0.25 suggests retraining).
        </p>
      </Panel>
      {open ? <VersionDialog id={open} onClose={() => setOpen(null)} /> : null}
    </div>
  );
}

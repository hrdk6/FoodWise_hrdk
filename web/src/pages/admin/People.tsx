import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useOpsOrganisations, useOpsSecurity, useOpsVitals } from "@/api/hooks";
import { fmtDateTime, fmtNum } from "@/lib/format";
import { C, ChartFrame, TooltipBox, axisProps } from "@/ui/charts";
import { ErrorState, Segmented, Skeleton, cx } from "@/ui/primitives";

import { VitalTiles } from "./Overview";
import { Kpi, Panel, Status, Table, fmtAgo, fmtMs, pct, td, tdNum, timeLabel } from "./parts";

const LIMITS: Record<string, [number, number]> = { LCP: [2500, 4000], INP: [200, 500], CLS: [0.1, 0.25], FCP: [1800, 3000], TTFB: [800, 1800] };

function vitalCell(metric: string, v: number | null) {
  if (v == null) return <span className="text-muted">–</span>;
  const [good, poor] = LIMITS[metric];
  const cls = v <= good ? "text-leaf" : v <= poor ? "text-haldi-deep" : "text-chilli";
  const mark = v <= good ? "" : v <= poor ? " ▲" : " ▲▲";
  return (
    <span className={cls} title={v <= good ? "Good" : v <= poor ? "Needs work" : "Poor"}>
      {metric === "CLS" ? v.toFixed(3) : fmtMs(v)}
      {mark}
    </span>
  );
}

function Web() {
  const [hours, setHours] = useState<"24" | "168" | "336">("24");
  const q = useOpsVitals(Number(hours));
  if (q.isLoading) return <Skeleton className="h-[420px] w-full" />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-base text-ink-2">
          Measured in the browsers of people using FoodWise (the 75th percentile, as search engines judge it). Only the page pattern and connection type are recorded, never who it was.
        </p>
        <Segmented label="Period" size="sm" value={hours} onChange={setHours} options={[{ value: "24", label: "24 h" }, { value: "168", label: "7 days" }, { value: "336", label: "14 days" }]} />
      </div>
      <div className="mb-8">
        <VitalTiles vitals={d.overall} />
      </div>
      <Panel title="By page" sub="▲ needs work, ▲▲ poor.">
        <Table head={[{ label: "Page" }, { label: "Samples", right: true }, { label: "LCP", right: true }, { label: "INP", right: true }, { label: "CLS", right: true }, { label: "FCP", right: true }, { label: "TTFB", right: true }]} minWidth={720}>
          {d.routes.map((r) => (
            <tr key={r.route as string} className="border-b border-steel-2 last:border-0">
              <td className={cx(td, "font-mono text-[13px]")}>{r.route as string}</td>
              <td className={tdNum}>{fmtNum(r.samples as number)}</td>
              {(["LCP", "INP", "CLS", "FCP", "TTFB"] as const).map((m) => (
                <td key={m} className={tdNum}>
                  {vitalCell(m, r[`${m}_p75`] as number | null)}
                </td>
              ))}
            </tr>
          ))}
          {!d.routes.length ? (
            <tr>
              <td className={td} colSpan={7}>
                No visits measured yet in this period.
              </td>
            </tr>
          ) : null}
        </Table>
      </Panel>
    </div>
  );
}

function Organisations() {
  const q = useOpsOrganisations();
  if (q.isLoading) return <Skeleton className="h-[320px] w-full" />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const rows = q.data!;
  return (
    <Panel title="Organisations" sub="Activity and setup, without any of their records.">
      <Table
        head={[
          { label: "Organisation" },
          { label: "Canteens", right: true },
          { label: "Days logged", right: true },
          { label: "Last log" },
          { label: "Plans decided (7 d)", right: true },
          { label: "Surplus (30 d)", right: true },
          { label: "Dish pictures", right: true },
          { label: "Last sign-in" },
        ]}
        minWidth={940}
      >
        {rows.map((o) => (
          <tr key={o.id} className="border-b border-steel-2 last:border-0">
            <td className={td}>
              <p className="font-medium">{o.name}</p>
              <p className="text-xs text-muted">
                Joined {fmtDateTime(o.created_at)}
                {o.is_demo ? ". Demo" : ""}
                {o.status !== "active" ? `. ${o.status}` : ""}
              </p>
            </td>
            <td className={tdNum}>{fmtNum(o.canteens)}</td>
            <td className={tdNum}>{fmtNum(o.days_logged)}</td>
            <td className={td}>{o.last_log ?? "never"}</td>
            <td className={tdNum}>
              {o.forecasts_7d ? pct(o.decided_share, 0) : "–"}
              <p className="text-xs text-muted">{fmtNum(o.forecasts_7d)} dishes</p>
            </td>
            <td className={tdNum}>
              {fmtNum(o.surplus_30d)}
              {o.open_surplus ? <p className="text-xs text-haldi-deep">{o.open_surplus} open</p> : null}
            </td>
            <td className={tdNum}>
              {fmtNum(o.dishes_with_picture)} / {fmtNum(o.dishes)}
            </td>
            <td className={td}>{fmtAgo(o.last_sign_in)}</td>
          </tr>
        ))}
      </Table>
    </Panel>
  );
}

function Security() {
  const q = useOpsSecurity();
  if (q.isLoading) return <Skeleton className="h-[320px] w-full" />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const series = d.failures_by_hour.map((h) => ({ t: timeLabel(h.hour as string), failures: h.failures as number }));
  return (
    <div>
      <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Sign-ins, 24 h" value={fmtNum(d.sign_ins_24h)} />
        <Kpi label="Failed sign-ins, 24 h" value={fmtNum(d.failed_24h)} level={d.failed_24h > 50 ? "warning" : undefined} />
        <Kpi label="Accounts locked now" value={fmtNum(d.locked_accounts)} sub="after 5 failures, for 15 minutes" level={d.locked_accounts ? "warning" : undefined} />
        <Kpi label="Stolen-session alarms, 7 d" value={fmtNum(d.token_reuse_7d)} sub="a used refresh token was replayed" level={d.token_reuse_7d ? "critical" : undefined} />
      </div>
      <div className="grid gap-6 lg:grid-cols-2">
        {series.length ? (
          <ChartFrame title="Failed sign-ins by hour" sub="Last 24 hours" table={{ columns: ["Hour", "Failures"], rows: series.map((s) => [s.t, s.failures]) }} height={220}>
            <ResponsiveContainer>
              <BarChart data={series} margin={{ top: 6, right: 8, bottom: 0, left: -18 }} barCategoryGap={2}>
                <CartesianGrid stroke={C.grid} vertical={false} />
                <XAxis dataKey="t" {...axisProps} minTickGap={24} />
                <YAxis {...axisProps} allowDecimals={false} width={40} />
                <Tooltip cursor={{ fill: C.grid }} content={({ active, payload, label }) => (active && payload?.length ? <TooltipBox title={label} rows={[{ color: C.blue, label: "Failed", value: fmtNum(payload[0].payload.failures) }]} /> : null)} />
                <Bar dataKey="failures" fill={C.blue} radius={[4, 4, 0, 0]} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </ChartFrame>
        ) : (
          <p className="rim flex items-center gap-2 px-4 py-3 text-base">
            <Status level="good">Quiet</Status> No failed sign-ins in the last 24 hours.
          </p>
        )}
        <Panel title="Recent failed sign-ins" sub="Emails and addresses are partly hidden.">
          <Table head={[{ label: "When" }, { label: "Account" }, { label: "From" }]} minWidth={420}>
            {d.recent_failures.map((f, i) => (
              <tr key={i} className="border-b border-steel-2 last:border-0">
                <td className={td}>{fmtAgo(f.at as string)}</td>
                <td className={cx(td, "font-mono text-[13px]")}>{f.account as string}</td>
                <td className={cx(td, "font-mono text-[13px]")}>{(f.ip as string) ?? "–"}</td>
              </tr>
            ))}
            {!d.recent_failures.length ? (
              <tr>
                <td className={td} colSpan={3}>
                  None recorded.
                </td>
              </tr>
            ) : null}
          </Table>
        </Panel>
      </div>
    </div>
  );
}

export default function People({ part }: { part: "web" | "organisations" | "security" }) {
  if (part === "organisations") return <Organisations />;
  if (part === "security") return <Security />;
  return <Web />;
}

import { Link } from "react-router";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useOpsOverview } from "@/api/hooks";
import { fmtNum } from "@/lib/format";
import { C, ChartFrame, LegendItem, TooltipBox, axisProps } from "@/ui/charts";
import { ErrorState, Skeleton } from "@/ui/primitives";

import { Kpi, type Level, Panel, Status, Table, fmtAgo, fmtMs, pct, td, tdNum, timeLabel } from "./parts";

const ATTN: Record<string, Level> = { critical: "critical", warning: "warning", info: "info" };

export function VitalTiles({ vitals }: { vitals: { metric: string; p75: number | null; samples: number; good: number | null; rating: string; good_limit: number; poor_limit: number }[] }) {
  const name: Record<string, string> = {
    LCP: "Largest content shown",
    INP: "Response to taps",
    CLS: "Layout shift",
    FCP: "First content shown",
    TTFB: "Server response",
  };
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
      {vitals.map((v) => {
        const value = v.p75 == null ? "–" : v.metric === "CLS" ? v.p75.toFixed(3) : fmtMs(v.p75);
        const level: Level = v.rating === "good" ? "good" : v.rating === "poor" ? "critical" : v.rating === "none" ? "none" : "warning";
        return (
          <div key={v.metric} className="rim px-4 py-3">
            <p className="text-sm text-muted">
              {name[v.metric]} <span className="text-xs">({v.metric})</span>
            </p>
            <p className="qty mt-1 text-[1.5rem] leading-none">{value}</p>
            <div className="mt-2">
              <Status level={level}>{v.rating === "none" ? "No data" : v.rating === "needs-improvement" ? "Needs work" : v.rating === "good" ? "Good" : "Poor"}</Status>
            </div>
            <p className="mt-1.5 text-xs text-muted">
              p75 of {fmtNum(v.samples)} visits. Good under {v.metric === "CLS" ? v.good_limit : fmtMs(v.good_limit)}
            </p>
          </div>
        );
      })}
    </div>
  );
}

export default function Overview() {
  const o = useOpsOverview();
  if (o.isLoading) return <Skeleton className="h-[480px] w-full" />;
  if (o.error) return <ErrorState error={o.error} retry={() => o.refetch()} />;
  const d = o.data!;
  const alive = d.workers.filter((w) => w.alive).length;
  const errLevel: Level = d.api.error_rate == null ? "none" : d.api.error_rate > 0.05 ? "critical" : d.api.error_rate > 0.01 ? "warning" : "good";
  const series = d.api.series.map((s) => ({ ...s, t: timeLabel(s.at) }));
  return (
    <div>
      <Panel title="Needs attention" sub={`Checked ${fmtAgo(d.generated_at)}.`}>
        {d.attention.length ? (
          <ul className="rim divide-y divide-rim">
            {d.attention.map((a, i) => (
              <li key={i} className="flex flex-wrap items-start gap-x-3 gap-y-1 px-4 py-2.5">
                <Status level={ATTN[a.level]}>{a.area}</Status>
                <p className="min-w-0 flex-1 text-base text-ink">{a.message}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="rim flex items-center gap-2 px-4 py-3 text-base text-ink">
            <Status level="good">All clear</Status> Nothing needs attention right now.
          </p>
        )}
      </Panel>

      <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Kpi label="Requests, last hour" value={fmtNum(d.api.requests)} sub={`${fmtNum(d.api.requests_per_min, 1)} per minute`} />
        <Kpi label="Server errors" value={pct(d.api.error_rate, 2)} level={errLevel === "good" ? undefined : errLevel} sub={`${fmtNum(d.api.server_errors)} in the last hour`} />
        <Kpi label="p95 response" value={fmtMs(d.api.p95_ms)} level={d.api.p95_ms && d.api.p95_ms > 1500 ? "warning" : undefined} sub={`median ${fmtMs(d.api.p50_ms)}`} />
        <Kpi label="Workers" value={`${alive} / ${d.workers.length}`} level={alive ? undefined : "critical"} sub={alive ? "checking in" : "none checking in"} />
        <Kpi label="Jobs waiting" value={fmtNum(d.jobs.queued)} level={d.jobs.oldest_queued_s && d.jobs.oldest_queued_s > 300 ? "warning" : undefined} sub={`${fmtNum(d.jobs.running)} running, ${fmtNum(d.jobs.dead)} dead`} />
        <Kpi label="Live forecast error" value={pct(d.models.median_live_wape)} sub="median across canteens" />
        <Kpi label="Model alerts" value={fmtNum(d.models.alerts)} level={d.models.degraded ? "warning" : undefined} sub={`${d.models.degraded} degraded, ${d.models.drift} drifting`} />
        <Kpi label="Database" value={fmtMs(d.database.latency_ms)} sub={`${fmtNum(d.database.size_mb)} MB, ${d.database.connections} connections`} />
      </div>

      <div className="mb-8 grid gap-4 lg:grid-cols-2">
        <ChartFrame
          title="Requests per minute"
          sub="Last 60 minutes, this API process"
          legend={
            <>
              <LegendItem color={C.blue} label="Requests" />
              <LegendItem color={C.bad} label="Server errors" />
            </>
          }
          table={{ columns: ["Minute", "Requests", "Server errors"], rows: series.filter((s) => s.requests).map((s) => [s.t, s.requests, s.server_errors]) }}
          height={200}
        >
          <ResponsiveContainer>
            <LineChart data={series} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="t" {...axisProps} minTickGap={40} />
              <YAxis {...axisProps} allowDecimals={false} width={44} />
              <Tooltip
                cursor={{ stroke: C.axis, strokeDasharray: "3 3" }}
                content={({ active, payload, label }) =>
                  active && payload?.length ? (
                    <TooltipBox
                      title={label}
                      rows={[
                        { color: C.blue, label: "Requests", value: fmtNum(payload[0].payload.requests) },
                        { color: C.bad, label: "Server errors", value: fmtNum(payload[0].payload.server_errors) },
                      ]}
                    />
                  ) : null
                }
              />
              <Line type="monotone" dataKey="requests" stroke={C.blue} strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="server_errors" stroke={C.bad} strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </ChartFrame>
        <ChartFrame
          title="Response time"
          sub="Last 60 minutes, by minute"
          legend={
            <>
              <LegendItem color={C.blue} label="Median" />
              <LegendItem color={C.turmeric} label="95th percentile" />
            </>
          }
          table={{ columns: ["Minute", "Median (ms)", "p95 (ms)"], rows: series.filter((s) => s.requests).map((s) => [s.t, s.p50_ms ?? "–", s.p95_ms ?? "–"]) }}
          height={200}
        >
          <ResponsiveContainer>
            <LineChart data={series} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="t" {...axisProps} minTickGap={40} />
              <YAxis {...axisProps} width={44} tickFormatter={(v) => `${v}`} unit=" ms" />
              <Tooltip
                cursor={{ stroke: C.axis, strokeDasharray: "3 3" }}
                content={({ active, payload, label }) =>
                  active && payload?.length ? (
                    <TooltipBox
                      title={label}
                      rows={[
                        { color: C.blue, label: "Median", value: fmtMs(payload[0].payload.p50_ms) },
                        { color: C.turmeric, label: "p95", value: fmtMs(payload[0].payload.p95_ms) },
                      ]}
                    />
                  ) : null
                }
              />
              <Line type="monotone" dataKey="p50_ms" stroke={C.blue} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
              <Line type="monotone" dataKey="p95_ms" stroke={C.turmeric} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </ChartFrame>
      </div>

      <div className="grid gap-x-6 lg:grid-cols-2">
        <Panel title="External services" sub="Calls in the last 24 hours, from the API and the workers.">
          <Table head={[{ label: "Service" }, { label: "Calls", right: true }, { label: "Failed", right: true }, { label: "Average", right: true }, { label: "Last success" }]} minWidth={520}>
            {d.external.map((e) => (
              <tr key={e.service} className="border-b border-steel-2 last:border-0">
                <td className={td}>
                  <p className="font-medium">{e.label}</p>
                  {e.last_error ? (
                    <p className="max-w-[260px] truncate text-xs text-muted" title={e.last_error}>
                      Last error {fmtAgo(e.last_error_at)}: {e.last_error}
                    </p>
                  ) : null}
                </td>
                <td className={tdNum}>{fmtNum(e.calls_24h)}</td>
                <td className={tdNum}>{e.calls_24h ? pct(e.failure_rate, 0) : "–"}</td>
                <td className={tdNum}>{fmtMs(e.avg_ms)}</td>
                <td className={td}>{fmtAgo(e.last_ok_at)}</td>
              </tr>
            ))}
          </Table>
        </Panel>
        <Panel title="Usage today" sub="Across every organisation.">
          <div className="grid grid-cols-2 gap-3">
            <Kpi label="Organisations" value={fmtNum(d.organisations)} sub={`${fmtNum(d.canteens)} canteens`} />
            <Kpi label="Canteens that logged today" value={fmtNum(d.logged_today)} />
            <Kpi label="Forecasts made today" value={fmtNum(d.forecasts_today)} />
            <Kpi label="Surplus waiting for a place" value={fmtNum(d.open_surplus)} />
          </div>
        </Panel>
      </div>

      <Panel
        title="Page speed for real users"
        sub="Core Web Vitals from browsers using FoodWise, last 24 hours."
        actions={
          <Link to="/admin/web" className="text-sm text-ink-2 underline underline-offset-4">
            By page
          </Link>
        }
      >
        <VitalTiles vitals={d.vitals} />
      </Panel>
    </div>
  );
}

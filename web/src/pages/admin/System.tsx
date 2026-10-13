import { RotateCcw } from "lucide-react";
import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ApiError } from "@/api/client";
import { useOpsAction, useOpsApi, useOpsDatabase, useOpsJobs } from "@/api/hooks";
import { fmtDateTime, fmtNum } from "@/lib/format";
import { C, ChartFrame, LegendItem, TooltipBox, axisProps } from "@/ui/charts";
import { useToast } from "@/ui/overlays";
import { Button, ErrorState, Segmented, Skeleton, cx } from "@/ui/primitives";

import { Kpi, Panel, Status, Table, fmtAgo, fmtMs, fmtSecs, pct, td, tdNum, timeLabel } from "./parts";

type Sort = "requests" | "p95" | "errors";

function Api() {
  const [win, setWin] = useState<"15" | "60" | "360" | "1440">("60");
  const [sort, setSort] = useState<Sort>("requests");
  const q = useOpsApi(Number(win));
  if (q.isLoading) return <Skeleton className="h-[480px] w-full" />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const s = d.summary;
  const series = s.series.map((p) => ({ ...p, t: timeLabel(p.at) }));
  const routes = [...d.routes].sort((a, b) =>
    sort === "p95" ? (b.p95_ms ?? 0) - (a.p95_ms ?? 0) : sort === "errors" ? b.server_errors - a.server_errors || b.client_errors - a.client_errors : b.requests - a.requests,
  );
  const label = { "15": "15 minutes", "60": "hour", "360": "6 hours", "1440": "24 hours" }[win];
  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <p className="text-base text-ink-2">
          Traffic handled by this API process since {fmtDateTime(s.up_since)}. Prometheus counters are also at <code className="rounded bg-steel-2 px-1">/metrics</code>.
        </p>
        <Segmented label="Window" size="sm" value={win} onChange={setWin} options={[{ value: "15", label: "15 min" }, { value: "60", label: "1 h" }, { value: "360", label: "6 h" }, { value: "1440", label: "24 h" }]} />
      </div>
      <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Kpi label={`Requests, last ${label}`} value={fmtNum(s.requests)} sub={`${fmtNum(s.requests_per_min, 1)} per minute`} />
        <Kpi label="Server errors (5xx)" value={fmtNum(s.server_errors)} sub={pct(s.error_rate, 2)} level={s.error_rate && s.error_rate > 0.01 ? "warning" : undefined} />
        <Kpi label="Client errors (4xx)" value={fmtNum(s.client_errors)} sub="sign-in expiry, validation, not found" />
        <Kpi label="Median response" value={fmtMs(s.p50_ms)} />
        <Kpi label="p95 response" value={fmtMs(s.p95_ms)} level={s.p95_ms && s.p95_ms > 1500 ? "warning" : undefined} />
      </div>
      <div className="mb-8 grid gap-4 lg:grid-cols-2">
        <ChartFrame
          title="Requests"
          legend={
            <>
              <LegendItem color={C.blue} label="Requests" />
              <LegendItem color={C.bad} label="Server errors" />
            </>
          }
          table={{ columns: ["Time", "Requests", "Server errors", "Client errors"], rows: series.filter((p) => p.requests).map((p) => [p.t, p.requests, p.server_errors, p.client_errors]) }}
          height={220}
        >
          <ResponsiveContainer>
            <LineChart data={series} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="t" {...axisProps} minTickGap={40} />
              <YAxis {...axisProps} allowDecimals={false} width={44} />
              <Tooltip
                cursor={{ stroke: C.axis, strokeDasharray: "3 3" }}
                content={({ active, payload, label: l }) =>
                  active && payload?.length ? (
                    <TooltipBox title={l} rows={[{ color: C.blue, label: "Requests", value: fmtNum(payload[0].payload.requests) }, { color: C.bad, label: "Server errors", value: fmtNum(payload[0].payload.server_errors) }]} />
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
          legend={
            <>
              <LegendItem color={C.blue} label="Median" />
              <LegendItem color={C.turmeric} label="95th percentile" />
            </>
          }
          table={{ columns: ["Time", "Median (ms)", "p95 (ms)"], rows: series.filter((p) => p.requests).map((p) => [p.t, p.p50_ms ?? "–", p.p95_ms ?? "–"]) }}
          height={220}
        >
          <ResponsiveContainer>
            <LineChart data={series} margin={{ top: 6, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid stroke={C.grid} vertical={false} />
              <XAxis dataKey="t" {...axisProps} minTickGap={40} />
              <YAxis {...axisProps} width={44} unit=" ms" />
              <Tooltip
                cursor={{ stroke: C.axis, strokeDasharray: "3 3" }}
                content={({ active, payload, label: l }) =>
                  active && payload?.length ? (
                    <TooltipBox title={l} rows={[{ color: C.blue, label: "Median", value: fmtMs(payload[0].payload.p50_ms) }, { color: C.turmeric, label: "p95", value: fmtMs(payload[0].payload.p95_ms) }]} />
                  ) : null
                }
              />
              <Line type="monotone" dataKey="p50_ms" stroke={C.blue} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
              <Line type="monotone" dataKey="p95_ms" stroke={C.turmeric} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </ChartFrame>
      </div>

      <Panel title="Routes" actions={<Segmented label="Sort by" size="sm" value={sort} onChange={setSort} options={[{ value: "requests", label: "Busiest" }, { value: "p95", label: "Slowest" }, { value: "errors", label: "Most errors" }]} />}>
        <Table head={[{ label: "Route" }, { label: "Requests", right: true }, { label: "5xx", right: true }, { label: "4xx", right: true }, { label: "Median", right: true }, { label: "p95", right: true }, { label: "p99", right: true }, { label: "Max", right: true }]} minWidth={820}>
          {routes.map((r) => (
            <tr key={r.route} className="border-b border-steel-2 last:border-0">
              <td className={cx(td, "font-mono text-[13px]")}>{r.route}</td>
              <td className={tdNum}>{fmtNum(r.requests)}</td>
              <td className={cx(tdNum, r.server_errors > 0 && "text-chilli")}>{fmtNum(r.server_errors)}</td>
              <td className={tdNum}>{fmtNum(r.client_errors)}</td>
              <td className={tdNum}>{fmtMs(r.p50_ms)}</td>
              <td className={cx(tdNum, (r.p95_ms ?? 0) > 1500 && "text-haldi-deep")}>{fmtMs(r.p95_ms)}</td>
              <td className={tdNum}>{fmtMs(r.p99_ms)}</td>
              <td className={tdNum}>{fmtMs(r.max_ms)}</td>
            </tr>
          ))}
          {!routes.length ? (
            <tr>
              <td className={td} colSpan={8}>
                No requests in this window.
              </td>
            </tr>
          ) : null}
        </Table>
      </Panel>

      <div className="grid gap-x-6 lg:grid-cols-2">
        <Panel title="Recent server errors" sub="Find the full trace in the logs by request id.">
          {d.errors.length ? (
            <Table head={[{ label: "When" }, { label: "Route" }, { label: "Status", right: true }, { label: "Request id" }]} minWidth={520}>
              {d.errors.map((e, i) => (
                <tr key={i} className="border-b border-steel-2 last:border-0">
                  <td className={td}>{fmtAgo(e.at as string)}</td>
                  <td className={cx(td, "font-mono text-[13px]")}>{e.route as string}</td>
                  <td className={tdNum}>{e.status as number}</td>
                  <td className={cx(td, "font-mono text-[13px]")}>{e.request_id as string}</td>
                </tr>
              ))}
            </Table>
          ) : (
            <p className="rim flex items-center gap-2 px-4 py-3 text-base">
              <Status level="good">None</Status> No server errors since the API started.
            </p>
          )}
        </Panel>
        <Panel title="Slow requests" sub="Over 800 ms, most recent first.">
          {d.slow.length ? (
            <Table head={[{ label: "When" }, { label: "Route" }, { label: "Took", right: true }]} minWidth={420}>
              {d.slow.map((e, i) => (
                <tr key={i} className="border-b border-steel-2 last:border-0">
                  <td className={td}>{fmtAgo(e.at as string)}</td>
                  <td className={cx(td, "font-mono text-[13px]")}>{e.route as string}</td>
                  <td className={tdNum}>{fmtMs(e.ms as number)}</td>
                </tr>
              ))}
            </Table>
          ) : (
            <p className="rim px-4 py-3 text-base text-muted">No slow requests.</p>
          )}
        </Panel>
      </div>
    </div>
  );
}

function Jobs() {
  const q = useOpsJobs();
  const act = useOpsAction();
  const toast = useToast();
  if (q.isLoading) return <Skeleton className="h-[480px] w-full" />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const retry = async (id: string) => {
    try {
      await act.mutateAsync({ kind: "retry", jobId: id });
      toast("ok", "Job queued again.");
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not retry.");
    }
  };
  const alive = d.workers.filter((w) => w.alive).length;
  return (
    <div>
      <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Kpi label="Workers checking in" value={`${alive} / ${d.workers.length}`} level={alive ? undefined : "critical"} />
        <Kpi label="Waiting" value={fmtNum(d.summary.queued)} sub={d.summary.oldest_queued_s ? `oldest ${fmtSecs(d.summary.oldest_queued_s)}` : "nothing overdue"} level={d.summary.oldest_queued_s && d.summary.oldest_queued_s > 300 ? "warning" : undefined} />
        <Kpi label="Running" value={fmtNum(d.summary.running)} />
        <Kpi label="Done in 24 h" value={fmtNum(d.summary.succeeded_24h)} sub={`${fmtNum(d.summary.failed_24h)} failed`} />
        <Kpi label="Dead letters" value={fmtNum(d.summary.dead)} level={d.summary.dead ? "warning" : undefined} sub="failed every retry" />
      </div>

      <Panel title="Workers" sub="Each worker checks in every 20 seconds and after every job.">
        <Table head={[{ label: "Worker" }, { label: "State" }, { label: "Last seen" }, { label: "Now running" }, { label: "Done", right: true }, { label: "Failed", right: true }, { label: "Up since" }]} minWidth={760}>
          {d.workers.map((w) => (
            <tr key={w.worker_id} className="border-b border-steel-2 last:border-0">
              <td className={cx(td, "font-mono text-[13px]")}>{w.worker_id}</td>
              <td className={td}>
                <Status level={w.alive ? "good" : "critical"}>{w.alive ? "Alive" : "Silent"}</Status>
              </td>
              <td className={td}>{fmtSecs(w.seconds_since_seen)} ago</td>
              <td className={td}>{w.current_job ?? "idle"}</td>
              <td className={tdNum}>{fmtNum(w.jobs_done)}</td>
              <td className={tdNum}>{fmtNum(w.jobs_failed)}</td>
              <td className={td}>{fmtDateTime(w.started_at)}</td>
            </tr>
          ))}
          {!d.workers.length ? (
            <tr>
              <td className={td} colSpan={7}>
                No worker has checked in during the last six hours. Start one with <code>foodwise worker</code>.
              </td>
            </tr>
          ) : null}
        </Table>
      </Panel>

      <Panel title="By kind of job" sub="Last 24 hours, plus anything still waiting.">
        <Table head={[{ label: "Job" }, { label: "Waiting", right: true }, { label: "Running", right: true }, { label: "Done", right: true }, { label: "Failed", right: true }, { label: "Dead", right: true }, { label: "Average", right: true }, { label: "p95", right: true }, { label: "Longest", right: true }]} minWidth={820}>
          {d.kinds.map((k) => (
            <tr key={k.kind} className="border-b border-steel-2 last:border-0">
              <td className={cx(td, "font-mono text-[13px]")}>{k.kind}</td>
              <td className={tdNum}>{fmtNum(k.queued)}</td>
              <td className={tdNum}>{fmtNum(k.running)}</td>
              <td className={tdNum}>{fmtNum(k.succeeded)}</td>
              <td className={cx(tdNum, k.failed > 0 && "text-haldi-deep")}>{fmtNum(k.failed)}</td>
              <td className={cx(tdNum, k.dead > 0 && "text-chilli")}>{fmtNum(k.dead)}</td>
              <td className={tdNum}>{fmtSecs(k.avg_s)}</td>
              <td className={tdNum}>{fmtSecs(k.p95_s)}</td>
              <td className={tdNum}>{fmtSecs(k.max_s)}</td>
            </tr>
          ))}
        </Table>
      </Panel>

      <Panel title="Dead letters" sub="Jobs that failed every attempt. Fix the cause, then retry.">
        {d.dead.length ? (
          <ul className="rim divide-y divide-rim">
            {d.dead.map((j) => (
              <li key={j.id} className="flex flex-wrap items-start gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-[13px] font-medium">{j.kind}</p>
                  <p className="text-sm text-muted">
                    {j.tenant ?? "platform"}. {j.attempts} attempts, last {fmtAgo(j.finished_at)}.
                  </p>
                  {j.error ? <p className="mt-1 break-all font-mono text-xs text-chilli">{j.error}</p> : null}
                </div>
                <Button size="sm" icon={<RotateCcw className="size-3.5" />} busy={act.isPending} onClick={() => retry(j.id)}>
                  Retry
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="rim flex items-center gap-2 px-4 py-3 text-base">
            <Status level="good">Empty</Status> No dead jobs.
          </p>
        )}
      </Panel>
      {d.recent_failures.length ? (
        <Panel title="Failed, will retry">
          <ul className="rim divide-y divide-rim">
            {d.recent_failures.map((j) => (
              <li key={j.id} className="px-4 py-2.5">
                <p className="font-mono text-[13px]">
                  {j.kind} <span className="text-muted">attempt {j.attempts} of {j.max_attempts}</span>
                </p>
                {j.error ? <p className="break-all font-mono text-xs text-ink-2">{j.error}</p> : null}
              </li>
            ))}
          </ul>
        </Panel>
      ) : null}
    </div>
  );
}

function Database() {
  const q = useOpsDatabase();
  if (q.isLoading) return <Skeleton className="h-[420px] w-full" />;
  if (q.error) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const used = Object.values(d.connections).reduce((a, b) => a + b, 0);
  return (
    <div>
      <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Kpi label="Round trip" value={fmtMs(d.summary.latency_ms)} sub={`PostgreSQL ${d.version.split(" ")[0]}`} />
        <Kpi label="Size" value={`${fmtNum(d.summary.size_mb)} MB`} sub={`schema ${d.migration ?? "unknown"}`} />
        <Kpi label="Connections" value={`${used} / ${d.max_connections}`} level={used > d.max_connections * 0.8 ? "warning" : undefined} sub={Object.entries(d.connections).map(([k, v]) => `${v} ${k}`).join(", ")} />
        <Kpi label="Cache hit rate" value={pct(d.summary.cache_hit, 2)} level={d.summary.cache_hit != null && d.summary.cache_hit < 0.95 ? "warning" : undefined} sub="aim for above 99%" />
        <Kpi label="Longest running query" value={fmtSecs(d.longest_query_s)} sub={`${fmtNum(d.deadlocks)} deadlocks, ${fmtNum(d.rollbacks)} rollbacks`} level={(d.longest_query_s ?? 0) > 30 ? "warning" : undefined} />
      </div>
      <Panel title="Largest tables" sub="Row counts are PostgreSQL's estimates. Many dead rows means autovacuum is behind.">
        <Table head={[{ label: "Table" }, { label: "Rows", right: true }, { label: "Dead rows", right: true }, { label: "Size", right: true }, { label: "Index scans", right: true }, { label: "Full scans", right: true }, { label: "Last vacuum" }]} minWidth={760}>
          {d.tables.map((t) => (
            <tr key={t.table} className="border-b border-steel-2 last:border-0">
              <td className={cx(td, "font-mono text-[13px]")}>{t.table}</td>
              <td className={tdNum}>{fmtNum(t.rows)}</td>
              <td className={cx(tdNum, t.rows > 0 && t.dead_rows > t.rows * 0.2 && "text-haldi-deep")}>{fmtNum(t.dead_rows)}</td>
              <td className={tdNum}>{fmtNum(t.size_mb, 1)} MB</td>
              <td className={tdNum}>{fmtNum(t.index_scans)}</td>
              <td className={tdNum}>{fmtNum(t.seq_scans)}</td>
              <td className={td}>{fmtAgo(t.last_vacuum)}</td>
            </tr>
          ))}
        </Table>
        <p className="mt-2 text-sm text-muted">Every organisation table is protected by row-level security; this view reads only PostgreSQL's own statistics.</p>
      </Panel>
    </div>
  );
}

export default function System({ part }: { part: "api" | "jobs" | "database" }) {
  if (part === "jobs") return <Jobs />;
  if (part === "database") return <Database />;
  return <Api />;
}

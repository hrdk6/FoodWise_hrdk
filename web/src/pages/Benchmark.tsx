import { CheckCircle2, CircleX, Download, Loader2, Play } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError, download } from "@/api/client";
import { useGenpact, useJob, useRunGenpact } from "@/api/hooks";
import { useAuth } from "@/app/auth";
import { fmtDateTime, fmtNum, fmtPct } from "@/lib/format";
import { useToast } from "@/ui/overlays";
import { Badge, Button, EmptyState, ErrorState, Figure, Meter, PageHeader, Section, Skeleton, cx } from "@/ui/primitives";

type Step = {
  n: number;
  key: string;
  title: string;
  findings: string[];
  action: string;
  counts: Record<string, number | boolean>;
  examples?: { week: number; center_id: number; meal_id: number; category: string; orders: number; typical: number; promotion: boolean }[];
  groups?: { group: string; columns: string[] }[];
  passed?: boolean;
};

type Row = {
  model: string;
  label: string;
  family: string;
  wape: number;
  rmsle: number;
  bias: number;
  skill: number;
  per_fold_wape: number[];
  status: string;
  beats_baseline_every_fold: boolean;
};

type Report = {
  dataset: { name: string; source: string; description: string };
  generated_at: string;
  duration_seconds: number;
  rows_raw: number;
  rows_clean: number;
  steps: Step[];
  folds: { fold: number; train_weeks: [number, number]; test_weeks: [number, number]; train_rows: number; test_rows: number }[];
  leaderboard: Row[];
  winner: string;
  best_baseline: string;
  horizon: string;
  tested_rows: number;
  cold_start_rows: number;
  notes: string[];
  library_versions: Record<string, string>;
};

function RunButton({ again }: { again: boolean }) {
  const { can } = useAuth();
  const run = useRunGenpact();
  const toast = useToast();
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const status = job.data?.status;
  const progress = job.data?.progress as { label?: string; fraction?: number } | null | undefined;

  useEffect(() => {
    if (!jobId || !status) return;
    if (status === "succeeded") {
      qc.invalidateQueries({ queryKey: ["benchmark", "genpact"] });
      toast("ok", "The benchmark finished.");
      setJobId(null);
    } else if (status === "dead" || status === "failed") {
      toast("error", job.data?.last_error ?? "The benchmark failed.");
      setJobId(null);
    }
  }, [jobId, status, job.data, qc, toast]);

  if (!can("models:train")) return null;
  if (jobId) {
    return (
      <div className="flex min-w-64 flex-col gap-1.5" role="status">
        <p className="inline-flex items-center gap-2 text-sm text-ink-2">
          <Loader2 className="size-4 animate-spin" aria-hidden /> {progress?.label ?? "Waiting for a worker"}
        </p>
        <Meter value={progress?.fraction ?? 0.02} tone="ink" label="Benchmark progress" />
      </div>
    );
  }
  return (
    <Button
      icon={<Play className="size-4" />}
      variant={again ? "ghost" : undefined}
      busy={run.isPending}
      onClick={async () => {
        try {
          const ref = await run.mutateAsync();
          setJobId(ref.job_id);
        } catch (e) {
          toast("error", e instanceof ApiError ? e.message : "Could not start the benchmark.");
        }
      }}
    >
      {again ? "Run again" : "Run benchmark"}
    </Button>
  );
}

function StepCard({ step }: { step: Step }) {
  return (
    <li className="rim px-5 py-4">
      <div className="flex items-start gap-3">
        <span className="num flex size-7 shrink-0 items-center justify-center rounded-full bg-ink text-sm font-semibold text-steel" aria-hidden>
          {step.n}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="flex flex-wrap items-center gap-2 font-[650]">
            {step.title}
            {step.passed !== undefined ? <Badge tone={step.passed ? "leaf" : "chilli"}>{step.passed ? "All checks passed" : "A check failed"}</Badge> : null}
          </h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-base text-ink-2">
            {step.findings.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
          <p className="mt-3 rounded-[8px] bg-steel-2 px-3 py-2 text-base text-ink">
            <strong className="font-[640]">Decision. </strong>
            {step.action}
          </p>
          {step.examples?.length ? (
            <div className="mt-3 overflow-x-auto">
              <table className="min-w-full text-sm">
                <caption className="mb-1 text-left text-sm text-muted">The most extreme weeks</caption>
                <thead className="text-left text-ink-2">
                  <tr>
                    {["Week", "Centre", "Meal", "Category", "Orders", "Usual level", "Promotion"].map((h, i) => (
                      <th key={h} className={cx("whitespace-nowrap px-2 py-1 font-[620]", i >= 4 && i <= 5 && "text-right")}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-rim">
                  {step.examples.map((e) => (
                    <tr key={`${e.week}-${e.center_id}-${e.meal_id}`}>
                      <td className="num px-2 py-1">{e.week}</td>
                      <td className="num px-2 py-1">{e.center_id}</td>
                      <td className="num px-2 py-1">{e.meal_id}</td>
                      <td className="px-2 py-1">{e.category}</td>
                      <td className="num px-2 py-1 text-right">{fmtNum(e.orders)}</td>
                      <td className="num px-2 py-1 text-right">{fmtNum(e.typical)}</td>
                      <td className="px-2 py-1">{e.promotion ? "yes" : "no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          {step.groups?.length ? (
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              {step.groups.map((g) => (
                <div key={g.group} className="rounded-[8px] border border-rim px-3 py-2">
                  <p className="mb-1 text-sm font-[620]">{g.group}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {g.columns.map((c) => (
                      <Badge key={c} tone="neutral">
                        {c}
                      </Badge>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </li>
  );
}

function Results({ r }: { r: Report }) {
  const max = Math.max(...r.leaderboard.map((x) => x.wape), 0.01);
  return (
    <ul className="rim divide-y divide-rim">
      {r.leaderboard.map((x) => {
        const selected = x.status === "selected";
        return (
          <li key={x.model} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 px-5 py-3 md:grid-cols-[minmax(180px,1fr)_minmax(140px,1.4fr)_70px_70px_minmax(160px,1fr)]">
            <div className="min-w-0">
              <p className={cx("truncate", selected ? "font-[650]" : "font-medium")}>{x.label}</p>
              <p className="text-sm text-muted">{x.family === "baseline" ? "Baseline" : x.family}</p>
            </div>
            <div className="order-3 col-span-2 md:order-none md:col-span-1" aria-hidden>
              <div className="h-2.5 rounded-r-[4px]" style={{ width: `${(x.wape / max) * 100}%`, background: selected ? "#3E6DB0" : "#A3B0AC" }} />
            </div>
            <p className="num text-right font-medium">{fmtPct(x.wape, 1)}</p>
            <p className="num hidden text-right text-ink-2 md:block">{x.rmsle.toFixed(3)}</p>
            <div className="order-4 col-span-2 text-sm md:order-none md:col-span-1">
              {selected ? (
                <span className="inline-flex items-center gap-1 text-leaf">
                  <CheckCircle2 className="size-4" aria-hidden /> Selected, {fmtPct(x.skill, 0, true)} better than the best baseline
                </span>
              ) : x.status === "baseline" ? (
                <span className="text-muted">Every model must beat this</span>
              ) : x.status === "beats_baseline" ? (
                <span className="text-ink-2">Beats the baseline, not the most accurate</span>
              ) : (
                <span className="inline-flex items-center gap-1 text-ink-2">
                  <CircleX className="size-4 text-muted" aria-hidden /> Worse than the baseline
                </span>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export default function Benchmark() {
  const q = useGenpact();
  const toast = useToast();
  const d = q.data;
  const r = (d?.report ?? null) as Report | null;
  const first = r?.steps[0]?.counts;
  const winner = r?.leaderboard.find((x) => x.model === r.winner);

  return (
    <div>
      <PageHeader
        title="Real-data benchmark"
        sub="The canteens in this app run on a simulated college. This page checks the same forecasting method on real data: weekly orders from a meal-delivery company, published for the Genpact Machine Learning Hackathon."
        actions={d && d.status !== "missing_files" ? <RunButton again={d.status === "ready"} /> : null}
      />
      {q.isLoading ? <Skeleton className="h-64 w-full" /> : null}
      {q.error ? <ErrorState error={q.error} retry={() => q.refetch()} /> : null}

      {d?.status === "missing_files" ? (
        <EmptyState title="The dataset is not here yet">
          Download "Food Demand Forecasting" from Kaggle and put {d.missing_files.join(", ")} in the <code>data/genpact</code> folder of the
          project (the server looks in <code>{d.data_dir}</code>). Then reload this page.
        </EmptyState>
      ) : null}
      {d?.status === "not_run" ? (
        <EmptyState title="Ready to run">The files are in place. Run the benchmark to clean the data and test the models; it takes about a minute.</EmptyState>
      ) : null}

      {r ? (
        <>
          <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[
              [fmtNum(r.rows_raw), "rows in train.csv"],
              [fmtNum(r.rows_clean), "rows after cleaning"],
              [`${fmtNum(first?.centres as number)} x ${fmtNum(first?.meals as number)}`, "centres x meals"],
              [`${first?.first_week}–${first?.last_week}`, "weeks of history"],
            ].map(([big, small]) => (
              <div key={small} className="rim px-5 py-4">
                <p className="num text-3xl font-[680] text-ink">{big}</p>
                <p className="mt-1 text-sm text-muted">{small}</p>
              </div>
            ))}
          </div>
          <p className="mb-8 rounded-[8px] bg-haldi-soft px-4 py-3 text-base text-haldi-deep">
            <strong>{r.dataset.name}.</strong> {r.dataset.description} Source: {r.dataset.source}
          </p>

          <Section
            title="Cleaning, step by step"
            sub="Each step reports what it found in the files and what was done about it."
            actions={
              <Button
                variant="ghost"
                icon={<Download className="size-4" />}
                onClick={async () => {
                  try {
                    await download("/api/v1/benchmark/genpact/clean.csv", "genpact-clean.csv");
                  } catch {
                    toast("error", "The file could not be downloaded.");
                  }
                }}
              >
                Cleaned data (CSV)
              </Button>
            }
          >
            <ol className="flex flex-col gap-3">
              {r.steps.map((s) => (
                <StepCard key={s.key} step={s} />
              ))}
            </ol>
          </Section>

          <Section
            title="Which model forecasts best"
            sub={`Forecasting ${r.horizon}. Error is WAPE over ${fmtNum(r.tested_rows)} test rows (lower is better); RMSLE is the competition's metric.`}
          >
            {winner ? (
              <div className="rim mb-4 grid grid-cols-3 gap-4 px-5 py-4">
                <Figure value={fmtPct(winner.wape, 1)} label={`${winner.label} error`} />
                <Figure value={fmtPct(winner.skill, 0, true)} label="Better than the best baseline" tone="leaf" />
                <Figure value={winner.beats_baseline_every_fold ? "Every fold" : "Not every fold"} label="Beats the baseline" />
              </div>
            ) : null}
            <Results r={r} />
            <div className="mt-4 overflow-x-auto">
              <table className="min-w-full text-sm">
                <caption className="mb-1 text-left text-sm text-muted">Walk-forward folds: each block is predicted by models trained only on earlier weeks</caption>
                <thead className="text-left text-ink-2">
                  <tr>
                    <th className="px-2 py-1 font-[620]">Fold</th>
                    <th className="px-2 py-1 font-[620]">Trained on weeks</th>
                    <th className="px-2 py-1 font-[620]">Tested on weeks</th>
                    <th className="px-2 py-1 text-right font-[620]">Training rows</th>
                    <th className="px-2 py-1 text-right font-[620]">Test rows</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-rim">
                  {r.folds.map((f) => (
                    <tr key={f.fold}>
                      <td className="num px-2 py-1">{f.fold}</td>
                      <td className="num px-2 py-1">
                        {f.train_weeks[0]}–{f.train_weeks[1]}
                      </td>
                      <td className="num px-2 py-1">
                        {f.test_weeks[0]}–{f.test_weeks[1]}
                      </td>
                      <td className="num px-2 py-1 text-right">{fmtNum(f.train_rows)}</td>
                      <td className="num px-2 py-1 text-right">{fmtNum(f.test_rows)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Limits of this benchmark">
            <ul className="list-disc space-y-1 pl-5 text-base text-ink-2">
              {r.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
            <p className="mt-4 text-sm text-muted">
              Run {fmtDateTime(r.generated_at)} in {r.duration_seconds} s with{" "}
              {Object.entries(r.library_versions)
                .map(([k, v]) => `${k} ${v}`)
                .join(", ")}
              .
            </p>
          </Section>
        </>
      ) : null}
    </div>
  );
}

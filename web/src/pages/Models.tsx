import { CheckCircle2, CircleX, Loader2, Play } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { ApiError } from "@/api/client";
import { useJob, useModel, useModels, useTrain } from "@/api/hooks";
import { useAuth, useCanteen } from "@/app/auth";
import { fmtDateTime, fmtNum, fmtPct } from "@/lib/format";
import { useToast } from "@/ui/overlays";
import { Badge, Button, EmptyState, ErrorState, Figure, Meter, PageHeader, Section, Skeleton, cx } from "@/ui/primitives";

export const STAGE: Record<string, { label: string; tone: "leaf" | "haldi" | "neutral" | "chilli" | "rain" }> = {
  champion: { label: "Champion", tone: "leaf" },
  challenger: { label: "Challenger", tone: "rain" },
  retired: { label: "Retired", tone: "neutral" },
  rejected: { label: "Rejected", tone: "neutral" },
  degraded: { label: "Degraded", tone: "chilli" },
};

type Entry = {
  algorithm: string;
  label: string;
  family: string;
  status: string;
  metrics: { wape?: number; coverage80?: number; bias?: number };
  gates: { gate: string; passed: boolean; detail: string }[];
  error?: string | null;
  members?: string[] | null;
  weights?: number[] | null;
};

const GATE_LABEL: Record<string, string> = {
  beats_baseline: "Beats the best baseline",
  beats_champion: "Beats the current champion",
  calibrated: "Ranges are calibrated",
  unbiased: "Not biased",
  cheaper: "Lower simulated cost",
  enough_history: "Enough history",
};

const byWape = (a: Entry, b: Entry) => (a.metrics.wape ?? Infinity) - (b.metrics.wape ?? Infinity);

/** Models kept on the summary as a fixed point of comparison, whether or not they win. */
const ALWAYS_SHOWN = ["ridge"];

/** The winner (or, if nothing was selected, the most accurate learned model), the fixed
 * comparison models, and the best baseline. Everything else lives in the full report. */
function headline(entries: Entry[]): Entry[] {
  const scored = entries.filter((e) => e.metrics.wape != null);
  const learned = scored.filter((e) => e.family !== "baseline").sort(byWape);
  const baseline = scored.filter((e) => e.family === "baseline").sort(byWape)[0];
  const pick = entries.find((e) => e.status === "selected") ?? learned[0];
  const fixed = entries.filter((e) => ALWAYS_SHOWN.includes(e.algorithm) && e !== pick);
  return [pick, ...fixed, baseline].filter((e): e is Entry => e != null);
}

export function Leaderboard({ entries, moreHref }: { entries: Entry[]; moreHref?: string }) {
  const shown = moreHref ? headline(entries) : entries;
  const hidden = entries.length - shown.length;
  const scored = shown.filter((e) => e.metrics.wape != null);
  const max = Math.max(...scored.map((e) => e.metrics.wape ?? 0), 0.01);
  return (
    <ul className="rim divide-y divide-rim">
      {shown.map((e) => {
        const failed = e.gates.filter((g) => !g.passed);
        const selected = e.status === "selected";
        return (
          <li key={e.algorithm} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 px-5 py-3 md:grid-cols-[minmax(180px,1fr)_minmax(140px,1.4fr)_70px_minmax(140px,1fr)]">
            <div className="min-w-0">
              <p className={cx("truncate", selected ? "font-[650]" : "font-medium")}>{e.label}</p>
              <p className="text-sm text-muted">
                {e.family === "baseline" ? "Baseline" : e.family.replace("_", "-")}
                {e.members?.length ? `: ${e.members.map((m, i) => `${m.replace("_", " ")} ${fmtPct(e.weights?.[i] ?? 0)}`).join(", ")}` : ""}
              </p>
            </div>
            <div className="order-3 col-span-2 md:order-none md:col-span-1" aria-hidden>
              {e.metrics.wape != null ? (
                <div className="h-2.5 rounded-r-[4px]" style={{ width: `${(e.metrics.wape / max) * 100}%`, background: selected ? "#3E6DB0" : "#A3B0AC" }} />
              ) : null}
            </div>
            <p className="num text-right font-medium">{e.metrics.wape != null ? fmtPct(e.metrics.wape, 1) : "–"}</p>
            <div className="order-4 col-span-2 text-sm md:order-none md:col-span-1">
              {selected ? (
                <span className="inline-flex items-center gap-1 text-leaf">
                  <CheckCircle2 className="size-4" aria-hidden /> Selected, passed every gate
                </span>
              ) : e.status === "baseline" ? (
                <span className="text-muted">Every model must beat this</span>
              ) : e.status === "passed" ? (
                <span className="text-ink-2">Passed, not the most accurate</span>
              ) : e.status === "skipped" || e.status === "failed" ? (
                <span className="text-muted">{e.error ?? "Did not finish"}</span>
              ) : (
                <span className="inline-flex items-start gap-1 text-ink-2" title={failed.map((g) => g.detail).join("; ")}>
                  <CircleX className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden />
                  Failed: {failed.map((g) => (GATE_LABEL[g.gate] ?? g.gate).toLowerCase()).slice(0, 2).join(", ")}
                </span>
              )}
            </div>
          </li>
        );
      })}
      {moreHref && hidden > 0 ? (
        <li className="px-5 py-3 text-sm text-muted">
          {hidden} other {hidden === 1 ? "model was" : "models were"} tested and did not win.{" "}
          <Link to={moreHref} className="text-ink-2 underline decoration-rim-strong underline-offset-4 hover:text-ink">
            See all in the full report
          </Link>
        </li>
      ) : null}
    </ul>
  );
}

function TrainButton() {
  const { canteen } = useCanteen();
  const { can } = useAuth();
  const train = useTrain();
  const toast = useToast();
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const status = job.data?.status;
  const progress = job.data?.progress as { label?: string; fraction?: number } | null | undefined;

  useEffect(() => {
    if (!jobId || !status) return;
    if (status === "succeeded") {
      const r = job.data?.result as { stage?: string; algorithm?: string; version?: number } | null;
      qc.invalidateQueries({ queryKey: ["models"] });
      qc.invalidateQueries({ queryKey: ["model"] });
      toast(
        "ok",
        r?.stage === "challenger"
          ? `v${r.version} (${r.algorithm}) passed the gates and starts its shadow week.`
          : r?.stage === "champion"
            ? `v${r?.version} is the new champion.`
            : `v${r?.version} did not pass the promotion gates, so the champion stays.`,
      );
      setJobId(null);
    } else if (status === "dead" || status === "failed") {
      toast("error", job.data?.last_error ?? "Training failed.");
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
        <Meter value={progress?.fraction ?? 0.02} tone="ink" label="Training progress" />
      </div>
    );
  }
  return (
    <Button
      icon={<Play className="size-4" />}
      busy={train.isPending}
      onClick={async () => {
        try {
          const ref = await train.mutateAsync(canteen!.id);
          setJobId(ref.job_id);
        } catch (e) {
          toast("error", e instanceof ApiError ? e.message : "Could not start training.");
        }
      }}
    >
      Retrain now
    </Button>
  );
}

export default function Models() {
  const { canteen } = useCanteen();
  const models = useModels(canteen?.id);
  const champ = models.data?.find((m) => m.stage === "champion" || m.stage === "degraded");
  const challenger = models.data?.find((m) => m.stage === "challenger");
  const latest = models.data?.[0];
  const detail = useModel(latest?.id);
  const champDetail = useModel(champ?.id);
  const mon = (champDetail.data?.monitoring ?? {}) as { live_wape?: number; live_coverage80?: number; alerts?: string[]; shortage_observed?: number; shortage_target?: number };
  return (
    <div>
      <PageHeader
        title="Forecast models"
        sub="Every week FoodWise backtests a zoo of models on this canteen's own history and keeps whichever wins honestly. A new winner must also beat the champion live, on days it has never seen, before it takes over."
        actions={<TrainButton />}
      />
      {models.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : models.error ? (
        <ErrorState error={models.error} retry={() => models.refetch()} />
      ) : !champ ? (
        <EmptyState title="No model yet">Import history or log a few days of service, then train the first model.</EmptyState>
      ) : (
        <>
          <section className="rim mb-8 grid gap-6 px-5 py-5 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]" aria-label="Champion">
            <div>
              <div className="flex items-center gap-2">
                <Badge tone={STAGE[champ.stage].tone}>{STAGE[champ.stage].label}</Badge>
                <span className="text-sm text-muted">since {fmtDateTime(champ.promoted_at)}</span>
              </div>
              <h2 className="mt-2 text-2xl font-[680]" style={{ fontStretch: "114%" }}>
                <Link to={`/models/${champ.id}`} className="hover:underline">
                  {champ.label} v{champ.version}
                </Link>
              </h2>
              <div className="mt-4 grid grid-cols-3 gap-4">
                <Figure value={champ.wape != null ? fmtPct(champ.wape, 1) : "–"} label="Backtest error" />
                <Figure value={mon.live_wape != null ? fmtPct(mon.live_wape, 1) : "–"} label="Live error, 4 weeks" />
                <Figure value={champ.skill != null ? fmtPct(champ.skill, 0, true) : "–"} label="Better than the best baseline" tone="leaf" />
              </div>
            </div>
            <div className="flex flex-col gap-3 md:border-l md:border-rim md:pl-6">
              {challenger ? (
                <div>
                  <p className="text-sm text-muted">Challenger in its shadow week</p>
                  <p className="font-[620]">
                    <Link to={`/models/${challenger.id}`} className="hover:underline">
                      {challenger.label} v{challenger.version}
                    </Link>
                  </p>
                  <p className="text-sm text-ink-2">
                    {fmtNum((challenger.shadow as { days_observed?: number } | null)?.days_observed ?? 0)} of {fmtNum((challenger.shadow as { days?: number } | null)?.days ?? 7)} days observed. It predicts silently; the kitchen still cooks the champion's plan.
                  </p>
                </div>
              ) : (
                <p className="text-sm text-muted">No challenger right now. The next weekly retrain may produce one.</p>
              )}
              {mon.alerts?.length ? (
                <ul className="flex flex-col gap-1.5 text-sm text-ink-2">
                  {mon.alerts.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          </section>

          {detail.data ? (
            <Section
              title={`Latest training run, v${detail.data.version}`}
              sub={`${fmtDateTime(detail.data.created_at)}, ${detail.data.data_window.days as number} days of history, ${(detail.data.data_window.folds as unknown[]).length} walk-forward folds, ${fmtNum(detail.data.training_seconds)} s. Error is WAPE on the folds (lower is better).`}
              actions={
                <Link to={`/models/${detail.data.id}`} className="text-sm text-ink-2 underline decoration-rim-strong underline-offset-4 hover:text-ink">
                  Full report
                </Link>
              }
            >
              <Leaderboard entries={detail.data.leaderboard as Entry[]} moreHref={`/models/${detail.data.id}`} />
            </Section>
          ) : null}

          <Section title="All versions">
            <div className="rim overflow-x-auto">
              <table className="w-full min-w-[640px] text-base">
                <thead>
                  <tr className="border-b border-rim text-left text-sm text-muted">
                    <th className="px-5 py-2.5 font-medium">Version</th>
                    <th className="px-3 py-2.5 font-medium">Model</th>
                    <th className="px-3 py-2.5 font-medium">Stage</th>
                    <th className="px-3 py-2.5 text-right font-medium">Error</th>
                    <th className="px-3 py-2.5 text-right font-medium">80% range covered</th>
                    <th className="px-5 py-2.5 font-medium">Trained</th>
                  </tr>
                </thead>
                <tbody>
                  {models.data!.map((m) => (
                    <tr key={m.id} className="border-b border-steel-2 last:border-0 hover:bg-white">
                      <td className="px-5 py-3">
                        <Link to={`/models/${m.id}`} className="font-medium underline decoration-rim-strong underline-offset-4 hover:decoration-ink">
                          v{m.version}
                        </Link>
                      </td>
                      <td className="px-3 py-3">{m.label}</td>
                      <td className="px-3 py-3">
                        <Badge tone={STAGE[m.stage]?.tone ?? "neutral"}>{STAGE[m.stage]?.label ?? m.stage}</Badge>
                      </td>
                      <td className="num px-3 py-3 text-right">{m.wape != null ? fmtPct(m.wape, 1) : "–"}</td>
                      <td className="num px-3 py-3 text-right">{m.coverage80 != null ? fmtPct(m.coverage80) : "–"}</td>
                      <td className="px-5 py-3 text-sm text-ink-2">
                        {fmtDateTime(m.created_at)}
                        {m.trigger ? <span className="text-muted">, {m.trigger}</span> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </>
      )}
    </div>
  );
}

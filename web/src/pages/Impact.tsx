import { Download, FileText } from "lucide-react";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ApiError, download } from "@/api/client";
import { useImpact } from "@/api/hooks";
import { useAuth, useCanteen } from "@/app/auth";
import { addDays, fmtDay, fmtKg, fmtNum, fmtPct, fmtRupees } from "@/lib/format";
import { C, ChartFrame, LegendItem, TooltipBox, axisProps } from "@/ui/charts";
import { useToast } from "@/ui/overlays";
import { Button, ErrorState, Figure, PageHeader, Section, Select, Skeleton, cx } from "@/ui/primitives";

function Compare({ label, manual, guided, better }: { label: string; manual: number | null | undefined; guided: number | null | undefined; better: "lower" }) {
  const improved = manual != null && guided != null && (better === "lower" ? guided < manual : guided > manual);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-baseline gap-x-6 border-t border-rim py-3 first:border-t-0">
      <p className="text-base text-ink-2">{label}</p>
      <p className="num text-right text-lg text-muted">{fmtPct(manual, 1)}</p>
      <p className={cx("num text-right text-lg font-[650]", improved ? "text-leaf" : "text-ink")}>{fmtPct(guided, 1)}</p>
    </div>
  );
}

export default function Impact() {
  const { today } = useCanteen();
  const { can } = useAuth();
  const toast = useToast();
  const [days, setDays] = useState("90");
  const impact = useImpact(Number(days));
  const d = impact.data;
  const weeks = useMemo(
    () =>
      (d?.weekly ?? []).map((w) => ({
        week: fmtDay(String(w.week), { day: "numeric", month: "short" }),
        waste: Number(w.waste_pct ?? 0),
        guided: Number(w.guided_days) >= Number(w.days) / 2,
        kg: Number(w.unserved_kg),
        prepared: Number(w.prepared_kg),
      })),
    [d],
  );
  const get = async (path: string, name: string) => {
    try {
      await download(path, name);
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Download failed.");
    }
  };
  return (
    <div>
      <PageHeader
        title="Impact"
        sub="Measured on this college's own logs. Days cooked to a FoodWise plan are compared with days cooked by hand."
        actions={
          <>
            <Select value={days} onChange={(e) => setDays(e.target.value)} aria-label="Period" className="w-40">
              <option value="30">Last 30 days</option>
              <option value="90">Last 90 days</option>
              <option value="180">Last 6 months</option>
            </Select>
            {can("reports:export") ? (
              <>
                <Button icon={<FileText className="size-4" />} onClick={() => get(`/api/v1/reports/impact.pdf?days=${days}`, `foodwise-impact-${today}.pdf`)}>
                  PDF report
                </Button>
                <Button variant="ghost" icon={<Download className="size-4" />} onClick={() => get(`/api/v1/reports/logs.csv?start=${addDays(today, -Number(days))}&end=${today}`, `foodwise-logs-${today}.csv`)}>
                  Logs as CSV
                </Button>
              </>
            ) : null}
          </>
        }
      />
      {impact.isLoading ? (
        <Skeleton className="h-96 w-full" />
      ) : impact.error ? (
        <ErrorState error={impact.error} retry={() => impact.refetch()} />
      ) : (
        <>
          <section className="rim mb-8 grid gap-8 px-5 py-6 sm:px-7 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]" aria-label="Waste reduction">
            <div>
              <p className="text-base text-muted">Food left unserved, as a share of food cooked</p>
              <p className="qty mt-2 text-5xl text-leaf">{d!.reduction.point != null ? `${fmtPct(d!.reduction.point)} less` : "Not enough data yet"}</p>
              {d!.reduction.lo != null ? (
                <p className="mt-2 text-lg text-ink-2">
                  Likely between {fmtPct(d!.reduction.lo)} and {fmtPct(d!.reduction.hi)} (90% interval).
                </p>
              ) : null}
              <p className="mt-4 max-w-md text-sm text-muted">{d!.reduction.note}</p>
            </div>
            <div>
              <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-6 pb-2 text-sm text-muted">
                <span />
                <span className="text-right">By hand</span>
                <span className="text-right">With FoodWise</span>
              </div>
              <Compare label="Left unserved" manual={d!.manual.waste_pct} guided={d!.guided.waste_pct} better="lower" />
              <Compare label="Dishes that ran out" manual={d!.manual.sold_out_pct} guided={d!.guided.sold_out_pct} better="lower" />
              <div className="grid grid-cols-[minmax(0,1fr)_auto_auto] gap-x-6 border-t border-rim py-3 text-base">
                <p className="text-ink-2">Days</p>
                <p className="num text-right text-muted">{fmtNum(d!.manual.days)}</p>
                <p className="num text-right">{fmtNum(d!.guided.days)}</p>
              </div>
              <p className="mt-2 text-sm text-muted">Running out is shown alongside waste on purpose: cutting waste by starving students would not be a win.</p>
            </div>
          </section>

          <div className="mb-8 grid grid-cols-2 gap-6 md:grid-cols-3 xl:grid-cols-6">
            <Figure value={fmtKg(d!.avoided_kg)} label="Food not cooked in the first place" />
            <Figure value={fmtRupees(d!.rupees_saved)} label="Ingredient cost avoided (estimate)" />
            <Figure value={fmtKg(d!.rescue.kg)} label="Rescued for people" tone="leaf" />
            <Figure value={fmtNum(d!.meals_donated)} label="Meals donated, approximately" />
            <Figure value={fmtKg(d!.co2e_kg)} label="CO2e avoided (estimate)" />
            <Figure value={d!.rescue.rescue_rate != null ? fmtPct(d!.rescue.rescue_rate) : "–"} label={`Of ${d!.rescue.above_threshold} large surpluses picked up`} />
          </div>

          <ChartFrame
            title="Unserved food by week, all canteens"
            legend={
              <>
                <LegendItem color={C.muted} kind="bar" label="Planned by hand" />
                <LegendItem color={C.blue} kind="bar" label="Planned with FoodWise" />
              </>
            }
            table={{ columns: ["Week of", "Unserved %", "Unserved kg", "Cooked kg", "With FoodWise"], rows: weeks.map((w) => [w.week, fmtPct(w.waste, 1), fmtNum(w.kg), fmtNum(w.prepared), w.guided ? "Yes" : "No"]) }}
          >
            <ResponsiveContainer>
              <BarChart data={weeks} margin={{ top: 8, right: 4, bottom: 0, left: -12 }} barCategoryGap="28%">
                <CartesianGrid vertical={false} stroke={C.grid} />
                <XAxis dataKey="week" {...axisProps} interval="preserveStartEnd" />
                <YAxis {...axisProps} width={44} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                <Tooltip
                  cursor={{ fill: "rgb(28 42 74 / 0.05)" }}
                  content={({ active, payload }) =>
                    active && payload?.length ? (
                      <TooltipBox
                        title={`Week of ${payload[0].payload.week}`}
                        rows={[
                          { label: "Unserved", value: fmtPct(payload[0].payload.waste, 1), color: payload[0].payload.guided ? C.blue : C.muted },
                          { label: "Weight", value: fmtKg(payload[0].payload.kg) },
                        ]}
                      />
                    ) : null
                  }
                />
                <Bar dataKey="waste" radius={[4, 4, 0, 0]} maxBarSize={24}>
                  {weeks.map((w, i) => (
                    <Cell key={i} fill={w.guided ? C.blue : C.muted} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartFrame>

          <Section title="How these numbers are made" className="mt-8">
            <ul className="flex max-w-3xl list-disc flex-col gap-1.5 pl-5 text-base text-ink-2">
              <li>Waste figures use the kitchen's own logs of what was cooked and what was left. Plate waste is tracked separately and is not counted here.</li>
              <li>The interval comes from resampling days, so a few unusual days cannot manufacture a result.</li>
              <li>Rescue weights are weighed at handoff. Meals assume {"0.4"} kg per meal; emissions use the factor set in Settings ({d!.co2e_note})</li>
              <li>{d!.benchmark}</li>
            </ul>
          </Section>
        </>
      )}
    </div>
  );
}

import { AlertTriangle, CheckCircle2, ChevronRight, HandHeart } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useImpact, useIncidents, useModel, useModels, usePlan, useSheet } from "@/api/hooks";
import { useAuth, useCanteen } from "@/app/auth";
import { addDays, fmtDay, fmtKg, fmtNum, fmtPct } from "@/lib/format";
import { C, ChartFrame, LegendItem, TooltipBox, axisProps } from "@/ui/charts";
import { Countdown } from "@/ui/overlays";
import { Badge, Button, ErrorState, Meter, Skeleton } from "@/ui/primitives";

function TomorrowHero() {
  const { canteen, today } = useCanteen();
  const { can } = useAuth();
  const day = addDays(today, 1);
  const plan = usePlan(canteen?.id, day);
  if (plan.isLoading) return <Skeleton className="h-40 w-full" />;
  if (plan.error) return <ErrorState error={plan.error} retry={() => plan.refetch()} />;
  const p = plan.data!;
  const dishes = p.meals.flatMap((m) => m.dishes);
  const counts = p.counts;
  const total = dishes.length;
  const waiting = counts.proposed ?? 0;
  const kg = dishes.reduce((s, d) => s + d.plan_qty * (d.unit === "kg" || d.unit === "litres" ? 1 : 0.25), 0);
  return (
    <section className="rim overflow-hidden" aria-labelledby="tomorrow">
      <div className="grid gap-6 px-5 py-6 sm:px-7 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
        <div>
          <p className="text-base text-muted">
            Tomorrow at {canteen?.name}, {fmtDay(day, { weekday: "long", day: "numeric", month: "long" })}
          </p>
          {total ? (
            <>
              <h2 id="tomorrow" className="mt-2 text-3xl font-[700] sm:text-4xl" style={{ fontStretch: "120%" }}>
                {waiting ? (
                  <>
                    {waiting} of {total} dishes need your approval
                  </>
                ) : (
                  <>Plan approved for all {total} dishes</>
                )}
              </h2>
              <p className="mt-3 max-w-2xl text-lg text-ink-2">
                {p.meals
                  .filter((m) => m.dishes.length)
                  .map((m) => `${m.label} ${m.dishes.length}`)
                  .join(", ")}{" "}
                dishes, roughly {fmtKg(kg)} of food in all.{" "}
                {p.context.rain_prob != null && p.context.rain_prob >= 0.5 ? `Rain is likely (${fmtPct(p.context.rain_prob)}). ` : ""}
                {p.context.events.length ? `${p.context.events.join(", ")}. ` : ""}
              </p>
            </>
          ) : (
            <h2 id="tomorrow" className="mt-2 text-3xl font-[700]" style={{ fontStretch: "120%" }}>
              Tomorrow's plan arrives at the cutoff
            </h2>
          )}
          <div className="mt-4 max-w-md">
            <Meter value={total ? (total - waiting) / total : 0} tone={waiting ? "haldi" : "leaf"} label="Dishes decided" />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to={`/plan?date=${day}`}>
            <Button variant={waiting && can("forecast:decide") ? "primary" : "secondary"} size="lg">
              {waiting && can("forecast:decide") ? "Review and approve" : "Open the plan"}
            </Button>
          </Link>
        </div>
      </div>
    </section>
  );
}

function KitchenToday() {
  const { canteen } = useCanteen();
  const sheet = useSheet(canteen?.id);
  if (sheet.isLoading) return <Skeleton className="h-48 w-full" />;
  if (sheet.error) return <ErrorState error={sheet.error} retry={() => sheet.refetch()} />;
  const meals = sheet.data!.meals.filter((m) => m.dishes.length);
  return (
    <section aria-labelledby="kitchen-today">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 id="kitchen-today" className="text-lg font-[650]">
          Today in the kitchen
        </h2>
        <Link to="/kitchen" className="text-sm text-ink-2 underline decoration-rim-strong underline-offset-4 hover:text-ink">
          Open kitchen log
        </Link>
      </div>
      <ul className="rim divide-y divide-rim">
        {meals.map((m) => {
          const logged = m.dishes.filter((d) => d.prepared_qty != null);
          const soldOut = logged.filter((d) => d.sold_out).length;
          const left = logged.reduce((s, d) => s + (d.unserved_qty ?? 0) * (d.unit === "kg" || d.unit === "litres" ? 1 : 0.25), 0);
          return (
            <li key={m.key} className="flex flex-wrap items-center gap-x-6 gap-y-1 px-5 py-3.5">
              <div className="w-32">
                <p className="font-[620]">{m.label}</p>
                <p className="text-sm text-muted">
                  {m.start} to {m.end}
                </p>
              </div>
              <div className="flex-1 text-base text-ink-2">
                {m.status === "upcoming" ? (
                  <span className="text-muted">Starts at {m.start}</span>
                ) : logged.length === 0 ? (
                  <span className={m.status === "done" ? "text-haldi-deep" : "text-muted"}>
                    {m.status === "done" ? "Not logged yet" : "Serving now, nothing logged yet"}
                  </span>
                ) : (
                  <span>
                    {logged.length} of {m.dishes.length} dishes logged, about {fmtKg(left)} left over
                    {soldOut ? <span className="text-chilli">, {soldOut} ran out</span> : null}
                  </span>
                )}
              </div>
              <Badge tone={m.status === "serving" ? "haldi" : m.status === "done" ? "neutral" : "neutral"}>
                {m.status === "serving" ? "Serving now" : m.status === "done" ? "Done" : "Later"}
              </Badge>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function RescueCard() {
  const active = useIncidents("active");
  const items = active.data ?? [];
  return (
    <section aria-labelledby="rescue-now" className="rim px-5 py-4">
      <div className="flex items-center justify-between">
        <h2 id="rescue-now" className="font-[650]">
          Surplus rescue
        </h2>
        <Link to="/rescue/new" className="text-sm text-ink-2 underline decoration-rim-strong underline-offset-4 hover:text-ink">
          Report surplus
        </Link>
      </div>
      {items.length === 0 ? (
        <p className="mt-2 text-base text-muted">No leftover food waiting to be given away.</p>
      ) : (
        <ul className="mt-3 flex flex-col gap-2">
          {items.map((i) => (
            <li key={i.id}>
              <Link to={`/rescue/${i.id}`} className="flex items-center gap-3 rounded-[8px] bg-chilli-soft/50 px-3 py-2.5 hover:bg-chilli-soft">
                <HandHeart className="size-5 text-chilli" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">
                    {fmtNum(i.total_portions)} portions at {i.canteen}
                  </p>
                  <p className="text-sm text-ink-2">
                    {i.status === "arranged" ? `${i.places.join(", ")} will take it` : i.route === "divert" ? "Find an animal shelter or compost site" : "Find a place nearby"}
                    {i.route === "people" && i.safe_until ? (
                      <>
                        , safe for <Countdown until={i.safe_until} />
                      </>
                    ) : null}
                  </p>
                </div>
                <ChevronRight className="size-4 text-muted" aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ModelCard() {
  const { canteen } = useCanteen();
  const models = useModels(canteen?.id);
  const champ = models.data?.find((m) => m.stage === "champion" || m.stage === "degraded");
  const challenger = models.data?.find((m) => m.stage === "challenger");
  const detail = useModel(champ?.id);
  const mon = (detail.data?.monitoring ?? {}) as { alerts?: string[]; live_wape?: number; status?: string };
  return (
    <section className="rim px-5 py-4" aria-labelledby="model-health">
      <div className="flex items-center justify-between">
        <h2 id="model-health" className="font-[650]">
          Forecast model
        </h2>
        <Link to="/models" className="text-sm text-ink-2 underline decoration-rim-strong underline-offset-4 hover:text-ink">
          Details
        </Link>
      </div>
      {champ ? (
        <>
          <p className="mt-2 text-base">
            {champ.label} <span className="text-muted">v{champ.version}</span>
          </p>
          <p className="text-sm text-muted">
            {mon.live_wape != null ? `${fmtPct(mon.live_wape, 1)} error over the last 4 weeks` : champ.wape != null ? `${fmtPct(champ.wape, 1)} backtest error` : "Rule-based while learning"}
            {challenger ? `. Challenger v${challenger.version} is in its shadow week.` : ""}
          </p>
          {mon.alerts?.length ? (
            <ul className="mt-3 flex flex-col gap-2">
              {mon.alerts.map((a) => (
                <li key={a} className="flex gap-2 text-sm text-ink-2">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0 text-haldi-deep" aria-hidden />
                  {a}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-3 flex items-center gap-2 text-sm text-leaf">
              <CheckCircle2 className="size-4" aria-hidden /> No drift or calibration alerts
            </p>
          )}
        </>
      ) : (
        <p className="mt-2 text-base text-muted">No model yet.</p>
      )}
    </section>
  );
}

function WasteTrend() {
  const { canteen } = useCanteen();
  const impact = useImpact(70, canteen?.id);
  const weeks = useMemo(
    () =>
      (impact.data?.weekly ?? []).map((w) => ({
        week: fmtDay(String(w.week), { day: "numeric", month: "short" }),
        waste: Number(w.waste_pct ?? 0),
        guided: Number(w.guided_days) >= Number(w.days) / 2,
        kg: Number(w.unserved_kg),
      })),
    [impact.data],
  );
  if (impact.isLoading) return <Skeleton className="h-72 w-full" />;
  if (impact.error) return <ErrorState error={impact.error} retry={() => impact.refetch()} />;
  const r = impact.data!.reduction;
  return (
    <ChartFrame
      title="Food left unserved, by week"
      sub={
        r.point != null
          ? `Cooking to FoodWise plans cut unserved food by ${fmtPct(r.point)} (likely ${fmtPct(r.lo)} to ${fmtPct(r.hi)}) on this canteen's own logs.`
          : r.note
      }
      legend={
        <>
          <LegendItem color={C.muted} kind="bar" label="Planned by hand" />
          <LegendItem color={C.blue} kind="bar" label="Planned with FoodWise" />
        </>
      }
      table={{ columns: ["Week of", "Unserved, % of cooked", "Unserved kg", "Planned with FoodWise"], rows: weeks.map((w) => [w.week, fmtPct(w.waste, 1), fmtNum(w.kg), w.guided ? "Yes" : "No"]) }}
    >
      <ResponsiveContainer>
        <BarChart data={weeks} margin={{ top: 8, right: 4, bottom: 0, left: -12 }} barCategoryGap="28%">
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="week" {...axisProps} interval="preserveStartEnd" />
          <YAxis {...axisProps} tickFormatter={(v) => `${Math.round(v * 100)}%`} width={44} />
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
  );
}

export default function Overview() {
  const { canteen, today } = useCanteen();
  return (
    <div className="flex flex-col gap-8">
      <header>
        <p className="text-base text-muted">{fmtDay(today, { weekday: "long", day: "numeric", month: "long" })}</p>
        <h1 className="text-3xl font-[700]" style={{ fontStretch: "118%" }}>
          {canteen?.name}
        </h1>
      </header>
      <TomorrowHero />
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-8">
          <KitchenToday />
          <WasteTrend />
        </div>
        <div className="flex flex-col gap-4">
          <RescueCard />
          <ModelCard />
        </div>
      </div>
    </div>
  );
}

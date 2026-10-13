import { Check, ChevronLeft, ChevronRight, CloudRain, Pencil, RefreshCw, Sun, Undo2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";

import { ApiError, type Schemas } from "@/api/client";
import { useApproveAll, useDecide, useGeneratePlan, usePlan } from "@/api/hooks";
import { useAuth, useCanteen } from "@/app/auth";
import { expectedWasteShortage, percentileName } from "@/lib/dist";
import { addDays, dayDiff, fmtDateTime, fmtDay, fmtNum, fmtPct, fmtQty, relDay, unitLabel } from "@/lib/format";
import { DemandShape, ShapeLegend } from "@/ui/DemandShape";
import { Dialog, useToast } from "@/ui/overlays";
import { Badge, Button, EmptyState, ErrorState, Field, Input, Skeleton, Textarea, cx } from "@/ui/primitives";
import { DishPhoto, useDishLookup } from "@/ui/DishPhoto";

type Row = Schemas["ForecastRow"];
type PlanT = Schemas["Plan"];

const QUICK_REASONS = ["Guest lecture or event", "Students away", "Last time this ran out", "Menu change"];

function Drivers({ row }: { row: Row }) {
  if (!row.drivers.length) {
    return row.kind === "model" ? null : <p className="text-sm text-muted">Based on recent servings</p>;
  }
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Why this number">
      {row.drivers.map((d) => (
        <li key={d.key} className="inline-flex items-center gap-1.5 rounded-[6px] bg-steel-2 px-2 py-0.5 text-sm text-ink-2">
          <span>{d.label}</span>
          <span className={cx("num font-[620]", d.effect > 0 ? "text-haldi-deep" : "text-rain")}>
            {d.effect > 0 ? "+" : "−"}
            {fmtPct(Math.abs(d.effect))}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ChangeDialog({ row, onClose }: { row: Row; onClose: () => void }) {
  const decide = useDecide();
  const toast = useToast();
  const [qty, setQty] = useState(String(row.plan_qty));
  const [reason, setReason] = useState(row.override_reason ?? "");
  const n = Number(qty);
  const valid = qty !== "" && Number.isFinite(n) && n >= 0;
  const effect = valid ? expectedWasteShortage(row.levels, row.quantiles, n) : null;
  const rec = expectedWasteShortage(row.levels, row.quantiles, row.recommended);
  const save = async () => {
    try {
      await decide.mutateAsync({ id: row.id, body: { action: "override", qty: n, reason } });
      toast("ok", `${row.dish}: cooking ${fmtQty(n, row.unit)}.`);
      onClose();
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not save the change.");
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Change ${row.dish}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save} busy={decide.isPending} disabled={!valid || !reason.trim()}>
            Save change
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <div className="h-14">
          <DemandShape levels={row.levels} quantiles={row.quantiles} recommended={row.recommended} override={valid && n !== row.recommended ? n : null} unit={row.unit} animate={false} />
        </div>
        <Field label={`Quantity to cook (${unitLabel(row.unit)})`} htmlFor="qty" hint={`FoodWise recommends ${fmtQty(row.recommended, row.unit)}.`}>
          <Input id="qty" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} className="qty h-12 text-xl" />
        </Field>
        {effect ? (
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="rim-sunken px-3 py-2">
              <p className="text-muted">Expected left over</p>
              <p className="num text-base font-medium">
                {fmtQty(effect.waste, row.unit)} <span className="text-muted">vs {fmtNum(rec.waste, 1)}</span>
              </p>
            </div>
            <div className="rim-sunken px-3 py-2">
              <p className="text-muted">Chance of running short</p>
              <p className={cx("num text-base font-medium", effect.pShort > 0.25 && "text-chilli")}>
                {fmtPct(effect.pShort)} <span className="text-muted">vs {fmtPct(rec.pShort)}</span>
              </p>
            </div>
          </div>
        ) : null}
        <Field label="Why are you changing it?" htmlFor="reason" hint="FoodWise tracks whether changes like this one help, and reports it back.">
          <Textarea id="reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} rows={2} />
        </Field>
        <div className="-mt-2 flex flex-wrap gap-1.5">
          {QUICK_REASONS.map((r) => (
            <button key={r} type="button" onClick={() => setReason(r)} className="rounded-full border border-rim bg-plate px-3 py-1 text-sm text-ink-2 hover:border-rim-strong">
              {r}
            </button>
          ))}
        </div>
      </div>
    </Dialog>
  );
}

function DishRow({ row, index, past, canDecide, onChange }: { row: Row; index: number; past: boolean; canDecide: boolean; onChange: () => void }) {
  const decide = useDecide();
  const toast = useToast();
  const approve = async (action: "approve" | "reset") => {
    try {
      await decide.mutateAsync({ id: row.id, body: { action } });
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not save.");
    }
  };
  const lastWeek = (row.context as { last_week?: number | null }).last_week ?? null;
  const photo = useDishLookup()(row.dish_id, row.dish);
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-3 border-t border-rim px-4 py-4 first:border-t-0 sm:px-5 md:grid-cols-[minmax(160px,0.9fr)_minmax(200px,1.5fr)_140px_auto] md:items-center">
      <div className="flex min-w-0 gap-3">
        <DishPhoto dish={photo} size="md" showDiet className="mt-0.5" />
        <div className="min-w-0">
        <p className="font-[620] text-ink">
          {row.dish}
          {row.dish_hi ? (
            <span lang="hi" className="ml-2 text-sm font-normal text-muted">
              {row.dish_hi}
            </span>
          ) : null}
        </p>
        <div className="mt-1.5">
          <Drivers row={row} />
        </div>
        </div>
      </div>

      <div className="text-right md:order-3">
        <p className={cx("qty text-[1.9rem]", row.status === "overridden" ? "text-ink" : "text-ink")}>
          {fmtNum(row.plan_qty, row.unit === "kg" || row.unit === "litres" ? 1 : 0)}
        </p>
        <p className="mt-1 text-sm text-muted">{unitLabel(row.unit, row.plan_qty)}</p>
      </div>

      <div className="col-span-2 md:order-2 md:col-span-1">
        <div className="h-[46px]">
          <DemandShape
            levels={row.levels}
            quantiles={row.quantiles}
            recommended={row.recommended}
            override={row.status === "overridden" ? row.override_qty : null}
            lastWeek={lastWeek}
            actual={row.actual ? row.actual.served : null}
            soldOut={row.actual?.sold_out}
            unit={row.unit}
            delay={index * 45}
          />
        </div>
        <p className="mt-1 text-sm text-muted">
          Likely {fmtNum(row.p10)} to {fmtNum(row.p90)}
          {lastWeek != null ? `, last week ${fmtNum(lastWeek)}` : ""}
          {row.actual ? (
            <span className={row.actual.sold_out ? "text-chilli" : "text-leaf"}>
              {row.actual.sold_out ? `. Ran out at ${fmtNum(row.actual.served)}` : `. Served ${fmtNum(row.actual.served)}, ${fmtNum(row.actual.unserved)} left`}
            </span>
          ) : null}
        </p>
      </div>

      <div className="col-span-2 flex flex-wrap items-center justify-between gap-2 md:order-4 md:col-span-1 md:justify-end">
        <div className="text-sm md:hidden">{statusText(row)}</div>
        {canDecide && !past ? (
          <div className="flex items-center gap-1.5">
            {row.status === "proposed" ? (
              <Button size="sm" variant="leaf" onClick={() => approve("approve")} busy={decide.isPending} icon={<Check className="size-4" />}>
                Approve
              </Button>
            ) : (
              <Button size="sm" variant="ghost" onClick={() => approve("reset")} busy={decide.isPending} icon={<Undo2 className="size-4" />} title="Undo the decision">
                Undo
              </Button>
            )}
            <Button size="sm" onClick={onChange} icon={<Pencil className="size-3.5" />}>
              Change
            </Button>
          </div>
        ) : null}
        <div className="hidden w-full text-right text-sm md:block">{statusText(row)}</div>
      </div>
    </li>
  );
}

function statusText(row: Row) {
  if (row.status === "approved")
    return (
      <span className="inline-flex items-center gap-1 text-leaf">
        <Check className="size-3.5" aria-hidden /> Approved{row.decided_by ? ` by ${row.decided_by.split(" ")[0]}` : ""}
      </span>
    );
  if (row.status === "overridden")
    return (
      <span className="text-ink-2" title={row.override_reason ?? undefined}>
        Changed from {fmtNum(row.recommended)}
        {row.override_reason ? <span className="text-muted">: {row.override_reason}</span> : null}
      </span>
    );
  if (row.changed_after_approval) return <span className="text-haldi-deep">Forecast moved since approval</span>;
  return null;
}

function ContextStrip({ plan }: { plan: PlanT }) {
  const c = plan.context;
  const rainy = (c.rain_prob ?? 0) >= 0.5;
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-base text-ink-2">
      {c.rain_prob != null ? (
        <span className="inline-flex items-center gap-1.5">
          {rainy ? <CloudRain className="size-4.5 text-rain" aria-hidden /> : <Sun className="size-4.5 text-haldi-deep" aria-hidden />}
          <span className="num">{fmtPct(c.rain_prob)}</span> chance of rain, <span className="num">{fmtNum(c.temp_c)}°C</span>
          {c.weather_source === "simulated" ? <span className="text-muted">(demo weather)</span> : null}
        </span>
      ) : null}
      {c.events.map((e) => (
        <Badge key={e} tone="haldi">
          {e}
        </Badge>
      ))}
      {plan.model ? (
        <Link to={plan.model.id ? `/models/${plan.model.id}` : "/models"} className="underline decoration-rim-strong underline-offset-4 hover:decoration-ink">
          {plan.model.kind === "model"
            ? `${plan.model.label} v${plan.model.version}${plan.model.wape != null ? `, ${fmtPct(plan.model.wape, 1)} backtest error` : ""}`
            : plan.model.kind === "prior"
              ? "Rule-based plan while FoodWise learns this canteen"
              : "Statistical baseline (the model is unavailable)"}
        </Link>
      ) : null}
      {plan.critical_ratio != null ? (
        <Link to="/settings?tab=service" className="text-muted underline decoration-rim-strong underline-offset-4 hover:text-ink">
          Cooks at the {percentileName(plan.critical_ratio)}
        </Link>
      ) : null}
    </div>
  );
}

export default function Plan() {
  const { canteen, today } = useCanteen();
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const day = params.get("date") ?? addDays(today, 1);
  const plan = usePlan(canteen?.id, day);
  const generate = useGeneratePlan();
  const approveAll = useApproveAll();
  const toast = useToast();
  const [editing, setEditing] = useState<Row | null>(null);
  const past = dayDiff(day, today) < 0;
  const canDecide = can("forecast:decide");
  const counts = plan.data?.counts ?? {};
  const total = (counts.proposed ?? 0) + (counts.approved ?? 0) + (counts.overridden ?? 0);
  const decided = total - (counts.proposed ?? 0);

  const setDay = (d: string) => setParams((p) => {
    p.set("date", d);
    return p;
  });

  useEffect(() => {
    document.title = `Prep plan, ${relDay(day, today)} | FoodWise`;
  }, [day, today]);

  const runGenerate = async () => {
    if (!canteen) return;
    try {
      await generate.mutateAsync({ canteen_id: canteen.id, date: day });
      toast("ok", "Forecast refreshed with the latest logs, RSVPs and weather.");
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not generate the plan.");
    }
  };
  const runApproveAll = async (meal?: string) => {
    if (!canteen) return;
    try {
      const out = await approveAll.mutateAsync({ canteen_id: canteen.id, date: day, meal: meal ?? null });
      toast("ok", meal ? `${meal} approved.` : `Plan approved for ${relDay(day, today).toLowerCase()}. The kitchen sheet is updated.`);
      return out;
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not approve.");
    }
  };

  const meals = useMemo(() => plan.data?.meals.filter((m) => m.dishes.length) ?? [], [plan.data]);
  let rowIndex = 0;

  return (
    <div>
      <header className="mb-6 flex flex-col gap-4">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-base text-muted">{canteen?.name} prep plan</p>
            <div className="mt-1 flex items-center gap-2">
              <button onClick={() => setDay(addDays(day, -1))} className="rounded-md p-1.5 text-ink-2 hover:bg-steel-2" aria-label="Previous day">
                <ChevronLeft className="size-5" />
              </button>
              <h1 className="text-3xl font-[700]" style={{ fontStretch: "118%" }}>
                {relDay(day, today)}
                {dayDiff(day, today) >= -1 && dayDiff(day, today) <= 1 ? <span className="ml-3 text-xl font-[520] text-muted">{fmtDay(day)}</span> : null}
              </h1>
              <button onClick={() => setDay(addDays(day, 1))} className="rounded-md p-1.5 text-ink-2 hover:bg-steel-2" aria-label="Next day">
                <ChevronRight className="size-5" />
              </button>
              <input
                type="date"
                aria-label="Pick a date"
                value={day}
                onChange={(e) => e.target.value && setDay(e.target.value)}
                className="ml-1 h-9 rounded-md border border-rim bg-plate px-2 text-sm text-ink-2"
              />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {can("forecast:generate") && !past ? (
              <Button onClick={runGenerate} busy={generate.isPending} icon={<RefreshCw className="size-4" />}>
                Refresh forecast
              </Button>
            ) : null}
            {canDecide && !past && (counts.proposed ?? 0) > 0 ? (
              <Button variant="primary" onClick={() => runApproveAll()} busy={approveAll.isPending} icon={<Check className="size-4" />}>
                Approve {counts.proposed} {counts.proposed === 1 ? "dish" : "dishes"}
              </Button>
            ) : null}
          </div>
        </div>
        {plan.data && total ? <ContextStrip plan={plan.data} /> : null}
        {plan.data && total ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <ShapeLegend />
            <p className="text-sm text-muted">
              <span className="num">{decided}</span> of <span className="num">{total}</span> decided
              {plan.data.generated_at ? `, forecast ${fmtDateTime(plan.data.generated_at)}` : ""}
            </p>
          </div>
        ) : null}
      </header>

      {plan.isLoading ? (
        <div className="flex flex-col gap-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : plan.error ? (
        <ErrorState error={plan.error} retry={() => plan.refetch()} />
      ) : !meals.length ? (
        <EmptyState
          title={past ? "There was no plan for this day" : "No plan for this day yet"}
          action={
            !past && can("forecast:generate") ? (
              <Button variant="primary" onClick={runGenerate} busy={generate.isPending}>
                Generate the plan
              </Button>
            ) : null
          }
        >
          {past
            ? "Plans are kept from the day FoodWise started forecasting for this canteen."
            : "FoodWise creates tomorrow's plan automatically at the canteen's cutoff time. You can also generate it now if the menu is set."}
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-6">
          {meals.map((meal) => {
            const proposed = meal.dishes.filter((d) => d.status === "proposed").length;
            return (
              <section key={meal.key} aria-labelledby={`meal-${meal.key}`}>
                <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2 px-1">
                  <h2 id={`meal-${meal.key}`} className="text-xl font-[660]" style={{ fontStretch: "112%" }}>
                    {meal.label}
                    <span className="ml-3 text-base font-normal text-muted">
                      {meal.start} to {meal.end}
                    </span>
                  </h2>
                  {canDecide && !past && proposed > 0 ? (
                    <Button size="sm" variant="ghost" onClick={() => runApproveAll(meal.key)} icon={<Check className="size-4" />}>
                      Approve {meal.label.toLowerCase()}
                    </Button>
                  ) : (
                    <span className="text-sm text-muted">{proposed === 0 ? "All decided" : `${proposed} waiting`}</span>
                  )}
                </div>
                <ul className="rim">
                  {meal.dishes.map((row) => (
                    <DishRow key={row.id} row={row} index={rowIndex++} past={past} canDecide={canDecide} onChange={() => setEditing(row)} />
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}
      {editing ? <ChangeDialog row={editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

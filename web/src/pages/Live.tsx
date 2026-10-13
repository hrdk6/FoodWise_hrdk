import { Activity, HandHeart } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { ApiError } from "@/api/client";
import { useCheckpoint, useLive, useSheet, useStartLive } from "@/api/hooks";
import { useAuth, useCanteen } from "@/app/auth";
import { fmtNum, fmtTime, unitLabel } from "@/lib/format";
import { useToast } from "@/ui/overlays";
import { Button, EmptyState, ErrorState, Meter, PageHeader, Segmented, Skeleton, cx } from "@/ui/primitives";
import { DishPhoto, useDishLookup } from "@/ui/DishPhoto";

export default function Live() {
  const { canteen } = useCanteen();
  const { can } = useAuth();
  const live = useLive(canteen?.id);
  const photo = useDishLookup();
  const sheet = useSheet(canteen?.id);
  const start = useStartLive();
  const checkpoint = useCheckpoint();
  const toast = useToast();
  const meals = sheet.data?.meals.filter((m) => m.dishes.length) ?? [];
  const [meal, setMeal] = useState("");
  const [remaining, setRemaining] = useState<Record<string, string>>({});
  const s = live.data;

  useEffect(() => {
    if (!meal && meals.length) setMeal((meals.find((m) => m.status === "serving") ?? meals.find((m) => m.status === "upcoming") ?? meals[0]).key);
  }, [meals, meal]);
  useEffect(() => {
    if (s) setRemaining(Object.fromEntries(s.dishes.map((d) => [d.dish_id, d.remaining != null ? String(d.remaining) : ""])));
  }, [s]);

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    try {
      await fn();
      if (ok) toast("ok", ok);
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "That didn't work.");
    }
  };

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title="Live service"
        sub="Log what's left in the pans a few times during service. FoodWise projects where the meal will end up, so you can cook another batch or line up a rescue before service ends."
      />
      {live.isLoading || sheet.isLoading ? (
        <Skeleton className="h-80 w-full" />
      ) : live.error ? (
        <ErrorState error={live.error} retry={() => live.refetch()} />
      ) : !s ? (
        <EmptyState
          title="Not tracking a meal yet"
          action={
            can("logs:write") ? (
              <div className="flex flex-wrap items-center gap-3">
                <Segmented label="Meal" value={meal} onChange={setMeal} options={meals.map((m) => ({ value: m.key, label: m.label }))} />
                <Button variant="primary" icon={<Activity className="size-4" />} busy={start.isPending} onClick={() => run(() => start.mutateAsync({ canteen_id: canteen!.id, meal }), "Tracking started.")}>
                  Start tracking
                </Button>
              </div>
            ) : null
          }
        >
          Tracking uses today's approved plan as the starting quantities.
        </EmptyState>
      ) : (
        <>
          <section className="rim mb-6 px-5 py-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-xl font-[660]">{s.meal_label}</h2>
              <p className="text-sm text-muted">
                {fmtTime(s.started_at)} to {fmtTime(s.ends_at)}, {s.checkpoints} {s.checkpoints === 1 ? "check" : "checks"} logged
              </p>
            </div>
            <div className="mt-3">
              <Meter value={s.elapsed} tone="ink" label="Service elapsed" />
            </div>
            {s.surplus_warning ? (
              <div role="alert" className="mt-4 flex flex-wrap items-center gap-3 rounded-[10px] bg-chilli-soft px-4 py-3">
                <HandHeart className="size-5 text-chilli" aria-hidden />
                <p className="flex-1 text-base">{s.surplus_warning}</p>
                <Link to={`/rescue/new?meal=${s.meal}`}>
                  <Button size="sm" variant="danger">
                    Prepare a rescue
                  </Button>
                </Link>
              </div>
            ) : null}
          </section>
          <ul className="flex flex-col gap-3">
            {s.dishes.map((d) => (
              <li key={d.dish_id} className="rim grid gap-4 px-5 py-4 md:grid-cols-[minmax(0,1.2fr)_160px_minmax(0,1.3fr)] md:items-center">
                <div className="flex gap-3">
                  <DishPhoto dish={photo(d.dish_id, d.dish)} size="md" className="mt-0.5" />
                  <div>
                  <p className="font-[620]">{d.dish}</p>
                  <p className="text-sm text-muted">
                    Cooked {fmtNum(d.prepared, 1)} {unitLabel(d.unit)}. Suggested batches {d.batches.map((b) => fmtNum(b, 1)).join(", then ")}.
                  </p>
                  </div>
                </div>
                <label className="block">
                  <span className="mb-1 block text-sm text-ink-2">Left in the pan</span>
                  <input
                    inputMode="decimal"
                    value={remaining[d.dish_id] ?? ""}
                    onChange={(e) => setRemaining((r) => ({ ...r, [d.dish_id]: e.target.value.replace(/[^0-9.]/g, "") }))}
                    className="qty h-11 w-full rounded-[8px] border border-rim bg-white px-3 text-lg"
                  />
                </label>
                <div className="text-base">
                  {d.projected_leftover == null ? (
                    <p className="text-muted">Log what's left to see a projection.</p>
                  ) : d.projected_leftover < 0 ? (
                    <p className="text-chilli">
                      Likely to run out{d.runs_out_at ? ` around ${fmtTime(d.runs_out_at)}` : ""}. {d.advice}.
                    </p>
                  ) : (
                    <p className={cx(d.advice ? "text-haldi-deep" : "text-ink-2")}>
                      About {fmtNum(d.projected_leftover, 1)} {unitLabel(d.unit)} likely left at the end.{d.advice ? ` ${d.advice}.` : ""}
                    </p>
                  )}
                </div>
              </li>
            ))}
          </ul>
          {can("logs:write") ? (
            <div className="mt-5 flex justify-end">
              <Button
                variant="primary"
                size="lg"
                busy={checkpoint.isPending}
                onClick={() =>
                  run(
                    () =>
                      checkpoint.mutateAsync({
                        id: s.session_id,
                        remaining: Object.fromEntries(Object.entries(remaining).filter(([, v]) => v !== "").map(([k, v]) => [k, Number(v)])),
                      }),
                    "Check saved.",
                  )
                }
              >
                Save this check
              </Button>
            </div>
          ) : null}
          <p className="mt-6 text-sm text-muted">
            Projection assumes diners arrive early in the service (a front-loaded depletion curve) until enough of this canteen's own checks exist to learn its rhythm. Total likely left over: {fmtNum(s.projected_leftover_kg, 1)} kg.
          </p>
        </>
      )}
    </div>
  );
}

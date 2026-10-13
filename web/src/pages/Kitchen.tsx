import { useQueryClient } from "@tanstack/react-query";
import { CloudOff, HandHeart, Minus, Plus, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";

import type { Schemas } from "@/api/client";
import { useSheet } from "@/api/hooks";
import { useCanteen } from "@/app/auth";
import { fmtDay, fmtNum, unitLabel } from "@/lib/format";
import { useLang } from "@/lib/i18n";
import { enqueue, flush, startBackgroundSync, useOnline, usePendingCount } from "@/lib/offline";
import { useToast } from "@/ui/overlays";
import { Button, ErrorState, Segmented, Skeleton, cx } from "@/ui/primitives";
import { DishPhoto, useDishLookup } from "@/ui/DishPhoto";

type SheetDish = Schemas["SheetDish"];
type Draft = { prepared: string; left: string; plate: string; soldOut: boolean };

function Stepper({ value, onChange, step, label, id }: { value: string; onChange: (v: string) => void; step: number; label: string; id: string }) {
  const n = Number(value || 0);
  const bump = (d: number) => onChange(String(Math.max(0, Math.round((n + d) * 10) / 10)));
  return (
    <div className="flex items-stretch overflow-hidden rounded-[10px] border border-rim bg-white">
      <button type="button" onClick={() => bump(-step)} className="flex w-11 items-center justify-center text-ink-2 hover:bg-steel-2 active:bg-steel-2" aria-label={`${label} minus ${step}`}>
        <Minus className="size-4" />
      </button>
      <input
        id={id}
        inputMode="decimal"
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/[^0-9.]/g, ""))}
        className="qty h-12 w-full min-w-0 border-x border-rim text-center text-xl focus:outline-none"
        aria-label={label}
      />
      <button type="button" onClick={() => bump(step)} className="flex w-11 items-center justify-center text-ink-2 hover:bg-steel-2 active:bg-steel-2" aria-label={`${label} plus ${step}`}>
        <Plus className="size-4" />
      </button>
    </div>
  );
}

function initial(d: SheetDish): Draft {
  return {
    prepared: String(d.prepared_qty ?? d.planned_qty ?? ""),
    left: d.unserved_qty != null ? String(d.unserved_qty) : "",
    plate: d.plate_waste_qty ? String(d.plate_waste_qty) : "",
    soldOut: !!d.sold_out,
  };
}

export default function Kitchen() {
  const { canteen } = useCanteen();
  const sheet = useSheet(canteen?.id);
  const photo = useDishLookup();
  const [lang, setLang, t] = useLang();
  const online = useOnline();
  const pending = usePendingCount();
  const toast = useToast();
  const qc = useQueryClient();
  const [meal, setMeal] = useState<string>("");
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [footfall, setFootfall] = useState("");
  const [saving, setSaving] = useState(false);
  const [surplus, setSurplus] = useState<Schemas["SurplusHint"] | null>(null);

  useEffect(
    () =>
      startBackgroundSync((out) => {
        qc.invalidateQueries({ queryKey: ["sheet"] });
        if (out.surplus.length) setSurplus(out.surplus[0]);
      }),
    [qc],
  );

  const meals = useMemo(() => sheet.data?.meals.filter((m) => m.dishes.length) ?? [], [sheet.data]);
  useEffect(() => {
    if (!meal && meals.length) {
      const serving = meals.find((m) => m.status === "serving");
      const lastDone = [...meals].reverse().find((m) => m.status === "done");
      setMeal((serving ?? lastDone ?? meals[0]).key);
    }
  }, [meals, meal]);
  const current = meals.find((m) => m.key === meal);
  useEffect(() => {
    if (!current) return;
    setDrafts(Object.fromEntries(current.dishes.map((d) => [d.dish_id, initial(d)])));
    setFootfall(current.footfall ? String(current.footfall) : "");
  }, [current]);

  const save = async () => {
    if (!current || !canteen || !sheet.data) return;
    setSaving(true);
    setSurplus(null);
    const entries = current.dishes
      .filter((d) => drafts[d.dish_id]?.prepared !== "")
      .map((d) => {
        const dr = drafts[d.dish_id];
        return {
          client_id: `${current.key}-${d.dish_id}`,
          canteen_id: canteen.id,
          date: sheet.data!.date,
          meal: current.key,
          dish_id: d.dish_id,
          prepared_qty: Number(dr.prepared || 0),
          unserved_qty: Number(dr.left || 0),
          plate_waste_qty: Number(dr.plate || 0),
          spoilage_qty: 0,
          sold_out: dr.soldOut,
          footfall: footfall ? Number(footfall) : null,
        };
      });
    const bad = entries.find((e) => e.unserved_qty > e.prepared_qty);
    if (bad) {
      setSaving(false);
      toast("error", "Left over can't be more than cooked. Check the numbers.");
      return;
    }
    await enqueue(entries);
    const out = await flush();
    setSaving(false);
    if (out) {
      const rejected = out.results.filter((r) => r.status === "rejected");
      if (rejected.length) toast("error", rejected[0].message ?? "Some entries were not saved.");
      else toast("ok", `${current.label}: ${t("saved").toLowerCase()}.`);
      if (out.surplus.length) setSurplus(out.surplus[0]);
      qc.invalidateQueries({ queryKey: ["sheet"] });
    } else {
      toast("info", t("savedOffline"));
    }
  };

  const set = (id: string, patch: Partial<Draft>) => setDrafts((d) => ({ ...d, [id]: { ...d[id], ...patch } }));

  return (
    <div className="mx-auto max-w-3xl">
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-base text-muted">
            {canteen?.name}, {sheet.data ? fmtDay(sheet.data.date, { weekday: "long", day: "numeric", month: "long" }) : ""}
          </p>
          <h1 className="text-3xl font-[700]" style={{ fontStretch: "118%" }}>
            {t("kitchen")}
          </h1>
        </div>
        <div className="flex items-center gap-3">
          {pending > 0 ? (
            <span className="inline-flex items-center gap-1.5 text-sm text-haldi-deep">
              {online ? <RefreshCw className="size-4 animate-spin" aria-hidden /> : <CloudOff className="size-4" aria-hidden />}
              {pending} {t("pending")}
            </span>
          ) : null}
          <Segmented
            size="sm"
            label="Language"
            value={lang}
            onChange={setLang}
            options={[
              { value: "en", label: "English" },
              { value: "hi", label: <span lang="hi">हिन्दी</span> },
            ]}
          />
        </div>
      </header>

      {sheet.isLoading ? (
        <Skeleton className="h-96 w-full" />
      ) : sheet.error ? (
        <ErrorState error={sheet.error} retry={() => sheet.refetch()} />
      ) : (
        <>
          <div role="tablist" aria-label="Meal" className="mb-4 grid gap-2" style={{ gridTemplateColumns: `repeat(${meals.length}, minmax(0, 1fr))` }}>
            {meals.map((m) => (
              <button
                key={m.key}
                role="tab"
                aria-selected={meal === m.key}
                onClick={() => setMeal(m.key)}
                className={cx(
                  "rounded-[10px] border px-3 py-2.5 text-left transition-colors",
                  meal === m.key ? "border-ink bg-ink text-steel" : "border-rim bg-plate text-ink hover:border-rim-strong",
                )}
              >
                <span className="block font-[650]">{m.label}</span>
                <span className={cx("block text-sm", meal === m.key ? "text-steel/75" : "text-muted")}>
                  {m.status === "serving" ? t("serving") : m.status === "done" ? t("done") : t("upcoming")}
                </span>
              </button>
            ))}
          </div>

          {surplus ? (
            <div role="alert" className="mb-4 flex flex-wrap items-center gap-3 rounded-[10px] border border-chilli/40 bg-chilli-soft px-4 py-3">
              <HandHeart className="size-5 text-chilli" aria-hidden />
              <p className="flex-1 text-base text-ink">{surplus.message}</p>
              <Link to={`/rescue/new?meal=${surplus.meal}&date=${surplus.date}`}>
                <Button variant="danger" size="sm">
                  Start a rescue
                </Button>
              </Link>
            </div>
          ) : null}

          {current ? (
            <ul className="flex flex-col gap-3">
              {current.dishes.map((d) => {
                const dr = drafts[d.dish_id] ?? initial(d);
                const step = d.unit === "kg" || d.unit === "litres" ? 0.5 : d.unit === "pieces" ? 10 : 5;
                return (
                  <li key={d.dish_id} className="rim px-4 py-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                      <DishPhoto dish={photo(d.dish_id, d.name)} size="lg" showDiet />
                      <p className="text-lg font-[650]">
                        {lang === "hi" && d.name_hi ? <span lang="hi">{d.name_hi}</span> : d.name}
                        {lang === "en" && d.name_hi ? (
                          <span lang="hi" className="ml-2 text-sm font-normal text-muted">
                            {d.name_hi}
                          </span>
                        ) : null}
                      </p>
                      </div>
                      {d.planned_qty != null ? (
                        <p className="text-sm text-muted">
                          {t("plan")}: <span className="num font-medium text-ink">{fmtNum(d.planned_qty, 1)}</span> {unitLabel(d.unit)}
                        </p>
                      ) : null}
                    </div>
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-[1fr_1fr_auto]">
                      <div>
                        <label htmlFor={`p-${d.dish_id}`} className="mb-1 block text-sm font-medium text-ink-2">
                          {t("cooked")} ({unitLabel(d.unit)})
                        </label>
                        <Stepper id={`p-${d.dish_id}`} value={dr.prepared} onChange={(v) => set(d.dish_id, { prepared: v })} step={step} label={t("cooked")} />
                      </div>
                      <div>
                        <label htmlFor={`l-${d.dish_id}`} className="mb-1 block text-sm font-medium text-ink-2">
                          {t("leftover")}
                        </label>
                        <Stepper id={`l-${d.dish_id}`} value={dr.left} onChange={(v) => set(d.dish_id, { left: v, soldOut: v === "0" ? dr.soldOut : false })} step={step} label={t("leftover")} />
                      </div>
                      <div className="col-span-2 sm:col-span-1">
                        <span className="mb-1 block text-sm font-medium text-ink-2">{t("ranOut")}</span>
                        <button
                          type="button"
                          aria-pressed={dr.soldOut}
                          onClick={() => set(d.dish_id, { soldOut: !dr.soldOut, left: !dr.soldOut ? "0" : dr.left })}
                          className={cx(
                            "h-12 w-full rounded-[10px] border px-5 font-[620] transition-colors sm:w-auto",
                            dr.soldOut ? "border-chilli bg-chilli text-white" : "border-rim bg-white text-ink-2 hover:border-rim-strong",
                          )}
                        >
                          {dr.soldOut ? t("ranOut") : "No"}
                        </button>
                      </div>
                    </div>
                    <details className="mt-3">
                      <summary className="cursor-pointer text-sm text-muted">{t("plateWaste")}</summary>
                      <div className="mt-2 max-w-60">
                        <Stepper id={`w-${d.dish_id}`} value={dr.plate} onChange={(v) => set(d.dish_id, { plate: v })} step={step} label={t("plateWaste")} />
                      </div>
                    </details>
                  </li>
                );
              })}
            </ul>
          ) : null}

          {current ? (
            <div className="sticky bottom-0 z-10 -mx-4 mt-5 flex flex-wrap items-end gap-3 border-t border-rim bg-steel/95 px-4 py-3 backdrop-blur sm:static sm:mx-0 sm:border-0 sm:bg-transparent sm:p-0">
              <div className="w-36">
                <label htmlFor="footfall" className="mb-1 block text-sm font-medium text-ink-2">
                  {t("diners")}
                </label>
                <input id="footfall" inputMode="numeric" value={footfall} onChange={(e) => setFootfall(e.target.value.replace(/\D/g, ""))} className="num h-12 w-full rounded-[10px] border border-rim bg-white px-3 text-lg" />
              </div>
              <Button variant="primary" size="lg" className="flex-1" onClick={save} busy={saving}>
                {t("save")} {current.label.toLowerCase()}
              </Button>
              <Link to={`/rescue/new?meal=${current.key}`} className="w-full sm:w-auto">
                <Button variant="secondary" size="lg" className="w-full border-chilli/50 text-chilli" icon={<HandHeart className="size-5" />}>
                  {t("reportSurplus")}
                </Button>
              </Link>
            </div>
          ) : null}
          {!online ? <p className="mt-4 text-sm text-muted">{t("offline")}</p> : null}
        </>
      )}
    </div>
  );
}

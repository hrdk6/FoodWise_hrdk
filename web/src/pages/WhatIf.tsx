import { FlaskConical } from "lucide-react";
import { useState } from "react";

import { ApiError, type Schemas } from "@/api/client";
import { useWhatIf } from "@/api/hooks";
import { useCanteen } from "@/app/auth";
import { percentileName } from "@/lib/dist";
import { addDays, fmtKg, fmtNum, fmtPct, unitLabel } from "@/lib/format";
import { DemandShape } from "@/ui/DemandShape";
import { useToast } from "@/ui/overlays";
import { Button, EmptyState, Field, Input, PageHeader, Section, Select, Switch, cx } from "@/ui/primitives";
import { DishPhoto, useDishLookup } from "@/ui/DishPhoto";

function Slider({ id, label, value, onChange, min, max, step, display }: { id: string; label: string; value: number; onChange: (v: number) => void; min: number; max: number; step: number; display: string }) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <label htmlFor={id} className="text-sm font-medium text-ink-2">
          {label}
        </label>
        <span className="num text-sm font-medium text-ink">{display}</span>
      </div>
      <input id={id} type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-[#1C2A4A]" />
    </div>
  );
}

export default function WhatIf() {
  const photo = useDishLookup();
  const { canteen, today } = useCanteen();
  const whatif = useWhatIf();
  const toast = useToast();
  const [day, setDay] = useState(addDays(today, 1));
  const [meal, setMeal] = useState("");
  const [headcount, setHeadcount] = useState(0);
  const [useRain, setUseRain] = useState(false);
  const [rain, setRain] = useState(0.8);
  const [exam, setExam] = useState(false);
  const [holiday, setHoliday] = useState(false);
  const [event, setEvent] = useState("");
  const [ratio, setRatio] = useState(0);
  const result = whatif.data;

  const run = async () => {
    try {
      await whatif.mutateAsync({
        canteen_id: canteen!.id,
        date: day,
        meal: meal || null,
        headcount_pct: headcount,
        rain_prob: useRain ? rain : null,
        exam: exam || null,
        holiday: holiday || null,
        event_attendance: event ? Number(event) : null,
        shortage_to_waste_ratio: ratio > 0 ? ratio : null,
      });
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not run the scenario.");
    }
  };

  const byMeal = (result?.dishes ?? []).reduce<Record<string, Schemas["WhatIfDish"][]>>((acc, d) => {
    (acc[d.meal] ??= []).push(d);
    return acc;
  }, {});
  const delta = result ? result.totals.scenario_kg - result.totals.base_kg : 0;

  return (
    <div>
      <PageHeader title="What if" sub="Try a fest, an exam week, a rainy day or a change in how much running short matters, and see the plan FoodWise would make. Nothing here changes the real plan." />
      <div className="grid gap-8 lg:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="rim flex h-fit flex-col gap-5 px-5 py-5 lg:sticky lg:top-6">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Day" htmlFor="day">
              <Input id="day" type="date" value={day} min={today} max={addDays(today, 14)} onChange={(e) => setDay(e.target.value)} />
            </Field>
            <Field label="Meal" htmlFor="meal">
              <Select id="meal" value={meal} onChange={(e) => setMeal(e.target.value)}>
                <option value="">All meals</option>
                {canteen?.meals.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.label}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <Slider id="hc" label="Diners compared with normal" value={headcount} onChange={setHeadcount} min={-0.6} max={0.6} step={0.05} display={headcount === 0 ? "As expected" : fmtPct(headcount, 0, true)} />
          <div className="flex flex-col gap-2">
            <Switch checked={useRain} onChange={setUseRain} label="Set the weather" />
            {useRain ? <Slider id="rain" label="Chance of rain" value={rain} onChange={setRain} min={0} max={1} step={0.05} display={fmtPct(rain)} /> : null}
          </div>
          <Switch checked={exam} onChange={setExam} label="Exam day" />
          <Switch checked={holiday} onChange={setHoliday} label="Holiday" />
          <Field label="Event on campus (expected visitors)" htmlFor="event">
            <Input id="event" inputMode="numeric" value={event} onChange={(e) => setEvent(e.target.value.replace(/\D/g, ""))} placeholder="0" />
          </Field>
          <Slider
            id="ratio"
            label="How bad is running short?"
            value={ratio}
            onChange={setRatio}
            min={0}
            max={12}
            step={0.5}
            display={ratio === 0 ? "Current setting" : `${fmtNum(ratio, 1)}× a wasted portion (cook at the ${percentileName(ratio / (1 + ratio))})`}
          />
          <Button variant="primary" size="lg" icon={<FlaskConical className="size-4" />} busy={whatif.isPending} onClick={run}>
            Run scenario
          </Button>
        </aside>

        <div className="min-w-0">
          {!result ? (
            <EmptyState title="Set up a scenario and run it">For example: a fest day with 2,000 visitors and an 80% chance of rain.</EmptyState>
          ) : (
            <>
              <section className="mb-6 grid grid-cols-3 gap-6">
                <div>
                  <p className="qty text-3xl">{fmtKg(result.totals.scenario_kg)}</p>
                  <p className="mt-1 text-sm text-muted">to cook in this scenario</p>
                </div>
                <div>
                  <p className={cx("qty text-3xl", delta > 0 ? "text-haldi-deep" : delta < 0 ? "text-rain" : "")}>
                    {delta >= 0 ? "+" : "−"}
                    {fmtKg(Math.abs(delta))}
                  </p>
                  <p className="mt-1 text-sm text-muted">compared with the normal plan</p>
                </div>
                <div>
                  <p className="qty text-3xl">{fmtKg(result.totals.expected_waste_kg)}</p>
                  <p className="mt-1 text-sm text-muted">expected to be left over</p>
                </div>
              </section>
              <p className="mb-4 text-sm text-muted">Each shape is the scenario's forecast. The turmeric line is what to cook; the grey tick is what the normal plan would cook.</p>
              {Object.entries(byMeal).map(([m, dishes]) => (
                <Section key={m} title={canteen?.meals.find((x) => x.key === m)?.label ?? m}>
                  <ul className="rim divide-y divide-rim">
                    {dishes.map((d) => {
                      const change = d.base_recommended ? d.recommended / d.base_recommended - 1 : 0;
                      return (
                        <li key={d.dish_id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 px-5 py-3 md:grid-cols-[minmax(140px,1fr)_minmax(180px,1.5fr)_150px]">
                          <div className="flex items-center gap-3">
                            <DishPhoto dish={photo(d.dish_id, d.dish)} size="sm" />
                            <div>
                            <p className="font-[620]">{d.dish}</p>
                            <p className="text-sm text-muted">{d.drivers.map((x) => `${x.label} ${x.effect > 0 ? "+" : "−"}${fmtPct(Math.abs(x.effect))}`).join(", ") || " "}</p>
                            </div>
                          </div>
                          <div className="order-3 col-span-2 h-10 md:order-none md:col-span-1">
                            <DemandShape levels={result.levels} quantiles={d.quantiles} recommended={d.recommended} lastWeek={d.base_recommended} unit={d.unit} animate={false} />
                          </div>
                          <div className="text-right">
                            <p className="qty text-xl">
                              {fmtNum(d.recommended, 1)} <span className="text-sm font-normal text-muted">{unitLabel(d.unit)}</span>
                            </p>
                            <p className={cx("num text-sm", Math.abs(change) < 0.01 ? "text-muted" : change > 0 ? "text-haldi-deep" : "text-rain")}>
                              {Math.abs(change) < 0.01 ? "no change" : `${fmtPct(change, 0, true)} vs ${fmtNum(d.base_recommended, 1)}`}
                            </p>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </Section>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

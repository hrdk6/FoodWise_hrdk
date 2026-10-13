import { Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { ApiError } from "@/api/client";
import { useCreateIncident, useDishes, useIncidentDraft, useSheet } from "@/api/hooks";
import { useAuth, useCanteen } from "@/app/auth";
import { unitLabel } from "@/lib/format";
import { DishPhoto, useDishLookup } from "@/ui/DishPhoto";
import { useToast } from "@/ui/overlays";
import { Button, Field, Input, PageHeader, Select, Textarea } from "@/ui/primitives";

type Item = { dish_id: string; qty: string; storage: "hot_held" | "chilled" | "ambient"; cooked: string };

function localTime(d = new Date()) {
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export default function RescueNew() {
  const { canteen, today } = useCanteen();
  const { can } = useAuth();
  const [params] = useSearchParams();
  const nav = useNavigate();
  const toast = useToast();
  const day = params.get("date") ?? today;
  const sheet = useSheet(canteen?.id);
  const meals = canteen?.meals ?? [];
  const [meal, setMeal] = useState(params.get("meal") ?? "");
  const draft = useIncidentDraft(canteen?.id, day, meal || undefined);
  const dishes = useDishes();
  const create = useCreateIncident();
  const [items, setItems] = useState<Item[]>([]);
  const [notes, setNotes] = useState("");
  const [belowThreshold, setBelowThreshold] = useState<string | null>(null);

  useEffect(() => {
    if (!meal && meals.length) {
      const serving = sheet.data?.meals.find((m) => m.status === "serving" || m.status === "done");
      setMeal(serving?.key ?? meals[0].key);
    }
  }, [meal, meals, sheet.data]);

  const menuDishes = useMemo(() => sheet.data?.meals.find((m) => m.key === meal)?.dishes ?? [], [sheet.data, meal]);
  const mealStart = meals.find((m) => m.key === meal)?.start ?? "12:00";
  const defaultCooked = useMemo(() => {
    const [h, m] = mealStart.split(":").map(Number);
    const d = new Date();
    d.setHours(h, m - 45, 0, 0);
    return localTime(d > new Date() ? new Date() : d);
  }, [mealStart]);

  useEffect(() => {
    if (draft.data?.length) {
      setItems(draft.data.map((d) => ({ dish_id: String(d.dish_id), qty: String(d.qty), storage: "hot_held", cooked: defaultCooked })));
    } else if (draft.data && menuDishes.length) {
      setItems([{ dish_id: menuDishes[0].dish_id, qty: "", storage: "hot_held", cooked: defaultCooked }]);
    }
  }, [draft.data, menuDishes, defaultCooked]);

  const dishName = (id: string) => dishes.data?.find((d) => d.id === id);
  const photo = useDishLookup();
  const choices = menuDishes.length ? menuDishes.map((d) => ({ id: d.dish_id, name: d.name })) : (dishes.data ?? []).map((d) => ({ id: d.id, name: d.name }));
  const valid = items.length > 0 && items.every((i) => i.dish_id && Number(i.qty) > 0);

  const submit = async (force = false) => {
    if (!canteen) return;
    try {
      const inc = await create.mutateAsync({
        canteen_id: canteen.id,
        trigger: params.get("meal") ? "end_of_service" : "manual",
        meal,
        notes: notes || null,
        force,
        items: items.map((i) => {
          const [h, m] = i.cooked.split(":").map(Number);
          const cooked = new Date();
          cooked.setHours(h, m, 0, 0);
          return { dish_id: i.dish_id, qty: Number(i.qty), storage: i.storage, cooked_at: cooked.toISOString() };
        }),
      });
      if (inc.status === "rejected") {
        setBelowThreshold(inc.events.at(-1)?.message ?? "This is below the large-surplus threshold.");
        return;
      }
      toast("ok", inc.route === "divert" ? "Not safe for people any more. Here are animal shelters and compost sites nearby." : "Safe to give. Here are the places nearby.");
      nav(`/rescue/${inc.id}`);
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not report the surplus.");
    }
  };

  const set = (i: number, patch: Partial<Item>) => setItems((xs) => xs.map((x, j) => (j === i ? { ...x, ...patch } : x)));

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Report surplus food"
        sub="Enter what is left and when it was cooked. FoodWise checks it is still safe to give away, then shows the places near you that can use it."
      />
      <div className="flex flex-col gap-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Canteen">
            <Input value={canteen?.name ?? ""} disabled />
          </Field>
          <Field label="Meal" htmlFor="meal">
            <Select id="meal" value={meal} onChange={(e) => setMeal(e.target.value)}>
              {meals.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <fieldset className="flex flex-col gap-3">
          <legend className="mb-2 text-sm font-medium text-ink-2">Food</legend>
          {draft.data?.length ? <p className="-mt-1 text-sm text-muted">Prefilled from what the kitchen logged as left over.</p> : null}
          {items.map((it, i) => {
            const unit = dishName(it.dish_id)?.unit ?? "plates";
            return (
              <div key={i} className="rim grid gap-3 px-4 py-4 sm:grid-cols-[auto_1.6fr_1fr_1.2fr_0.9fr_auto] sm:items-end">
                <DishPhoto dish={photo(it.dish_id)} size="lg" showDiet className="hidden sm:block" />
                <Field label="Dish" htmlFor={`dish-${i}`}>
                  <Select id={`dish-${i}`} value={it.dish_id} onChange={(e) => set(i, { dish_id: e.target.value })}>
                    {choices.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label={`Quantity (${unitLabel(unit)})`} htmlFor={`qty-${i}`}>
                  <Input id={`qty-${i}`} inputMode="decimal" value={it.qty} onChange={(e) => set(i, { qty: e.target.value.replace(/[^0-9.]/g, "") })} className="num" />
                </Field>
                <Field label="Kept" htmlFor={`st-${i}`}>
                  <Select id={`st-${i}`} value={it.storage} onChange={(e) => set(i, { storage: e.target.value as Item["storage"] })}>
                    <option value="hot_held">Hot (in the bain-marie)</option>
                    <option value="chilled">In the fridge</option>
                    <option value="ambient">At room temperature</option>
                  </Select>
                </Field>
                <Field label="Cooked at" htmlFor={`ck-${i}`}>
                  <Input id={`ck-${i}`} type="time" value={it.cooked} onChange={(e) => set(i, { cooked: e.target.value })} />
                </Field>
                <Button variant="ghost" onClick={() => setItems((xs) => xs.filter((_, j) => j !== i))} aria-label="Remove this dish" icon={<Trash2 className="size-4" />} />
              </div>
            );
          })}
          <div>
            <Button variant="ghost" icon={<Plus className="size-4" />} onClick={() => setItems((xs) => [...xs, { dish_id: choices[0]?.id ?? "", qty: "", storage: "hot_held", cooked: defaultCooked }])}>
              Add a dish
            </Button>
          </div>
        </fieldset>

        <Field label="Notes (optional)" htmlFor="notes" hint="For example: which gate to use. Never include student details.">
          <Textarea id="notes" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={500} />
        </Field>

        {belowThreshold ? (
          <div role="status" className="rounded-[10px] border border-rim bg-steel-2 px-4 py-3">
            <p className="text-base text-ink">{belowThreshold}</p>
            <p className="mt-1 text-sm text-muted">Small surpluses are logged to improve tomorrow's plan.</p>
            {can("incident:manage") ? (
              <Button className="mt-3" onClick={() => submit(true)} busy={create.isPending}>
                Find a place anyway
              </Button>
            ) : null}
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Button variant="primary" size="lg" onClick={() => submit(false)} busy={create.isPending} disabled={!valid}>
            Check safety and find places
          </Button>
          <Button variant="ghost" size="lg" onClick={() => nav(-1)}>
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}

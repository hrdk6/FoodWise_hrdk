import { Camera, Check, ImageOff, Pencil, RefreshCw, Search, Upload } from "lucide-react";
import { type ChangeEvent, type FormEvent, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router";

import { ApiError, type Schemas } from "@/api/client";
import { useDishImage, useDishes, useImageCandidates, useMenus, usePutMenu } from "@/api/hooks";
import { useAuth, useCanteen } from "@/app/auth";
import { addDays, fmtDay, relDay } from "@/lib/format";
import { DietMark, DishPhoto } from "@/ui/DishPhoto";
import { Dialog, useToast } from "@/ui/overlays";
import { Button, EmptyState, ErrorState, Input, PageHeader, Skeleton, Tabs, cx } from "@/ui/primitives";

type Dish = Schemas["DishOut"];

const COURSE_ORDER = ["breakfast", "main", "rice", "dal", "curry", "sabzi", "bread", "side", "dessert", "snack", "beverage"];

function PictureDialog({ dish, onClose }: { dish: Dish; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const cands = useImageCandidates(dish.id, search);
  const act = useDishImage();
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      toast("ok", ok);
      onClose();
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not change the picture.");
    }
  };
  const upload = (e: ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) run(() => act.mutateAsync({ kind: "upload", id: dish.id, file: f }), `Your photo of ${dish.name} is saved.`);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setSearch(q.trim());
  };
  return (
    <Dialog open onClose={onClose} title={`Picture for ${dish.name}`} wide>
      <div className="flex flex-col gap-5">
        <div className="flex flex-wrap items-center gap-4">
          <DishPhoto dish={dish} size="xl" showDiet />
          <div className="min-w-0 flex-1">
            <p className="text-base text-ink-2">
              {dish.image_source === "upload"
                ? "Your kitchen's own photo."
                : dish.image_url
                  ? `Found on Wikimedia Commons. ${dish.image_credit ?? ""}`
                  : dish.image_status === "pending"
                    ? "FoodWise is still looking for a picture."
                    : "No picture yet."}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" variant="primary" icon={<Upload className="size-4" />} onClick={() => file.current?.click()} busy={act.isPending && act.variables?.kind === "upload"}>
                Upload your own photo
              </Button>
              <input ref={file} type="file" accept="image/jpeg,image/png,image/webp" capture="environment" className="sr-only" onChange={upload} aria-label="Photo of the dish" />
              {dish.image_url ? (
                <Button size="sm" variant="ghost" icon={<ImageOff className="size-4" />} onClick={() => run(() => act.mutateAsync({ kind: "remove", id: dish.id }), "Picture removed.")}>
                  No picture
                </Button>
              ) : (
                <Button size="sm" variant="ghost" icon={<RefreshCw className="size-4" />} onClick={() => run(() => act.mutateAsync({ kind: "auto", id: dish.id }), "Looking for a picture again.")}>
                  Look again
                </Button>
              )}
            </div>
            <p className="mt-2 text-sm text-muted">Photos are cropped square, and location and camera details are removed.</p>
          </div>
        </div>

        <div>
          <p className="mb-2 font-[640]">Or choose a freely licensed photo</p>
          <form onSubmit={submit} className="mb-3 flex gap-2">
            <Input aria-label="Search words" placeholder={`Search, for example "${dish.name}"`} value={q} onChange={(e) => setQ(e.target.value)} />
            <Button type="submit" icon={<Search className="size-4" />}>
              Search
            </Button>
          </form>
          {cands.isLoading ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="aspect-square w-full" />
              ))}
            </div>
          ) : cands.error ? (
            <ErrorState error={cands.error} retry={() => cands.refetch()} />
          ) : !cands.data?.length ? (
            <p className="text-base text-muted">No freely licensed photos matched. Try other words, or upload your own.</p>
          ) : (
            <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {cands.data.map((c) => (
                <li key={c.title}>
                  <button
                    onClick={() => run(() => act.mutateAsync({ kind: "choose", id: dish.id, title: c.title }), `Picture for ${dish.name} updated.`)}
                    disabled={act.isPending}
                    className="group block w-full text-left"
                  >
                    <span className="block aspect-square overflow-hidden rounded-[10px] border border-rim bg-steel-2 group-hover:border-ink-2">
                      <img src={c.thumb_url} alt={c.title.replace(/^File:/, "").replace(/\.\w+$/, "")} loading="lazy" className="size-full object-cover" />
                    </span>
                    <span className="mt-1 block truncate text-xs text-muted" title={c.credit}>
                      {c.license}, {c.credit.split(",")[0]}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-xs text-muted">From Wikimedia Commons. The photographer and licence are shown wherever the picture appears.</p>
        </div>
      </div>
    </Dialog>
  );
}

function MealEditor({ day, meal, label, current, dishes, onClose }: { day: string; meal: string; label: string; current: string[]; dishes: Dish[]; onClose: () => void }) {
  const { canteen } = useCanteen();
  const put = usePutMenu();
  const toast = useToast();
  const [picked, setPicked] = useState<string[]>(current);
  const [filter, setFilter] = useState("");
  const shown = dishes.filter((d) => d.is_active && d.name.toLowerCase().includes(filter.toLowerCase()));
  const save = async () => {
    try {
      await put.mutateAsync({ canteen_id: canteen!.id, date: day, meal, dish_ids: picked });
      toast("ok", `${label} on ${fmtDay(day)} saved. The plan for that meal is being re-forecast.`);
      onClose();
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not save the menu.");
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title={`${label}, ${fmtDay(day)}`}
      wide
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={put.isPending} onClick={save} icon={<Check className="size-4" />}>
            Save {picked.length} {picked.length === 1 ? "dish" : "dishes"}
          </Button>
        </>
      }
    >
      <Input aria-label="Find a dish" placeholder="Find a dish" value={filter} onChange={(e) => setFilter(e.target.value)} className="mb-3" />
      <ul className="grid gap-2 sm:grid-cols-2">
        {shown.map((d) => {
          const on = picked.includes(d.id);
          return (
            <li key={d.id}>
              <button
                type="button"
                aria-pressed={on}
                onClick={() => setPicked((xs) => (on ? xs.filter((x) => x !== d.id) : [...xs, d.id]))}
                className={cx("flex w-full items-center gap-3 rounded-[10px] border px-3 py-2 text-left", on ? "border-leaf bg-leaf-soft/60" : "border-rim bg-plate hover:border-rim-strong")}
              >
                <DishPhoto dish={d} size="sm" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{d.name}</span>
                  <span className="block text-xs text-muted">{d.course}</span>
                </span>
                {on ? <Check className="size-4 text-leaf" aria-hidden /> : null}
              </button>
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}

function Week() {
  const { canteen, today } = useCanteen();
  const { can } = useAuth();
  const [start, setStart] = useState(today);
  const end = addDays(start, 6);
  const menus = useMenus(canteen?.id, start, end);
  const dishes = useDishes();
  const [editing, setEditing] = useState<{ day: string; meal: string; label: string; current: string[] } | null>(null);
  const byId = useMemo(() => new Map((dishes.data ?? []).map((d) => [d.id, d])), [dishes.data]);
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  const meals = canteen?.meals ?? [];
  if (menus.isLoading || dishes.isLoading) return <Skeleton className="h-96 w-full" />;
  if (menus.error) return <ErrorState error={menus.error} retry={() => menus.refetch()} />;
  const menuFor = (day: string) => menus.data?.find((m) => m.date === day)?.meals ?? {};
  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="ghost" onClick={() => setStart(addDays(start, -7))}>
          Previous week
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setStart(today)} disabled={start === today}>
          This week
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setStart(addDays(start, 7))}>
          Next week
        </Button>
      </div>
      <div className="flex flex-col gap-6">
        {days.map((day) => (
          <section key={day} aria-label={fmtDay(day)}>
            <h3 className={cx("mb-2 text-lg font-[650]", day === today && "text-haldi-deep")}>
              {relDay(day, today)}
              {relDay(day, today) !== fmtDay(day) ? <span className="ml-2 text-base font-normal text-muted">{fmtDay(day)}</span> : null}
            </h3>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {meals.map((m) => {
                const ids = menuFor(day)[m.key] ?? [];
                return (
                  <div key={m.key} className="rim px-4 py-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <p className="font-[640]">
                        {m.label} <span className="text-sm font-normal text-muted">{m.start}</span>
                      </p>
                      {can("menu:write") && day >= today ? (
                        <Button size="sm" variant="ghost" icon={<Pencil className="size-3.5" />} onClick={() => setEditing({ day, meal: m.key, label: m.label, current: ids })}>
                          Edit
                        </Button>
                      ) : null}
                    </div>
                    {ids.length ? (
                      <ul className="flex flex-wrap gap-x-4 gap-y-3">
                        {ids.map((id) => {
                          const d = byId.get(id);
                          if (!d) return null;
                          return (
                            <li key={id} className="flex w-[84px] flex-col items-center gap-1 text-center">
                              <DishPhoto dish={d} size="lg" showDiet />
                              <span className="text-sm leading-tight text-ink">{d.name}</span>
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      <p className="text-sm text-muted">Nothing on the menu.</p>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>
      {editing && dishes.data ? <MealEditor {...editing} dishes={dishes.data} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

function Dishes() {
  const dishes = useDishes();
  const { can } = useAuth();
  const [picking, setPicking] = useState<Dish | null>(null);
  if (dishes.isLoading) return <Skeleton className="h-96 w-full" />;
  if (dishes.error) return <ErrorState error={dishes.error} retry={() => dishes.refetch()} />;
  if (!dishes.data?.length) return <EmptyState title="No dishes yet">Dishes appear here when you import your history or add them to a menu.</EmptyState>;
  const sorted = [...dishes.data].sort((a, b) => COURSE_ORDER.indexOf(a.course) - COURSE_ORDER.indexOf(b.course) || a.name.localeCompare(b.name));
  const missing = sorted.filter((d) => !d.image_url).length;
  const current = picking ? (dishes.data.find((d) => d.id === picking.id) ?? picking) : null;
  return (
    <div>
      <p className="mb-4 text-base text-ink-2">
        Pictures appear wherever a dish is mentioned: the prep plan, kitchen log, live service, rescues and the students' feedback page.
        {missing ? ` ${missing} ${missing === 1 ? "dish has" : "dishes have"} no picture yet.` : ""}
      </p>
      <ul className="grid grid-cols-2 gap-x-4 gap-y-6 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6">
        {sorted.map((d) => (
          <li key={d.id} className="flex flex-col items-center text-center">
            <button
              onClick={() => can("menu:write") && setPicking(d)}
              className="group relative rounded-full focus-visible:outline-2 focus-visible:outline-haldi"
              aria-label={`Change the picture for ${d.name}`}
              disabled={!can("menu:write")}
            >
              <DishPhoto dish={d} size="xl" />
              {can("menu:write") ? (
                <span className="absolute bottom-1 right-1 flex size-8 items-center justify-center rounded-full border border-rim bg-plate text-ink-2 opacity-90 shadow-sm group-hover:opacity-100">
                  <Camera className="size-4" aria-hidden />
                </span>
              ) : null}
            </button>
            <p className="mt-2 flex items-center gap-1.5 font-[620] leading-tight">
              <DietMark diet={d.diet} /> {d.name}
            </p>
            <p className="text-xs text-muted">{d.course}</p>
            <p className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted" title={d.image_credit ?? undefined}>
              {d.image_source === "upload" ? "Your photo" : d.image_credit ? d.image_credit : d.image_status === "pending" ? "Looking for a picture" : "No picture"}
            </p>
          </li>
        ))}
      </ul>
      {current ? <PictureDialog dish={current} onClose={() => setPicking(null)} /> : null}
    </div>
  );
}

export default function Menu() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "dishes" ? "dishes" : "week";
  const setTab = (t: "week" | "dishes") => setParams(t === "week" ? {} : { tab: t }, { replace: true });
  return (
    <div>
      <PageHeader title="Menu" sub="What is cooking this week, with a picture of every dish. Changing a meal re-forecasts that day's plan." />
      <Tabs
        value={tab}
        onChange={setTab}
        tabs={[
          { value: "week", label: "This week" },
          { value: "dishes", label: "Dishes and pictures" },
        ]}
      />
      <div className="mt-6">{tab === "week" ? <Week /> : <Dishes />}</div>
    </div>
  );
}

import { Printer, QrCode } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ApiError, type Schemas } from "@/api/client";
import { useComments, useFeedbackSummary, useMenuSuggestions, useMenus, usePutMenu, useQr, useRsvpStats, useVotes } from "@/api/hooks";
import { useAuth, useCanteen } from "@/app/auth";
import { addDays, fmtDateTime, fmtDay, fmtNum, fmtPct } from "@/lib/format";
import { C, ChartFrame, LegendItem, TooltipBox, axisProps } from "@/ui/charts";
import { Dialog, useToast } from "@/ui/overlays";
import { Badge, Button, EmptyState, ErrorState, PageHeader, Section, Segmented, Select, Skeleton, cx } from "@/ui/primitives";
import { DishPhoto, useDishLookup } from "@/ui/DishPhoto";

type Dish = Schemas["DishScore"];

const DIAGNOSIS: Record<string, { label: string; tone: "chilli" | "leaf" | "haldi" }> = {
  replace_or_reduce: { label: "Replace or cook less", tone: "chilli" },
  increase: { label: "Cook more", tone: "leaf" },
  watch: { label: "Watch", tone: "haldi" },
};
const ASPECT: Record<string, string> = { taste: "taste", quantity: "quantity", temperature: "temperature", freshness: "freshness", variety: "variety", hygiene: "cleanliness" };

/** Score on a 1-5 line, with the canteen average as a reference tick. */
function ScoreLine({ score, mean }: { score: number; mean: number | null }) {
  const x = (v: number) => `${((v - 1) / 4) * 100}%`;
  return (
    <div className="relative h-6 w-full" aria-hidden>
      <div className="absolute inset-x-0 top-1/2 h-px bg-rim" />
      {[1, 2, 3, 4, 5].map((v) => (
        <div key={v} className="absolute top-[calc(50%-3px)] h-1.5 w-px bg-rim-strong" style={{ left: x(v) }} />
      ))}
      {mean != null ? <div className="absolute top-0.5 h-5 w-[2px] rounded bg-muted/60" style={{ left: x(mean) }} title="Canteen average" /> : null}
      <div className="absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-plate bg-[#3E6DB0]" style={{ left: x(score) }} />
    </div>
  );
}

function DishRow({ d, mean }: { d: Dish; mean: number | null }) {
  const negatives = Object.entries(d.aspects).sort((a, b) => b[1] - a[1]).slice(0, 2);
  const total = d.liked + d.okay + d.disliked;
  const photo = useDishLookup()(d.dish_id, d.dish);
  return (
    <li className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-5 gap-y-2 px-5 py-3.5 md:grid-cols-[minmax(150px,1fr)_minmax(160px,1.2fr)_90px_minmax(150px,1fr)_auto]">
      <div className="flex min-w-0 items-center gap-3">
        <DishPhoto dish={photo} size="md" />
        <div className="min-w-0">
        <p className="font-[620]">{d.dish}</p>
        <p className="text-sm text-muted">
          {d.n} {d.n === 1 ? "rating" : "ratings"}
          {d.trend != null && Math.abs(d.trend) >= 0.15 ? (
            <span className={d.trend > 0 ? "text-leaf" : "text-chilli"}>
              , {d.trend > 0 ? "up" : "down"} {fmtNum(Math.abs(d.trend), 1)} on last month
            </span>
          ) : null}
        </p>
        </div>
      </div>
      {d.shown && d.score != null ? (
        <>
          <div className="order-3 col-span-2 md:order-none md:col-span-1">
            <ScoreLine score={d.score} mean={mean} />
          </div>
          <p className="qty text-right text-xl">
            {fmtNum(d.score, 1)}
            <span className="text-sm font-normal text-muted">/5</span>
          </p>
          <div className="order-4 col-span-2 text-sm text-ink-2 md:order-none md:col-span-1">
            <div className="mb-1 flex h-1.5 w-full gap-[2px] overflow-hidden rounded-full" aria-label={`${d.liked} liked, ${d.okay} okay, ${d.disliked} disliked`}>
              <span className="bg-[#3E6DB0]" style={{ width: `${(d.liked / total) * 100}%` }} />
              <span className="bg-rim-strong" style={{ width: `${(d.okay / total) * 100}%` }} />
              <span className="bg-[#C8880E]" style={{ width: `${(d.disliked / total) * 100}%` }} />
            </div>
            {negatives.length ? `Complaints about ${negatives.map(([a]) => ASPECT[a] ?? a).join(" and ")}` : `${fmtPct(d.liked / total)} liked it`}
          </div>
          <div className="order-5 col-span-2 flex flex-wrap items-center gap-2 md:order-none md:col-span-1 md:justify-end">
            {d.waste_rate != null ? <span className="text-sm text-muted">{fmtPct(d.waste_rate)} unserved</span> : null}
            {d.diagnosis && DIAGNOSIS[d.diagnosis] ? <Badge tone={DIAGNOSIS[d.diagnosis].tone}>{DIAGNOSIS[d.diagnosis].label}</Badge> : null}
          </div>
        </>
      ) : (
        <p className="text-sm text-muted md:col-span-4">Hidden until at least 5 students rate it, so no one can be singled out.</p>
      )}
    </li>
  );
}

function QrDialog({ onClose }: { onClose: () => void }) {
  const { canteen, today } = useCanteen();
  const [meal, setMeal] = useState(canteen?.meals[0]?.key ?? "");
  const qr = useQr(canteen?.id, today, meal);
  return (
    <Dialog
      open
      onClose={onClose}
      title="Feedback QR code"
      footer={
        <Button icon={<Printer className="size-4" />} onClick={() => window.print()}>
          Print
        </Button>
      }
    >
      <div className="flex flex-col items-center gap-4">
        <Segmented label="Meal" value={meal} onChange={setMeal} options={(canteen?.meals ?? []).map((m) => ({ value: m.key, label: m.label }))} />
        {qr.data ? (
          <>
            <div className="rounded-[12px] bg-white p-5 ring-1 ring-rim">
              <QRCodeSVG value={qr.data.url} size={220} fgColor="#1C2A4A" level="M" />
            </div>
            <div className="text-center">
              <p className="text-lg font-[650]">
                {qr.data.canteen}, {canteen?.meals.find((m) => m.key === meal)?.label} today
              </p>
              <p className="text-sm text-muted">Scan to rate today's food. Anonymous. Valid until {fmtDateTime(qr.data.expires_at)}.</p>
              <a href={new URL(qr.data.url).pathname} target="_blank" rel="noreferrer" className="mt-2 inline-block text-sm text-rain underline underline-offset-4">
                Open the student page
              </a>
            </div>
          </>
        ) : (
          <Skeleton className="size-60" />
        )}
      </div>
    </Dialog>
  );
}

function Comments() {
  const { canteen } = useCanteen();
  const [filter, setFilter] = useState<"all" | "negative" | "positive">("all");
  const comments = useComments(canteen?.id, undefined, filter === "all" ? undefined : filter);
  return (
    <Section title="What students wrote" actions={<Segmented size="sm" label="Filter comments" value={filter} onChange={setFilter} options={[{ value: "all", label: "All" }, { value: "negative", label: "Unhappy" }, { value: "positive", label: "Happy" }]} />}>
      {comments.isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : !comments.data?.length ? (
        <EmptyState title="No comments yet" />
      ) : (
        <ul className="flex max-h-[640px] flex-col gap-2 overflow-y-auto pr-1">
          {comments.data.map((c) => (
            <li key={c.id} className="rim-sunken px-4 py-3">
              <p className={cx("text-base text-ink", c.language === "hi" && "font-[450]")} lang={c.language === "hi" ? "hi" : undefined}>
                "{c.text}"
              </p>
              <p className="mt-1 text-sm text-muted">
                {c.dish}, {c.meal} on {fmtDay(c.date)}, rated {c.rating}/5
                {c.language && c.language !== "en" ? `, ${c.language === "hi" ? "Hindi" : "Hinglish"}` : ""}
                {c.down_weighted ? ", possible spam (counts less)" : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function RsvpChart() {
  const { canteen } = useCanteen();
  const rsvp = useRsvpStats(canteen?.id);
  const data = useMemo(() => {
    const dinner = (rsvp.data ?? []).filter((r) => r.meal === (canteen?.meals.at(-1)?.key ?? "dinner"));
    return dinner.map((r) => ({ day: fmtDay(r.date, { day: "numeric", month: "short" }), eating: r.eating, came: r.footfall }));
  }, [rsvp.data, canteen]);
  if (!data.length) return null;
  const label = canteen?.meals.at(-1)?.label.toLowerCase();
  return (
    <ChartFrame
      title={`RSVPs and who came, ${label}`}
      sub="Students tell the mess by 9 pm whether they'll eat. RSVPs feed tomorrow's forecast directly."
      legend={
        <>
          <LegendItem color={C.blue} label="Came" />
          <LegendItem color={C.turmeric} label="RSVP'd eating" />
        </>
      }
      table={{ columns: ["Day", "RSVP eating", "Came"], rows: data.map((d) => [d.day, fmtNum(d.eating), d.came != null ? fmtNum(d.came) : "–"]) }}
      height={220}
    >
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -10 }}>
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="day" {...axisProps} interval="preserveStartEnd" />
          <YAxis {...axisProps} width={44} />
          <Tooltip content={({ active, payload }) => (active && payload?.length ? <TooltipBox title={payload[0].payload.day} rows={[{ label: "Came", value: fmtNum(payload[0].payload.came), color: C.blue }, { label: "RSVP eating", value: fmtNum(payload[0].payload.eating), color: C.turmeric }]} /> : null)} />
          <Line dataKey="came" stroke={C.blue} strokeWidth={2} dot={false} connectNulls={false} />
          <Line dataKey="eating" stroke={C.turmeric} strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

function Votes() {
  const { canteen } = useCanteen();
  const votes = useVotes(canteen?.id);
  const latest = votes.data?.[0]?.week_start;
  const rows = (votes.data ?? []).filter((v) => v.week_start === latest);
  const total = rows.reduce((s, r) => s + r.votes, 0);
  if (!rows.length) return null;
  return (
    <Section title="Next week's special" sub={`${fmtNum(total)} votes this week`}>
      <ul className="flex flex-col gap-2.5">
        {rows.map((r, i) => (
          <li key={r.dish_id}>
            <div className="mb-1 flex justify-between text-base">
              <span className={i === 0 ? "font-[620]" : ""}>{r.dish}</span>
              <span className="num text-ink-2">{fmtPct(r.votes / total)}</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-steel-2">
              <div className="h-full rounded-full" style={{ width: `${(r.votes / total) * 100}%`, background: i === 0 ? C.blue : C.muted }} />
            </div>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function Suggestions() {
  const { canteen, today } = useCanteen();
  const { can } = useAuth();
  const sugg = useMenuSuggestions(canteen?.id);
  const menus = useMenus(canteen?.id, today, addDays(today, 8));
  const put = usePutMenu();
  const toast = useToast();
  if (!sugg.data?.length) return null;
  const swap = async (s: Schemas["Suggestion"]) => {
    const current = menus.data?.find((m) => m.date === s.date)?.meals[s.meal] ?? [];
    if (!current.includes(s.current_dish_id)) {
      toast("error", "The menu changed since this suggestion was made. Reload the page.");
      return;
    }
    try {
      await put.mutateAsync({
        canteen_id: canteen!.id,
        date: s.date,
        meal: s.meal,
        dish_ids: current.map((id) => (id === s.current_dish_id ? s.suggested_dish_id : id)),
      });
      toast("ok", `${s.suggested_dish} replaces ${s.current_dish} on ${fmtDay(s.date)}. That day's plan will be re-forecast.`);
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not change the menu.");
    }
  };
  return (
    <Section title="Menu suggestions" sub="Swaps for the coming week that keep course and diet the same and avoid repeating a dish within three days.">
      <ul className="rim divide-y divide-rim">
        {sugg.data.map((s) => (
          <li key={`${s.date}-${s.meal}-${s.current_dish}`} className="flex flex-wrap items-center gap-x-5 gap-y-2 px-5 py-3.5">
            <div className="min-w-0 flex-1">
              <p className="font-[620]">
                {fmtDay(s.date)} {s.meal}: {s.current_dish} to {s.suggested_dish}
              </p>
              <p className="text-sm text-muted">{s.reason}</p>
            </div>
            {can("menu:write") ? (
              <Button size="sm" busy={put.isPending} onClick={() => swap(s)}>
                Swap on the menu
              </Button>
            ) : null}
          </li>
        ))}
      </ul>
    </Section>
  );
}

export default function Feedback() {
  const { canteen } = useCanteen();
  const [days, setDays] = useState("28");
  const summary = useFeedbackSummary(canteen?.id, Number(days));
  const [qr, setQr] = useState(false);
  const s = summary.data;
  return (
    <div>
      <PageHeader
        title="Student feedback"
        sub={s ? `${fmtNum(s.responses)} ratings in ${s.days} days${s.response_rate != null ? `, from about ${fmtPct(s.response_rate, 1)} of diners` : ""}. Canteen average ${fmtNum(s.canteen_mean, 1)}/5.` : null}
        actions={
          <>
            <Select value={days} onChange={(e) => setDays(e.target.value)} aria-label="Period" className="w-40">
              <option value="14">Last 2 weeks</option>
              <option value="28">Last 4 weeks</option>
              <option value="90">Last 3 months</option>
            </Select>
            <Button icon={<QrCode className="size-4" />} onClick={() => setQr(true)}>
              QR codes
            </Button>
          </>
        }
      />
      <div className="grid gap-8 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div className="min-w-0">
          <Section title="Dishes" sub="Scores are smoothed toward the canteen average so a handful of votes cannot swing them; recent ratings count more.">
            {summary.isLoading ? (
              <Skeleton className="h-96 w-full" />
            ) : summary.error ? (
              <ErrorState error={summary.error} retry={() => summary.refetch()} />
            ) : (
              <>
                <div className="mb-2 flex flex-wrap gap-x-5 gap-y-1">
                  <LegendItem color={C.blue} kind="dot" label="Dish score" />
                  <LegendItem color="#A3B0AC" kind="line" label="Canteen average" />
                  <LegendItem color={C.blue} kind="bar" label="Liked" />
                  <LegendItem color={C.turmeric} kind="bar" label="Disliked" />
                </div>
                <ul className="rim divide-y divide-rim">
                  {s!.dishes.map((d) => (
                    <DishRow key={d.dish_id} d={d} mean={s!.canteen_mean ?? null} />
                  ))}
                </ul>
              </>
            )}
          </Section>
          <Suggestions />
          <RsvpChart />
        </div>
        <div className="min-w-0">
          <Votes />
          <Comments />
        </div>
      </div>
      {qr ? <QrDialog onClose={() => setQr(false)} /> : null}
    </div>
  );
}

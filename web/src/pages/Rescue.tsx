import { HandHeart } from "lucide-react";
import { Link } from "react-router";

import type { Schemas } from "@/api/client";
import { useImpact, useIncidents } from "@/api/hooks";
import { useAuth } from "@/app/auth";
import { fmtDateTime, fmtKg, fmtNum, fmtPct } from "@/lib/format";
import { Countdown } from "@/ui/overlays";
import { Badge, Button, EmptyState, ErrorState, Figure, PageHeader, Section, Skeleton } from "@/ui/primitives";

type Brief = Schemas["IncidentBrief"];

const STATUS: Record<string, { label: string; tone: "haldi" | "leaf" | "chilli" | "neutral" | "rain" }> = {
  detected: { label: "Checking", tone: "neutral" },
  open: { label: "Needs a place", tone: "haldi" },
  arranged: { label: "Place found", tone: "rain" },
  closed: { label: "Closed", tone: "neutral" },
  rejected: { label: "Below threshold", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export function StatusBadge({ status, route }: { status: string; route?: string }) {
  const s = STATUS[status] ?? { label: status, tone: "neutral" as const };
  if (status === "open" && route === "divert") return <Badge tone="chilli">Not for people: find animal feed or compost</Badge>;
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

function outcome(i: Brief) {
  const impact = (i.impact ?? {}) as { kg?: number; meals?: number; route?: string; places?: string[]; disposed_kg?: number };
  if (i.status === "closed" && impact.kg) {
    const where = (impact.places ?? i.places).join(", ") || "a nearby place";
    return impact.route === "people" ? `${fmtKg(impact.kg)} to ${where}, about ${fmtNum(impact.meals)} meals` : `${fmtKg(impact.kg)} to ${where} (animal feed or compost)`;
  }
  if (i.status === "closed") return `Nothing handed over; ${fmtKg(impact.disposed_kg ?? i.total_kg)} disposed`;
  if (i.status === "rejected") return "Logged; below the large-surplus threshold";
  if (i.status === "cancelled") return "Cancelled";
  return "";
}

export default function Rescue() {
  const { can } = useAuth();
  const active = useIncidents("active");
  const closed = useIncidents("closed");
  const impact = useImpact(90);
  const r = impact.data?.rescue;
  return (
    <div>
      <PageHeader
        title="Surplus rescue"
        sub="When cooked food is left over, FoodWise checks it is still safe and shows the places near you that can use it: community kitchens, langars, shelters, children's and elders' homes."
        actions={
          can("incident:create") ? (
            <Link to="/rescue/new">
              <Button variant="primary" icon={<HandHeart className="size-4" />}>
                Report surplus
              </Button>
            </Link>
          ) : null
        }
      />
      {r ? (
        <div className="mb-8 grid grid-cols-2 gap-6 sm:grid-cols-4">
          <Figure value={fmtKg(r.kg)} label="Rescued in 90 days" tone="leaf" />
          <Figure value={fmtNum(r.meals)} label="Meals, approximately" />
          <Figure value={r.rescue_rate != null ? fmtPct(r.rescue_rate) : "–"} label="Large surpluses given to people" />
          <Figure value={r.median_minutes_to_agree != null ? `${fmtNum(r.median_minutes_to_agree)} min` : "–"} label="Median time to find a place" />
        </div>
      ) : null}

      <Section title="In progress">
        {active.isLoading ? (
          <Skeleton className="h-24 w-full" />
        ) : active.error ? (
          <ErrorState error={active.error} retry={() => active.refetch()} />
        ) : !active.data?.length ? (
          <EmptyState title="No leftover food waiting">Report surplus from the kitchen log after a meal, or here.</EmptyState>
        ) : (
          <ul className="rim divide-y divide-rim">
            {active.data.map((i) => (
              <li key={i.id}>
                <Link to={`/rescue/${i.id}`} className="flex flex-wrap items-center gap-x-6 gap-y-2 px-5 py-4 hover:bg-white">
                  <div className="min-w-0 flex-1">
                    <p className="font-[620]">
                      {fmtNum(i.total_portions)} portions ({fmtKg(i.total_kg, 1)}) at {i.canteen}
                    </p>
                    <p className="text-sm text-muted">Reported {fmtDateTime(i.created_at)}</p>
                  </div>
                  <StatusBadge status={i.status} route={i.route} />
                  <p className="w-44 text-right text-sm text-ink-2">
                    {i.route === "people" && i.safe_until ? (
                      <>
                        Safe for <Countdown until={i.safe_until} />
                      </>
                    ) : i.places.length ? (
                      i.places.join(", ")
                    ) : null}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Past rescues">
        {closed.isLoading ? (
          <Skeleton className="h-48 w-full" />
        ) : closed.error ? (
          <ErrorState error={closed.error} retry={() => closed.refetch()} />
        ) : (
          <div className="rim overflow-x-auto">
            <table className="w-full min-w-[720px] text-base">
              <thead>
                <tr className="border-b border-rim text-left text-sm text-muted">
                  <th className="px-5 py-2.5 font-medium">Reported</th>
                  <th className="px-3 py-2.5 font-medium">Canteen</th>
                  <th className="px-3 py-2.5 text-right font-medium">Food</th>
                  <th className="px-3 py-2.5 font-medium">Outcome</th>
                  <th className="px-5 py-2.5 text-right font-medium">Time to find a place</th>
                </tr>
              </thead>
              <tbody>
                {closed.data?.map((i) => {
                  const impact = i.impact as { minutes_to_agree?: number; rescue_minutes?: number } | null;
                  const minutes = impact?.minutes_to_agree ?? impact?.rescue_minutes;
                  return (
                    <tr key={i.id} className="border-b border-steel-2 last:border-0 hover:bg-white">
                      <td className="px-5 py-3">
                        <Link to={`/rescue/${i.id}`} className="underline decoration-rim-strong underline-offset-4 hover:decoration-ink">
                          {fmtDateTime(i.created_at)}
                        </Link>
                      </td>
                      <td className="px-3 py-3 text-ink-2">{i.canteen}</td>
                      <td className="num px-3 py-3 text-right">{fmtKg(i.total_kg)}</td>
                      <td className="px-3 py-3 text-ink-2">{outcome(i)}</td>
                      <td className="num px-5 py-3 text-right text-ink-2">{minutes != null ? `${fmtNum(minutes)} min` : "–"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}

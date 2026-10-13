import { HandHeart } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { usePlacesHistory } from "@/api/hooks";
import { useCanteen } from "@/app/auth";
import { fmtDateTime, fmtKg, fmtNum } from "@/lib/format";
import { telUrl } from "@/lib/location";
import { PlaceFinder } from "@/ui/PlaceFinder";
import { Button, PageHeader, Section, Segmented, Skeleton } from "@/ui/primitives";

export default function Places() {
  const { canteen } = useCanteen();
  const [route, setRoute] = useState<"people" | "divert">("people");
  const given = usePlacesHistory();
  return (
    <div>
      <PageHeader
        title="Places nearby"
        sub="Who near you can use leftover food, from OpenStreetMap. Call a few before you need them: ask when they can collect and how much they can take."
        actions={
          <Link to="/rescue/new">
            <Button variant="primary" icon={<HandHeart className="size-4" />}>
              Report surplus
            </Button>
          </Link>
        }
      />
      <div className="grid gap-8 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="min-w-0">
          <div className="mb-4">
            <Segmented
              label="Kind of place"
              value={route}
              onChange={setRoute}
              options={[
                { value: "people", label: "Feed people" },
                { value: "divert", label: "Animal feed and compost" },
              ]}
            />
          </div>
          {canteen ? <PlaceFinder key={route} canteenId={canteen.id} canteenName={canteen.name} route={route} /> : null}
        </div>
        <div className="min-w-0">
          <Section title="Where you have given food" sub="Places you recorded in past rescues, most recent first.">
            {given.isLoading ? (
              <Skeleton className="h-40 w-full" />
            ) : !given.data?.length ? (
              <p className="text-base text-muted">Nothing yet. Places appear here after your first rescue.</p>
            ) : (
              <ul className="rim divide-y divide-rim">
                {given.data.map((g) => (
                  <li key={g.place_ref} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-3">
                    <div className="min-w-0">
                      <p className="font-[620]">{g.name}</p>
                      <p className="text-sm text-muted">
                        {g.kind_label}. {g.times ? `${fmtNum(g.times)} handover${g.times === 1 ? "" : "s"}, ${fmtKg(g.kg)}` : "No handover yet"}
                        {g.fell_through ? `, fell through ${g.fell_through}×` : ""}. Last {fmtDateTime(g.last)}.
                      </p>
                    </div>
                    {g.phone ? (
                      <a href={telUrl(g.phone)} className="text-sm font-medium text-rain underline underline-offset-4">
                        {g.phone}
                      </a>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}

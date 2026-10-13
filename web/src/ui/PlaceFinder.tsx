import { Clock, HandHeart, LocateFixed, MapPin, MessageCircle, Navigation, Phone, Search } from "lucide-react";
import { useMemo, useState } from "react";

import type { Schemas } from "@/api/client";
import { useNearbyPlaces } from "@/api/hooks";
import { fmtDateTime, fmtKg, fmtNum } from "@/lib/format";
import { directionsUrl, findNumberUrl, telUrl, useDeviceLocation, whatsappUrl } from "@/lib/location";

import { MapLegend, type MapPoint, MapView } from "./MapView";
import { Badge, Button, EmptyState, ErrorState, Select, Skeleton, cx } from "./primitives";

export type Place = Schemas["PlaceOut"];

const RADII = [5, 10, 15, 25];

function linkButton(className?: string) {
  return cx(
    "inline-flex h-9 items-center gap-1.5 rounded-[8px] border border-rim bg-plate px-3 text-sm font-medium text-ink hover:border-rim-strong hover:bg-white",
    className,
  );
}

function PlaceCard({
  p,
  shareText,
  onGive,
  highlight,
}: {
  p: Place;
  shareText: string;
  onGive?: (p: Place) => void;
  highlight: boolean;
}) {
  const open = p.open_now === true ? <Badge tone="leaf">Open now</Badge> : p.open_now === false ? <Badge tone="neutral">Closed now</Badge> : null;
  return (
    <li id={`place-${p.ref.replace(/[^a-z0-9]/gi, "-")}`} className={cx("px-4 py-4 sm:px-5", highlight && "bg-haldi-soft/40", !p.fits_window && "opacity-80")}>
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
        <div className="min-w-0">
          <p className="text-lg font-[650] leading-snug">{p.name}</p>
          <p className="text-sm text-muted">
            {p.kind_label}. {fmtNum(p.distance_km, 1)} km, about {fmtNum(p.eta_min)} min
            {p.address ? `. ${p.address}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {p.agreed_here ? <Badge tone="leaf">Taking food</Badge> : null}
          {p.history.given ? <Badge tone="rain">Given {p.history.given}×</Badge> : null}
          {open}
        </div>
      </div>
      {p.note ? <p className="mt-1.5 text-base text-ink-2">{p.note}</p> : null}
      {p.history.given ? (
        <p className="mt-1 text-sm text-ink-2">
          You gave {fmtKg(p.history.kg)} here in total{p.history.last ? `, last on ${fmtDateTime(p.history.last)}` : ""}.
        </p>
      ) : null}
      {p.notes.length ? (
        <ul className="mt-1.5 flex flex-col gap-0.5 text-sm text-muted">
          {p.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
      {p.opening_hours ? (
        <p className="mt-1 inline-flex items-center gap-1 text-sm text-muted">
          <Clock className="size-3.5" aria-hidden /> {p.opening_hours}
        </p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {p.phone ? (
          <>
            <a href={telUrl(p.phone)} className={linkButton()}>
              <Phone className="size-4" aria-hidden /> Call {p.phone}
            </a>
            <a href={whatsappUrl(p.phone, shareText)} target="_blank" rel="noreferrer" className={linkButton()}>
              <MessageCircle className="size-4" aria-hidden /> WhatsApp
            </a>
          </>
        ) : (
          <a href={findNumberUrl(p.name, p.address)} target="_blank" rel="noreferrer" className={linkButton()}>
            <Search className="size-4" aria-hidden /> Find their number
          </a>
        )}
        <a href={directionsUrl(p.lat, p.lon)} target="_blank" rel="noreferrer" className={linkButton()}>
          <Navigation className="size-4" aria-hidden /> Directions
        </a>
        {onGive && !p.agreed_here ? (
          <Button size="sm" variant="leaf" className="h-9" icon={<HandHeart className="size-4" />} onClick={() => onGive(p)}>
            They'll take some
          </Button>
        ) : null}
      </div>
    </li>
  );
}

export function PlaceFinder({
  canteenId,
  canteenName,
  incident,
  onGive,
  route,
}: {
  canteenId: string;
  canteenName: string;
  incident?: Schemas["IncidentDetail"];
  onGive?: (p: Place) => void;
  route?: "people" | "divert";
}) {
  const loc = useDeviceLocation();
  const [radius, setRadius] = useState<number | null>(null);
  const [kind, setKind] = useState<string>("all");
  const [showAll, setShowAll] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const query = useMemo(
    () => ({
      canteen_id: canteenId,
      lat: loc.coords ? Math.round(loc.coords.lat * 10000) / 10000 : null,
      lon: loc.coords ? Math.round(loc.coords.lon * 10000) / 10000 : null,
      radius_km: radius,
      incident_id: incident?.id ?? null,
      route: route ?? null,
    }),
    [canteenId, loc.coords, radius, incident?.id, route],
  );
  const near = useNearbyPlaces(query);
  const d = near.data;
  const places = (d?.places ?? []).filter((p) => kind === "all" || p.kind === kind);
  const shown = showAll ? places : places.slice(0, 8);
  const shareText =
    incident?.share_text ??
    `Namaste, this is the kitchen at ${canteenName}. Sometimes we have freshly cooked food left over after a meal. Could your organisation use it? We would call you first, and you would collect it from us.`;

  const points: MapPoint[] = d
    ? [
        { id: "canteen", lat: d.canteen_lat, lon: d.canteen_lon, label: canteenName, tone: "canteen" },
        ...(d.origin === "device" ? [{ id: "you", lat: d.origin_lat, lon: d.origin_lon, label: "You", tone: "you" as const }] : []),
        ...places.map((p) => ({
          id: p.ref,
          lat: p.lat,
          lon: p.lon,
          label: p.name,
          detail: `${p.kind_label}, ${fmtNum(p.distance_km, 1)} km`,
          tone: (p.agreed_here ? "agreed" : p.fits_window ? "place" : "far") as MapPoint["tone"],
        })),
      ]
    : [];
  const kinds = Object.entries(d?.counts ?? {}).sort((a, b) => b[1] - a[1]);
  const kindLabel = (k: string) => d?.places.find((p) => p.kind === k)?.kind_label ?? k;

  const select = (ref: string) => {
    setSelected(ref);
    const i = places.findIndex((p) => p.ref === ref);
    if (i >= 8) setShowAll(true);
    requestAnimationFrame(() => document.getElementById(`place-${ref.replace(/[^a-z0-9]/gi, "-")}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-[10px] bg-steel-2 px-4 py-3">
        <p className="flex min-w-0 items-start gap-2 text-base text-ink-2">
          {loc.state.status === "ready" ? <LocateFixed className="mt-0.5 size-4.5 shrink-0 text-[#3E6DB0]" aria-hidden /> : <MapPin className="mt-0.5 size-4.5 shrink-0 text-muted" aria-hidden />}
          <span>
            {loc.state.status === "ready"
              ? `Within ${d?.radius_km ?? "…"} km of you${loc.state.coords.accuracy > 200 ? ` (location accurate to about ${fmtNum(loc.state.coords.accuracy / 1000, 1)} km)` : ""}.`
              : `Within ${d?.radius_km ?? "…"} km of ${canteenName}.`}
            {loc.state.status === "denied" || loc.state.status === "unavailable" ? <span className="block text-sm text-muted">{loc.state.message}</span> : null}
          </span>
        </p>
        <div className="flex items-center gap-2">
          {loc.state.status !== "ready" ? (
            <Button size="sm" onClick={loc.request} busy={loc.state.status === "asking"} icon={<LocateFixed className="size-4" />}>
              Use my location
            </Button>
          ) : null}
          <label className="sr-only" htmlFor="radius">
            Search radius
          </label>
          <Select id="radius" className="h-8 w-28 text-sm" value={radius ?? d?.radius_km ?? 10} onChange={(e) => setRadius(Number(e.target.value))}>
            {RADII.filter((r) => !d || r <= d.max_radius_km).map((r) => (
              <option key={r} value={r}>
                {r} km
              </option>
            ))}
          </Select>
        </div>
      </div>

      {near.isLoading ? (
        <div className="relative">
          <Skeleton className="h-72 w-full" />
          <p className="absolute inset-x-0 top-1/2 -translate-y-1/2 text-center text-base text-ink-2" role="status">
            Looking for places on OpenStreetMap. The first search in an area can take up to half a minute.
          </p>
        </div>
      ) : near.error ? (
        <ErrorState error={near.error} retry={() => near.refetch()} />
      ) : d ? (
        <>
          {d.error ? <p className="rounded-[8px] bg-haldi-soft px-3 py-2 text-sm text-haldi-deep">{d.error}</p> : null}
          {d.places.length ? (
            <>
              <MapView
                points={points}
                center={[d.origin_lat, d.origin_lon]}
                height={300}
                accuracy={loc.coords && d.origin === "device" ? { lat: d.origin_lat, lon: d.origin_lon, m: loc.coords.accuracy } : null}
                onSelect={(id) => id !== "canteen" && id !== "you" && select(id)}
              />
              <MapLegend tones={["canteen", ...(d.origin === "device" ? (["you"] as const) : []), "place", ...(d.places.some((p) => !p.fits_window) ? (["far"] as const) : []), ...(d.places.some((p) => p.agreed_here) ? (["agreed"] as const) : [])]} />
              {kinds.length > 1 ? (
                <div className="flex flex-wrap gap-1.5" role="group" aria-label="Kind of place">
                  {[["all", d.places.length] as [string, number], ...kinds].map(([k, n]) => (
                    <button
                      key={k}
                      onClick={() => setKind(k)}
                      aria-pressed={kind === k}
                      className={cx(
                        "rounded-full border px-3 py-1 text-sm",
                        kind === k ? "border-ink bg-ink text-steel" : "border-rim bg-plate text-ink-2 hover:border-rim-strong",
                      )}
                    >
                      {k === "all" ? "All" : kindLabel(k)} <span className="num opacity-70">{n}</span>
                    </button>
                  ))}
                </div>
              ) : null}
              <ul className="rim divide-y divide-rim">
                {shown.map((p) => (
                  <PlaceCard key={p.ref} p={p} shareText={shareText} onGive={onGive} highlight={selected === p.ref} />
                ))}
              </ul>
              {places.length > shown.length ? (
                <Button variant="ghost" onClick={() => setShowAll(true)}>
                  Show all {places.length} places
                </Button>
              ) : null}
            </>
          ) : (
            <EmptyState title={d.route === "divert" ? "No animal shelter or compost site on the map nearby" : "No places that feed people on the map nearby"}>
              {radius !== d.max_radius_km ? "Search a wider area, or " : ""}
              {onGive ? "add a place you know below." : "ask local NGOs; places appear here once they are on OpenStreetMap."}
            </EmptyState>
          )}
          <p className="text-xs text-muted">
            {d.attribution}. Details on the map can be out of date, so call before sending food.
            {d.stale && d.fetched_at ? ` Saved ${fmtDateTime(d.fetched_at)}.` : ""}
          </p>
        </>
      ) : null}
    </div>
  );
}

import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Copy, MessageCircle, Phone, Plus, Radio, X } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { ApiError, type Schemas } from "@/api/client";
import { useIncident, useIncidentAction } from "@/api/hooks";
import { useAuth } from "@/app/auth";
import { fmtDateTime, fmtKg, fmtNum, fmtTime } from "@/lib/format";
import { telUrl } from "@/lib/location";
import { subscribe } from "@/lib/sse";
import { DishPhoto, useDishLookup } from "@/ui/DishPhoto";
import { Countdown, Dialog, useToast } from "@/ui/overlays";
import { type Place, PlaceFinder } from "@/ui/PlaceFinder";
import { Badge, Button, ErrorState, Field, Input, Section, Segmented, Select, Skeleton, Textarea, cx } from "@/ui/primitives";

import { StatusBadge } from "./Rescue";

type Detail = Schemas["IncidentDetail"];
type Donation = Schemas["DonationOut"];

const STEPS: { label: string; reached: (d: Detail) => boolean }[] = [
  { label: "Safety checked", reached: (d) => !["detected", "rejected"].includes(d.status) },
  { label: "A place agreed", reached: (d) => d.donations.some((x) => x.status !== "fell_through") },
  { label: "Handed over", reached: (d) => d.donations.some((x) => x.status === "handed_over") },
  { label: "Closed", reached: (d) => d.status === "closed" },
];

const PLACE_KINDS: [string, string][] = [
  ["community_kitchen", "Community kitchen or langar"],
  ["food_bank", "Food bank"],
  ["shelter", "Shelter"],
  ["children_home", "Children's home"],
  ["elder_home", "Elders' home"],
  ["ngo", "NGO or other"],
  ["animal_shelter", "Animal shelter or gaushala"],
  ["compost", "Compost or biogas"],
];

function headline(d: Detail) {
  const agreed = d.donations.filter((x) => x.status === "agreed");
  switch (d.status) {
    case "open":
      return d.route === "people" ? "Find a place that can use this food" : "Not safe for people now: offer it for animal feed or compost";
    case "arranged":
      return agreed.length ? `${agreed.map((x) => x.place_name).join(" and ")} will take it` : "Part handed over";
    case "closed": {
      const kg = (d.impact as { kg?: number } | null)?.kg ?? 0;
      return kg ? `${fmtKg(kg, 1)} handed over${d.route === "people" ? " to feed people" : ""}` : "Closed without a handover";
    }
    case "rejected":
      return "Logged, below the large-surplus threshold";
    case "cancelled":
      return "Cancelled";
    default:
      return "Checking safety";
  }
}

function GiveDialog({ d, place, onClose }: { d: Detail; place: Place | "manual"; onClose: () => void }) {
  const act = useIncidentAction();
  const toast = useToast();
  const manual = place === "manual";
  const [portions, setPortions] = useState(String(Math.round(d.remaining_portions)));
  const [method, setMethod] = useState<"call" | "whatsapp" | "in_person" | "other">(manual || !place.phone ? "in_person" : "call");
  const [contact, setContact] = useState("");
  const [notes, setNotes] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState(d.route === "people" ? "community_kitchen" : "animal_shelter");
  const [phone, setPhone] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const body: Schemas["DonationIn"] = {
      place: manual
        ? { name, kind, phone: phone || null }
        : { ref: place.ref, name: place.name, kind: place.kind, lat: place.lat, lon: place.lon, phone: place.phone, address: place.address, distance_km: place.distance_km },
      portions: Number(portions),
      contact_method: method,
      contact_name: contact || null,
      notes: notes || null,
    };
    try {
      await act.mutateAsync({ kind: "donate", id: d.id, body });
      toast("ok", `${manual ? name : place.name} will take ${portions} portions.`);
      onClose();
    } catch (err) {
      toast("error", err instanceof ApiError ? err.message : "Could not save.");
    }
  };
  const kinds = PLACE_KINDS.filter(([k]) => (d.route === "divert" ? ["animal_shelter", "compost"].includes(k) : true));
  return (
    <Dialog open onClose={onClose} title={manual ? "A place you know" : `${place.name} will take some`}>
      <form onSubmit={submit} className="flex flex-col gap-4">
        {manual ? (
          <>
            <Field label="Name of the place" htmlFor="pn">
              <Input id="pn" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Kind" htmlFor="pk">
                <Select id="pk" value={kind} onChange={(e) => setKind(e.target.value)}>
                  {kinds.map(([k, l]) => (
                    <option key={k} value={k}>
                      {l}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Phone (optional)" htmlFor="pp">
                <Input id="pp" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
              </Field>
            </div>
          </>
        ) : (
          <p className="text-base text-ink-2">Record this once they have said yes on the phone or in person.</p>
        )}
        <Field label="Portions they will take" htmlFor="po" hint={`${fmtNum(d.remaining_portions)} portions are not yet promised to anyone.`}>
          <Input id="po" inputMode="numeric" value={portions} onChange={(e) => setPortions(e.target.value.replace(/\D/g, ""))} className="num" />
        </Field>
        <div>
          <p className="mb-1.5 text-sm font-medium text-ink-2">How you reached them</p>
          <Segmented
            label="How you reached them"
            value={method}
            onChange={setMethod}
            options={[
              { value: "call", label: "Phone call" },
              { value: "whatsapp", label: "WhatsApp" },
              { value: "in_person", label: "In person" },
              { value: "other", label: "Other" },
            ]}
          />
        </div>
        <Field label="Who you spoke to (optional)" htmlFor="pc">
          <Input id="pc" value={contact} onChange={(e) => setContact(e.target.value)} maxLength={120} />
        </Field>
        <Field label="Note (optional)" htmlFor="pnote" hint="For example: collecting at 3:30 with their van.">
          <Textarea id="pnote" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={300} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="leaf" busy={act.isPending} disabled={!(Number(portions) > 0) || (manual && name.trim().length < 2)} icon={<Check className="size-4" />}>
            Save
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function HandoverDialog({ d, donation, onClose }: { d: Detail; donation: Donation; onClose: () => void }) {
  const act = useIncidentAction();
  const toast = useToast();
  const [kg, setKg] = useState(String(Math.round(d.total_kg * (donation.portions / Math.max(d.total_portions, 1)) * 10) / 10));
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const next = await act.mutateAsync({ kind: "handed-over", id: d.id, donationId: donation.id, weight_kg: Number(kg) });
      toast("ok", next.status === "closed" ? "Handed over. All the food has gone, and the impact is recorded." : `Handed over to ${donation.place_name}.`);
      onClose();
    } catch (err) {
      toast("error", err instanceof ApiError ? err.message : "Could not save.");
    }
  };
  return (
    <Dialog open onClose={onClose} title={`Handed over to ${donation.place_name}`}>
      <form onSubmit={submit} className="flex flex-col gap-4">
        <p className="text-base text-ink-2">Weigh what they took. The weight is what your impact report counts.</p>
        <Field label="Weight handed over (kg)" htmlFor="kg">
          <Input id="kg" inputMode="decimal" value={kg} onChange={(e) => setKg(e.target.value.replace(/[^0-9.]/g, ""))} className="qty h-12 text-2xl" />
        </Field>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="leaf" busy={act.isPending} disabled={!(Number(kg) > 0)} icon={<Check className="size-4" />}>
            Save handover
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function DonationRow({ d, x, onHandover }: { d: Detail; x: Donation; onHandover: () => void }) {
  const act = useIncidentAction();
  const toast = useToast();
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const tone = x.status === "handed_over" ? "leaf" : x.status === "agreed" ? "haldi" : "neutral";
  const label = { agreed: "Will collect", handed_over: "Handed over", fell_through: "Fell through" }[x.status] ?? x.status;
  const fall = async () => {
    try {
      await act.mutateAsync({ kind: "fell-through", id: d.id, donationId: x.id, reason: reason || undefined });
      toast("info", `Marked as fallen through. ${x.portions} portions are free again.`);
      setAsking(false);
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not save.");
    }
  };
  return (
    <li className="px-4 py-3.5 sm:px-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <p className="font-[620]">{x.place_name}</p>
          <p className="text-sm text-muted">
            {fmtNum(x.portions)} portions
            {x.weight_kg != null ? `, ${fmtKg(x.weight_kg, 1)} weighed` : ""}
            {x.distance_km != null ? `. ${fmtNum(x.distance_km, 1)} km away` : ""}
            {x.contact_name ? `. Spoke to ${x.contact_name}` : ""}
            {x.notes ? `. ${x.notes}` : ""}
          </p>
        </div>
        <Badge tone={tone}>{label}</Badge>
      </div>
      {x.status === "agreed" ? (
        <div className="mt-2.5 flex flex-wrap gap-2">
          <Button size="sm" variant="leaf" onClick={onHandover} icon={<Check className="size-4" />}>
            Handed over
          </Button>
          {x.place_phone ? (
            <a href={telUrl(x.place_phone)} className="inline-flex h-8 items-center gap-1.5 rounded-[8px] border border-rim bg-plate px-3 text-sm font-medium hover:border-rim-strong">
              <Phone className="size-3.5" aria-hidden /> Call
            </a>
          ) : null}
          {asking ? (
            <span className="flex flex-wrap items-center gap-2">
              <Input aria-label="What happened (optional)" placeholder="What happened (optional)" value={reason} onChange={(e) => setReason(e.target.value)} className="h-8 w-56 text-sm" />
              <Button size="sm" variant="danger" busy={act.isPending} onClick={fall}>
                Confirm
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setAsking(false)} aria-label="Keep it">
                <X className="size-4" />
              </Button>
            </span>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setAsking(true)}>
              They can't come
            </Button>
          )}
        </div>
      ) : null}
    </li>
  );
}

function ShareText({ d }: { d: Detail }) {
  const toast = useToast();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(d.share_text);
      toast("ok", "Message copied.");
    } catch {
      toast("error", "Could not copy. Select the text and copy it.");
    }
  };
  return (
    <Section title="Message to send" sub="Read it out on the phone, or send it by WhatsApp from a place's card.">
      <p className="rim-sunken select-all px-4 py-3 text-base leading-relaxed text-ink">{d.share_text}</p>
      <div className="mt-2 flex gap-2">
        <Button size="sm" onClick={copy} icon={<Copy className="size-4" />}>
          Copy
        </Button>
        <a href={`https://wa.me/?text=${encodeURIComponent(d.share_text)}`} target="_blank" rel="noreferrer" className="inline-flex h-8 items-center gap-1.5 rounded-[8px] border border-rim bg-plate px-3 text-sm font-medium hover:border-rim-strong">
          <MessageCircle className="size-4" aria-hidden /> Share on WhatsApp
        </a>
      </div>
    </Section>
  );
}

export default function IncidentPage() {
  const { id } = useParams();
  const inc = useIncident(id);
  const qc = useQueryClient();
  const { can } = useAuth();
  const act = useIncidentAction();
  const toast = useToast();
  const photo = useDishLookup();
  const [giving, setGiving] = useState<Place | "manual" | null>(null);
  const [handover, setHandover] = useState<Donation | null>(null);
  const [disposal, setDisposal] = useState<"" | "compost" | "animal_feed" | "biogas" | "bin">("");
  const [live, setLive] = useState(false);

  useEffect(() => {
    if (!id) return;
    const ctrl = new AbortController();
    subscribe(
      `/api/v1/incidents/${id}/events`,
      (event, data) => {
        if (event === "snapshot") {
          setLive(true);
          qc.setQueryData(["incident", id], data);
        }
      },
      ctrl.signal,
    );
    return () => ctrl.abort();
  }, [id, qc]);

  if (inc.isLoading) return <Skeleton className="h-96 w-full" />;
  if (inc.error) return <ErrorState error={inc.error} retry={() => inc.refetch()} />;
  const d = inc.data!;
  const active = ["open", "arranged"].includes(d.status);
  const waiting = d.donations.filter((x) => x.status === "agreed").length;
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      toast("ok", ok);
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "That didn't work.");
    }
  };

  return (
    <div>
      <Link to="/rescue" className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink">
        <ArrowLeft className="size-4" /> Surplus rescue
      </Link>
      <header className="mb-6 grid gap-4 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={d.status} route={d.route} />
            {live && active ? (
              <span className="inline-flex items-center gap-1 text-sm text-leaf">
                <Radio className="size-3.5" aria-hidden /> Live
              </span>
            ) : null}
          </div>
          <h1 className="mt-2 text-3xl font-[700]" style={{ fontStretch: "116%" }}>
            {headline(d)}
          </h1>
          <p className="mt-1.5 text-base text-muted">
            {fmtNum(d.total_portions)} portions, {fmtKg(d.total_kg, 1)} at {d.canteen}. Reported {fmtDateTime(d.created_at)}.
            {active && d.remaining_portions > 0 && d.remaining_portions < d.total_portions ? ` ${fmtNum(d.remaining_portions)} portions still unpromised.` : ""}
          </p>
        </div>
        {active && d.route === "people" && d.safe_until ? (
          <div className="md:text-right">
            <p className="text-sm text-muted">Safe to eat until {fmtTime(d.safe_until)}</p>
            <p className="qty text-4xl">
              <Countdown until={d.safe_until} />
            </p>
          </div>
        ) : null}
      </header>

      {d.status !== "rejected" && d.status !== "cancelled" ? (
        <ol className="mb-8 grid grid-cols-4 gap-2" aria-label="Progress">
          {STEPS.map((s) => {
            const reached = s.reached(d);
            return (
              <li key={s.label} className="flex flex-col gap-1.5">
                <span className={cx("h-1.5 rounded-full", reached ? (d.route === "people" ? "bg-leaf" : "bg-ink-2") : "bg-steel-2")} />
                <span className={cx("text-sm", reached ? "text-ink" : "text-muted")}>{s.label}</span>
              </li>
            );
          })}
        </ol>
      ) : null}

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <div className="min-w-0">
          {d.donations.length ? (
            <Section title="Who is taking it">
              <ul className="rim divide-y divide-rim">
                {d.donations.map((x) => (
                  <DonationRow key={x.id} d={d} x={x} onHandover={() => setHandover(x)} />
                ))}
              </ul>
            </Section>
          ) : null}

          {active && can("places:read") ? (
            <Section
              title={d.route === "people" ? "Places nearby that feed people" : "Animal shelters and compost sites nearby"}
              sub="Call first. When they say yes, record it here so everyone in the kitchen can see."
              actions={
                d.remaining_portions > 0 ? (
                  <Button size="sm" variant="ghost" icon={<Plus className="size-4" />} onClick={() => setGiving("manual")}>
                    A place you know
                  </Button>
                ) : null
              }
            >
              <PlaceFinder
                canteenId={d.canteen_id}
                canteenName={d.canteen}
                incident={d}
                route={d.route as "people" | "divert"}
                onGive={d.remaining_portions > 0 ? (p) => setGiving(p) : undefined}
              />
            </Section>
          ) : null}

          {active && can("incident:manage") ? (
            <Section title="Finish">
              <div className="flex flex-wrap items-end gap-3">
                <Field label="Anything left goes to" htmlFor="disp">
                  <Select id="disp" value={disposal} onChange={(e) => setDisposal(e.target.value as typeof disposal)} className="w-56">
                    <option value="">Nothing is left</option>
                    <option value="compost">Compost</option>
                    <option value="animal_feed">Animal feed</option>
                    <option value="biogas">Biogas</option>
                    <option value="bin">Bin</option>
                  </Select>
                </Field>
                <Button
                  disabled={waiting > 0}
                  busy={act.isPending}
                  onClick={() => run(() => act.mutateAsync({ kind: "close", id: d.id, disposal: disposal || null }), "Closed and impact recorded.")}
                >
                  Close this surplus
                </Button>
                <Button variant="ghost" onClick={() => run(() => act.mutateAsync({ kind: "cancel", id: d.id, reason: "Reported by mistake" }), "Cancelled.")}>
                  Reported by mistake
                </Button>
              </div>
              {waiting ? <p className="mt-2 text-sm text-muted">Mark each place above as handed over, or as unable to come, first.</p> : null}
            </Section>
          ) : null}
        </div>

        <div className="min-w-0">
          <Section title="Food">
            <ul className="rim divide-y divide-rim">
              {d.items.map((it, i) => {
                const item = it as { dish_id: string; name: string; qty: number; unit: string; kg: number; storage: string; safe_until?: string | null; route?: string; diet: string };
                return (
                  <li key={i} className="flex items-center gap-3 px-4 py-3 sm:px-5">
                    <DishPhoto dish={{ ...photo(item.dish_id, item.name), diet: item.diet }} size="md" showDiet />
                    <div className="min-w-0 flex-1">
                      <p className="font-[620]">{item.name}</p>
                      <p className="text-sm text-muted">
                        {fmtNum(item.qty, 1)} {item.unit}, {fmtKg(item.kg, 1)}, kept {item.storage.replace("_", " ")}
                      </p>
                    </div>
                    <p className={cx("text-right text-sm", item.route === "divert" ? "text-chilli" : "text-ink-2")}>
                      {item.route === "divert" ? "Not safe for people" : item.safe_until ? `Safe until ${fmtTime(item.safe_until)}` : ""}
                    </p>
                  </li>
                );
              })}
            </ul>
          </Section>
          {active ? <ShareText d={d} /> : null}
          <Section title="Timeline">
            <ol className="relative ml-2 border-l border-rim">
              {d.events.map((e) => (
                <li key={e.id} className="mb-4 ml-4">
                  <span className="absolute -left-[5px] mt-1.5 size-2.5 rounded-full border-2 border-plate bg-ink-2" aria-hidden />
                  <p className="text-base text-ink">{e.message}</p>
                  <p className="text-sm text-muted">
                    {fmtTime(e.at)}
                    {e.actor && e.actor !== "system" ? `, ${e.actor}` : ""}
                  </p>
                </li>
              ))}
            </ol>
            {d.impact ? (
              <p className="text-sm text-muted">
                Recorded impact: {fmtKg((d.impact as { kg?: number }).kg, 1)} handed over
                {(d.impact as { meals?: number }).meals ? `, about ${fmtNum((d.impact as { meals?: number }).meals)} meals` : ""},{" "}
                {fmtKg((d.impact as { co2e_kg?: number }).co2e_kg)} CO2e avoided.
              </p>
            ) : null}
          </Section>
        </div>
      </div>
      {giving ? <GiveDialog d={d} place={giving} onClose={() => setGiving(null)} /> : null}
      {handover ? <HandoverDialog d={d} donation={handover} onClose={() => setHandover(null)} /> : null}
    </div>
  );
}

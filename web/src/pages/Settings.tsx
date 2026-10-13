import { AlertTriangle, CheckCircle2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { ApiError } from "@/api/client";
import {
  useAudit,
  useCanteens,
  useChangePassword,
  useCreateWebhook,
  useDeleteWebhook,
  useModel,
  useModels,
  useOutbox,
  usePatchCanteen,
  usePatchTenant,
  useTenant,
  useWebhooks,
} from "@/api/hooks";
import { useAuth } from "@/app/auth";
import { percentileName } from "@/lib/dist";
import { fmtDateTime, fmtNum, titleCase } from "@/lib/format";
import { useToast } from "@/ui/overlays";
import { Badge, Button, ErrorState, Field, Input, PageHeader, Section, Select, Skeleton, Switch, Tabs } from "@/ui/primitives";

import { TradeOff } from "./ModelVersion";

type TabKey = "college" | "service" | "safety" | "rescue" | "account" | "integrations" | "audit" | "messages";

function useRun() {
  const toast = useToast();
  return async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      toast("ok", ok);
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not save.");
    }
  };
}

function College() {
  const tenant = useTenant();
  const patch = usePatchTenant();
  const { can } = useAuth();
  const run = useRun();
  const [name, setName] = useState("");
  useEffect(() => {
    if (tenant.data) setName(tenant.data.name);
  }, [tenant.data]);
  if (!tenant.data) return <Skeleton className="h-48 w-full" />;
  const t = tenant.data;
  const editable = can("tenant:write");
  return (
    <div className="flex max-w-2xl flex-col gap-5">
      <Field label="College name" htmlFor="name">
        <Input id="name" value={name} onChange={(e) => setName(e.target.value)} disabled={!editable} />
      </Field>
      <Field label="How meals are served" htmlFor="mode" hint="Tells FoodWise which forecasting approaches make sense.">
        <Select id="mode" value={t.service_mode} disabled={!editable} onChange={(e) => run(() => patch.mutateAsync({ service_mode: e.target.value as "choice" }), "Saved.")}>
          <option value="fixed_thali">Fixed thali: everyone gets the same plate</option>
          <option value="choice">Choice: several dishes compete</option>
          <option value="counter">Counter: a la carte</option>
        </Select>
      </Field>
      <Field label="Student feedback" htmlFor="fb">
        <Select id="fb" value={t.feedback_mode} disabled={!editable} onChange={(e) => run(() => patch.mutateAsync({ feedback_mode: e.target.value as "anonymous" }), "Saved.")}>
          <option value="anonymous">Anonymous (recommended)</option>
          <option value="verified">Verified with college sign-in</option>
        </Select>
      </Field>
      <div className="rim-sunken px-4 py-3">
        <Switch
          checked={t.data_sharing === "anonymized_pool"}
          disabled={!editable}
          onChange={(v) => run(() => patch.mutateAsync({ data_sharing: v ? "anonymized_pool" : "none" }), v ? "Opted in to anonymised pooling." : "Opted out.")}
          label="Share anonymised data with other colleges"
        />
        <p className="mt-2 text-sm text-muted">Helps new colleges start with better plans and unlocks benchmarks. Off by default; no names, menus or student data leave the college.</p>
      </div>
      {editable ? (
        <div>
          <Button variant="primary" busy={patch.isPending} disabled={name === t.name || !name} onClick={() => run(() => patch.mutateAsync({ name }), "Saved.")}>
            Save changes
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function ServiceLevel() {
  const canteens = useCanteens();
  const tenant = useTenant();
  const patch = usePatchCanteen();
  const { can } = useAuth();
  const run = useRun();
  const [selected, setSelected] = useState<string>("");
  const [ratio, setRatio] = useState(5);
  const list = canteens.data ?? [];
  const current = list.find((c) => c.id === selected) ?? list[0];
  const defaultRatio = Number((tenant.data?.settings.costs as { shortage_to_waste_ratio?: number } | undefined)?.shortage_to_waste_ratio ?? 5);
  useEffect(() => {
    if (current) setRatio(Number((current.costs as { shortage_to_waste_ratio?: number } | null)?.shortage_to_waste_ratio ?? defaultRatio));
  }, [current, defaultRatio]);
  const models = useModels(current?.id);
  const champ = models.data?.find((m) => m.stage === "champion" || m.stage === "degraded");
  const detail = useModel(champ?.id);
  const cr = ratio / (1 + ratio);
  if (!current) return <Skeleton className="h-48 w-full" />;
  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <div className="flex flex-col gap-5">
        <Select aria-label="Canteen" value={current.id} onChange={(e) => setSelected(e.target.value)}>
          {list.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </Select>
        <div>
          <p className="text-base text-ink-2">Running out of a dish is how many times worse than wasting one portion?</p>
          <input type="range" min={0.5} max={15} step={0.5} value={ratio} disabled={!can("tenant:write")} onChange={(e) => setRatio(Number(e.target.value))} className="mt-3 w-full accent-[#1C2A4A]" aria-label="Cost of running short compared with wasting" />
          <p className="qty mt-2 text-3xl">{fmtNum(ratio, 1)}×</p>
          <p className="mt-2 text-base text-ink-2">
            So FoodWise cooks at the <span className="font-[620]">{percentileName(cr)}</span> of each forecast: on a typical day, demand will be below the plan {Math.round(cr * 100)} times in 100.
          </p>
          <p className="mt-2 text-sm text-muted">A prepaid hostel mess that must never run out might choose 9 (the 90th percentile). A counter canteen where a sold-out dish just means another choice might choose 2 to 4.</p>
        </div>
        {can("tenant:write") ? (
          <div>
            <Button variant="primary" busy={patch.isPending} onClick={() => run(() => patch.mutateAsync({ id: current.id, body: { shortage_to_waste_ratio: ratio } }), `Saved. ${current.name}'s next plan cooks at the ${percentileName(cr)}.`)}>
              Save for {current.name}
            </Button>
          </div>
        ) : null}
      </div>
      <div>
        {detail.data ? (
          <TradeOff points={((detail.data.metrics as { tradeoff?: unknown }).tradeoff as Parameters<typeof TradeOff>[0]["points"]) ?? []} current={cr} />
        ) : (
          <Skeleton className="h-72 w-full" />
        )}
      </div>
    </div>
  );
}

function Safety() {
  const tenant = useTenant();
  const patch = usePatchTenant();
  const { can } = useAuth();
  const run = useRun();
  const [windows, setWindows] = useState<Record<string, Record<string, number>>>({});
  const safety = tenant.data?.settings.safety as { reviewed?: boolean; reviewed_by?: string | null; windows_h: Record<string, Record<string, number>>; handling_buffer_min: number; min_useful_window_min: number } | undefined;
  useEffect(() => {
    if (safety) setWindows(safety.windows_h);
  }, [safety]);
  if (!safety) return <Skeleton className="h-64 w-full" />;
  const dirty = JSON.stringify(windows) !== JSON.stringify(safety.windows_h);
  return (
    <div className="flex flex-col gap-5">
      {safety.reviewed ? (
        <p className="flex items-center gap-2 rounded-[10px] bg-leaf-soft px-4 py-3 text-base text-leaf">
          <CheckCircle2 className="size-5" aria-hidden /> Reviewed by {safety.reviewed_by ?? "the food-safety officer"}.
        </p>
      ) : (
        <p className="flex items-start gap-2 rounded-[10px] bg-haldi-soft px-4 py-3 text-base text-haldi-deep">
          <AlertTriangle className="mt-0.5 size-5 shrink-0" aria-hidden /> These are conservative defaults. Your food-safety officer must review them against local rules (in India, the FSSAI surplus food regulations) before donations go out.
        </p>
      )}
      <div className="rim overflow-x-auto">
        <table className="w-full min-w-[520px] text-base">
          <thead>
            <tr className="border-b border-rim text-left text-sm text-muted">
              <th className="px-5 py-2.5 font-medium">Food</th>
              <th className="px-3 py-2.5 font-medium">Kept hot</th>
              <th className="px-3 py-2.5 font-medium">In the fridge</th>
              <th className="px-3 py-2.5 font-medium">Room temperature</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(windows).map(([cat, w]) => (
              <tr key={cat} className="border-b border-steel-2 last:border-0">
                <td className="px-5 py-2">{titleCase(cat)}</td>
                {(["hot_held", "chilled", "ambient"] as const).map((st) => (
                  <td key={st} className="px-3 py-2">
                    <div className="flex items-center gap-1.5">
                      <Input
                        aria-label={`${cat} ${st} hours`}
                        inputMode="decimal"
                        value={String(w[st] ?? 0)}
                        disabled={!can("tenant:write")}
                        onChange={(e) => setWindows((x) => ({ ...x, [cat]: { ...x[cat], [st]: Number(e.target.value.replace(/[^0-9.]/g, "") || 0) } }))}
                        className="num h-9 w-20"
                      />
                      <span className="text-sm text-muted">h</span>
                    </div>
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-sm text-muted">
        Hours from cooking until food is no longer given to people. A place must be able to collect it {safety.handling_buffer_min} minutes before that; with less than {safety.min_useful_window_min} minutes left, FoodWise suggests animal feed or compost instead. 0 means never given to people in that state.
      </p>
      {can("tenant:write") ? (
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" disabled={!dirty} busy={patch.isPending} onClick={() => run(() => patch.mutateAsync({ settings: { safety: { windows_h: windows } } }), "Saved. Ask your food-safety officer to review the new windows.")}>
            Save windows
          </Button>
          {!safety.reviewed ? (
            <Button busy={patch.isPending} onClick={() => run(() => patch.mutateAsync({ settings: { safety: { reviewed: true } } }), "Marked as reviewed.")}>
              Mark as reviewed by me
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function RescueSettings() {
  const tenant = useTenant();
  const patch = usePatchTenant();
  const { can } = useAuth();
  const run = useRun();
  const t = tenant.data?.settings;
  const [minKg, setMinKg] = useState("5");
  const [minPortions, setMinPortions] = useState("20");
  useEffect(() => {
    if (t) {
      const th = t.thresholds as { surplus_min_kg: number; surplus_min_portions: number };
      setMinKg(String(th.surplus_min_kg));
      setMinPortions(String(th.surplus_min_portions));
    }
  }, [t]);
  const places = (t?.places ?? {}) as { radius_km?: number; max_radius_km?: number; min_useful_portions?: number };
  if (!t) return <Skeleton className="h-48 w-full" />;
  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <div className="flex flex-col gap-4">
        <p className="text-base text-ink-2">A surplus counts as large, and FoodWise suggests places to give it to, when it reaches the larger of these and the canteen's usual 95th-percentile leftover. Smaller amounts are logged to improve the next plan.</p>
        <div className="grid grid-cols-2 gap-3">
          <Field label="At least (kg)" htmlFor="minkg">
            <Input id="minkg" inputMode="decimal" value={minKg} onChange={(e) => setMinKg(e.target.value)} disabled={!can("tenant:write")} />
          </Field>
          <Field label="Or at least (portions)" htmlFor="minp">
            <Input id="minp" inputMode="numeric" value={minPortions} onChange={(e) => setMinPortions(e.target.value)} disabled={!can("tenant:write")} />
          </Field>
        </div>
        {can("tenant:write") ? (
          <div>
            <Button variant="primary" busy={patch.isPending} onClick={() => run(() => patch.mutateAsync({ settings: { thresholds: { surplus_min_kg: Number(minKg), surplus_min_portions: Number(minPortions) } } }), "Saved.")}>
              Save thresholds
            </Button>
          </div>
        ) : null}
      </div>
      <PlaceSettings key={`${places.radius_km}-${places.min_useful_portions}`} initial={places} />
    </div>
  );
}

function PlaceSettings({ initial }: { initial: { radius_km?: number; max_radius_km?: number; min_useful_portions?: number } }) {
  const patch = usePatchTenant();
  const { can } = useAuth();
  const run = useRun();
  const [radius, setRadius] = useState(String(initial.radius_km ?? 10));
  const [minUseful, setMinUseful] = useState(String(initial.min_useful_portions ?? 10));
  return (
    <div className="flex flex-col gap-4">
      <p className="text-base text-ink-2">
        Places nearby come from OpenStreetMap, around the phone or laptop being used (or the canteen, if location is off). Nobody has to register with FoodWise; staff call the place and record who agreed.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Search within (km)" htmlFor="rad">
          <Select id="rad" value={radius} onChange={(e) => setRadius(e.target.value)} disabled={!can("tenant:write")}>
            {[2, 5, 10, 15, 25].map((r) => (
              <option key={r} value={r}>
                {r} km
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Smallest useful donation (portions)" htmlFor="mu">
          <Input id="mu" inputMode="numeric" value={minUseful} onChange={(e) => setMinUseful(e.target.value.replace(/\D/g, ""))} disabled={!can("tenant:write")} />
        </Field>
      </div>
      {can("tenant:write") ? (
        <div>
          <Button busy={patch.isPending} onClick={() => run(() => patch.mutateAsync({ settings: { places: { radius_km: Number(radius), min_useful_portions: Number(minUseful) } } }), "Saved.")}>
            Save place search
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function Account() {
  const { me } = useAuth();
  const change = useChangePassword();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const mismatch = repeat.length > 0 && next !== repeat;
  const submit = async () => {
    try {
      await change.mutateAsync({ current_password: current, new_password: next });
      setCurrent("");
      setNext("");
      setRepeat("");
      toast("ok", "Password changed. Other phones and computers using this account are signed out.");
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Could not change the password.");
    }
  };
  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <div>
        <p className="text-base text-ink-2">
          Everyone in {me?.tenant?.name ?? "your organisation"} signs in with <span className="font-medium text-ink">{me?.user.email}</span>. Share the password with the people who plan and log food, and change it when someone leaves.
        </p>
        <p className="mt-3 text-sm text-muted">Every change is still recorded in the audit trail with the time and device address.</p>
      </div>
      <form
        className="flex max-w-sm flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Field label="Current password" htmlFor="pw-cur">
          <Input id="pw-cur" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </Field>
        <Field label="New password" htmlFor="pw-new" hint="At least 10 characters.">
          <Input id="pw-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <Field label="New password again" htmlFor="pw-rep" error={mismatch ? "The two new passwords are different." : undefined}>
          <Input id="pw-rep" type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
        </Field>
        <div>
          <Button type="submit" variant="primary" busy={change.isPending} disabled={!current || next.length < 10 || next !== repeat}>
            Change password
          </Button>
        </div>
      </form>
    </div>
  );
}

function Integrations() {
  const hooks = useWebhooks();
  const create = useCreateWebhook();
  const del = useDeleteWebhook();
  const run = useRun();
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState<string | null>(null);
  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <p className="text-base text-ink-2">
        Send FoodWise events to your mess software. Each request is signed: verify the <code className="rounded bg-steel-2 px-1">X-FoodWise-Signature</code> header (HMAC-SHA256 of the body with your secret).
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-72 flex-1">
          <Field label="Endpoint URL" htmlFor="wh">
            <Input id="wh" placeholder="https://mess.example.edu/foodwise" value={url} onChange={(e) => setUrl(e.target.value)} />
          </Field>
        </div>
        <Button
          variant="primary"
          busy={create.isPending}
          disabled={!url}
          onClick={async () => {
            try {
              const out = await create.mutateAsync({ url, events: [] });
              setSecret(out.secret);
              setUrl("");
            } catch (e) {
              run(() => Promise.reject(e), "");
            }
          }}
        >
          Add webhook
        </Button>
      </div>
      {secret ? (
        <div className="rounded-[10px] bg-haldi-soft px-4 py-3">
          <p className="text-base text-haldi-deep">Signing secret, shown once. Store it in your mess software's configuration.</p>
          <code className="mt-1 block break-all text-sm">{secret}</code>
        </div>
      ) : null}
      <ul className="rim divide-y divide-rim">
        {(hooks.data ?? []).map((h) => (
          <li key={h.id} className="flex flex-wrap items-center justify-between gap-2 px-5 py-3">
            <div className="min-w-0">
              <p className="truncate font-medium">{h.url}</p>
              <p className="text-sm text-muted">{h.events.length ? h.events.join(", ") : "All events"}. Last: {(h.recent[0] as { status?: string } | undefined)?.status ?? "none yet"}</p>
            </div>
            <Button size="sm" variant="ghost" icon={<Trash2 className="size-4" />} onClick={() => run(() => del.mutateAsync(h.id), "Webhook removed.")}>
              Remove
            </Button>
          </li>
        ))}
        {!hooks.data?.length ? <li className="px-5 py-4 text-muted">No webhooks yet.</li> : null}
      </ul>
      <p className="text-sm text-muted">Events: forecast.generated, forecast.approved, incident.created, incident.claimed, incident.closed, model.promoted.</p>
    </div>
  );
}

function Audit() {
  const audit = useAudit();
  if (audit.isLoading) return <Skeleton className="h-64 w-full" />;
  if (audit.error) return <ErrorState error={audit.error} />;
  return (
    <div>
      <p className="mb-3 text-sm text-muted">Every sign-in, decision, override, rescue action and export. The database refuses edits and deletions to this log.</p>
      <ul className="rim divide-y divide-rim">
        {audit.data!.items.map((a) => (
          <li key={a.id} className="grid gap-1 px-5 py-2.5 sm:grid-cols-[150px_200px_1fr]">
            <span className="text-sm text-muted">{fmtDateTime(a.at)}</span>
            <span className="truncate text-sm">{a.actor.split(" <")[0]}</span>
            <span className="text-sm text-ink-2">
              <Badge>{a.action}</Badge>{" "}
              {a.meta && Object.keys(a.meta).length ? <span className="text-muted">{JSON.stringify(a.meta).slice(0, 120)}</span> : null}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Messages() {
  const out = useOutbox();
  if (out.isLoading) return <Skeleton className="h-64 w-full" />;
  return (
    <div>
      <p className="mb-3 text-sm text-muted">Emails FoodWise sent or would send, such as the evening prep-plan digest. Email is sent when SMTP is configured.</p>
      <ul className="flex flex-col gap-2">
        {(out.data ?? []).map((m) => (
          <li key={m.id} className="rim-sunken px-4 py-3">
            <div className="flex flex-wrap justify-between gap-2 text-sm text-muted">
              <span>
                {m.channel === "whatsapp" ? "WhatsApp" : m.channel.toUpperCase()} to {m.recipient}
              </span>
              <span>
                {fmtDateTime(m.created_at)}, {m.status}
              </span>
            </div>
            {m.subject ? <p className="mt-1 font-medium">{m.subject}</p> : null}
            <p className="text-base text-ink-2">{m.body}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function Settings() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as TabKey) ?? "college";
  const tabs: { value: TabKey; label: string; show: boolean }[] = [
    { value: "college", label: "Organisation", show: true },
    { value: "service", label: "Service level", show: true },
    { value: "safety", label: "Food safety", show: true },
    { value: "rescue", label: "Rescue", show: true },
    { value: "account", label: "Sign-in", show: true },
    { value: "integrations", label: "Integrations", show: can("webhooks:manage") },
    { value: "audit", label: "Audit trail", show: can("audit:read") },
    { value: "messages", label: "Message log", show: can("outbox:read") },
  ];
  return (
    <div>
      <PageHeader title="Settings" />
      <Tabs value={tab} onChange={(v) => setParams({ tab: v })} tabs={tabs.filter((t) => t.show).map(({ value, label }) => ({ value, label }))} />
      <div className="mt-6">
        <Section title={tabs.find((t) => t.value === tab)?.label ?? ""}>
          {tab === "college" ? <College /> : null}
          {tab === "service" ? <ServiceLevel /> : null}
          {tab === "safety" ? <Safety /> : null}
          {tab === "rescue" ? <RescueSettings /> : null}
          {tab === "account" ? <Account /> : null}
          {tab === "integrations" ? <Integrations /> : null}
          {tab === "audit" ? <Audit /> : null}
          {tab === "messages" ? <Messages /> : null}
        </Section>
      </div>
    </div>
  );
}

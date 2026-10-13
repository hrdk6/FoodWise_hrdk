import { Download, Upload } from "lucide-react";
import { type DragEvent, useMemo, useRef, useState } from "react";

import { ApiError, type Schemas, api, download, unwrap } from "@/api/client";
import { useAddCalendar, useCalendar, useCommitImport, useDishes, useImports, useReadiness, useValidateImport } from "@/api/hooks";
import { useAuth, useCanteen } from "@/app/auth";
import { addDays, fmtDateTime, fmtDay, fmtNum, titleCase } from "@/lib/format";
import { useToast } from "@/ui/overlays";
import { Badge, Button, ErrorState, Field, Input, Meter, PageHeader, Section, Select, Skeleton, Switch, cx } from "@/ui/primitives";

type Job = Schemas["ImportOut"];

const FIELDS: { key: string; label: string; required?: boolean }[] = [
  { key: "date", label: "Date", required: true },
  { key: "canteen", label: "Canteen" },
  { key: "meal", label: "Meal", required: true },
  { key: "dish", label: "Dish", required: true },
  { key: "unit", label: "Unit" },
  { key: "prepared_qty", label: "Prepared (cooked)", required: true },
  { key: "served_qty", label: "Served", required: true },
  { key: "leftover_qty", label: "Left over" },
  { key: "footfall", label: "Diners" },
  { key: "sold_out", label: "Sold out" },
  { key: "weather", label: "Weather" },
  { key: "event", label: "Event" },
];

function Readiness() {
  const r = useReadiness();
  if (r.isLoading) return <Skeleton className="h-32 w-full" />;
  if (r.error) return <ErrorState error={r.error} />;
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {r.data!.map((c) => (
        <div key={c.canteen_id} className="rim px-5 py-4">
          <div className="flex items-baseline justify-between gap-2">
            <p className="font-[640]">{c.canteen}</p>
            <Badge tone={c.tier >= 2 ? "leaf" : c.tier === 1 ? "rain" : "haldi"}>
              Tier {c.tier}: {c.tier_name}
            </Badge>
          </div>
          <p className="mt-1 text-sm text-ink-2">{c.gives}</p>
          <div className="mt-3">
            <Meter value={c.next_tier_days ? c.progress : 1} tone={c.tier >= 2 ? "leaf" : "haldi"} label="Progress to the next tier" />
          </div>
          <p className="mt-2 text-sm text-muted">
            {c.message}
            {c.quality_score != null ? ` Data quality ${fmtNum(c.quality_score)}/100.` : ""}
            {c.last_log ? ` Last log ${fmtDay(c.last_log)}.` : ""}
          </p>
        </div>
      ))}
    </div>
  );
}

function Wizard() {
  const { canteens } = useCanteen();
  const toast = useToast();
  const imports = useImports();
  const dishes = useDishes();
  const validate = useValidateImport();
  const commit = useCommitImport();
  const [job, setJob] = useState<Job | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [defaultCanteen, setDefaultCanteen] = useState(canteens[0]?.id ?? "");
  const [matches, setMatches] = useState<Record<string, string>>({});
  const [createNew, setCreateNew] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const upload = async (file: File) => {
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const out = await unwrap(api.POST("/api/v1/imports", { body: form as unknown as { file: string }, bodySerializer: (b) => b as unknown as FormData }));
      setJob(out);
      setMapping(out.mapping);
      setMatches({});
      imports.refetch();
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) upload(f);
  };
  const runValidate = async () => {
    if (!job) return;
    try {
      const out = await validate.mutateAsync({ id: job.id, body: { mapping, default_canteen_id: defaultCanteen || null, dish_matches: matches } });
      setJob(out);
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Validation failed.");
    }
  };
  const runCommit = async () => {
    if (!job) return;
    try {
      const out = await commit.mutateAsync({ id: job.id, create: createNew });
      setJob(out);
      toast("ok", `${fmtNum(out.committed_rows)} rows imported. Training a model on the new history has started.`);
    } catch (e) {
      toast("error", e instanceof ApiError ? e.message : "Import failed.");
    }
  };

  const report = job?.report as
    | {
        score: number;
        rows: number;
        valid_rows: number;
        counts: Record<string, number>;
        issues: { row: number | null; level: string; code: string; message: string }[];
        date_range: [string, string] | null;
        days: number;
        dish_matches: { raw: string; dish_id: string | null; dish_name: string | null; score: number; status: string }[];
        readiness_after: Record<string, { tier: number; name: string; days: number; message: string }>;
        committed_rows?: number;
        skipped_rows?: number;
      }
    | undefined;
  const step = !job ? 1 : job.status === "uploaded" ? 2 : job.status === "validated" ? 3 : 4;
  const steps = ["Upload a file", "Match the columns", "Check the report", "Import"];
  const dishOptions = useMemo(() => dishes.data ?? [], [dishes.data]);

  return (
    <div>
      <ol className="mb-5 grid grid-cols-4 gap-2" aria-label="Import steps">
        {steps.map((s, i) => (
          <li key={s} className="flex flex-col gap-1.5">
            <span className={cx("h-1.5 rounded-full", i + 1 <= step ? "bg-ink" : "bg-steel-2")} />
            <span className={cx("text-sm", i + 1 === step ? "font-medium text-ink" : "text-muted")}>
              {i + 1}. {s}
            </span>
          </li>
        ))}
      </ol>

      {step === 1 ? (
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cx("flex flex-col items-start gap-3 rounded-[12px] border-2 border-dashed px-6 py-8", dragging ? "border-ink bg-white" : "border-rim-strong")}
        >
          <p className="text-lg font-[620]">Drop a CSV or Excel file with your service history</p>
          <p className="max-w-2xl text-base text-muted">
            One row per dish per meal per day: what was prepared, what was served, and what was left. Column names don't have to match; you'll map them next. Up to 10 MB.
          </p>
          <div className="flex flex-wrap gap-2">
            <input ref={fileRef} type="file" accept=".csv,.xlsx,.txt" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            <Button variant="primary" icon={<Upload className="size-4" />} busy={uploading} onClick={() => fileRef.current?.click()}>
              Choose a file
            </Button>
            <Button variant="ghost" icon={<Download className="size-4" />} onClick={() => download("/api/v1/imports/template.csv", "foodwise_import_template.csv")}>
              Download the template
            </Button>
          </div>
        </div>
      ) : null}

      {step === 2 && job ? (
        <div className="flex flex-col gap-5">
          <p className="text-base text-ink-2">
            {job.filename}: {fmtNum(job.row_count)} rows. FoodWise guessed the columns below; correct any that are wrong.
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {FIELDS.map((f) => (
              <Field key={f.key} label={`${f.label}${f.required ? "" : " (optional)"}`} htmlFor={`map-${f.key}`}>
                <Select id={`map-${f.key}`} value={mapping[f.key] ?? ""} onChange={(e) => setMapping((m) => ({ ...m, [f.key]: e.target.value || null }))}>
                  <option value="">Not in this file</option>
                  {job.headers.map((h) => (
                    <option key={h} value={h}>
                      {h}
                    </option>
                  ))}
                </Select>
              </Field>
            ))}
            <Field label="Canteen for rows without one" htmlFor="defc">
              <Select id="defc" value={defaultCanteen} onChange={(e) => setDefaultCanteen(e.target.value)}>
                {canteens.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <div className="rim overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-rim text-left text-muted">
                  {job.headers.map((h) => (
                    <th key={h} className="whitespace-nowrap px-3 py-2 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {job.preview.slice(0, 5).map((r, i) => (
                  <tr key={i} className="border-b border-steel-2 last:border-0">
                    {r.map((c, j) => (
                      <td key={j} className="whitespace-nowrap px-3 py-1.5">
                        {c}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex gap-2">
            <Button variant="primary" busy={validate.isPending} onClick={runValidate}>
              Check the data
            </Button>
            <Button variant="ghost" onClick={() => setJob(null)}>
              Start again
            </Button>
          </div>
        </div>
      ) : null}

      {step >= 3 && job && report ? (
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
            <div>
              <p className={cx("qty text-3xl", report.score >= 90 ? "text-leaf" : report.score >= 70 ? "text-haldi-deep" : "text-chilli")}>{fmtNum(report.score)}</p>
              <p className="mt-1 text-sm text-muted">Data quality score</p>
            </div>
            <div>
              <p className="qty text-3xl">{fmtNum(report.valid_rows)}</p>
              <p className="mt-1 text-sm text-muted">of {fmtNum(report.rows)} rows usable</p>
            </div>
            <div>
              <p className="qty text-3xl">{fmtNum(report.days)}</p>
              <p className="mt-1 text-sm text-muted">{report.date_range ? `days, ${fmtDay(report.date_range[0])} to ${fmtDay(report.date_range[1])}` : "days"}</p>
            </div>
            <div>
              <p className="qty text-3xl">{fmtNum(report.counts.error ?? 0)}</p>
              <p className="mt-1 text-sm text-muted">rows with errors, {fmtNum(report.counts.warning ?? 0)} warnings</p>
            </div>
          </div>
          {Object.entries(report.readiness_after ?? {}).map(([cid, r]) => (
            <p key={cid} className="rounded-[10px] bg-leaf-soft px-4 py-3 text-base text-leaf">
              After this import, {canteens.find((c) => c.id === cid)?.name ?? "the canteen"} reaches tier {r.tier} ({r.name}). {r.message}
            </p>
          ))}

          <Section title="Dish names" sub="Confirm suggested matches; anything new becomes a dish. Spelling variations like 'Veg Pulao' and 'Vegetable pulao' are matched for you.">
            <div className="rim divide-y divide-rim">
              {report.dish_matches.map((m) => (
                <div key={m.raw} className="grid items-center gap-3 px-4 py-2.5 sm:grid-cols-[1fr_auto_1.2fr]">
                  <p className="font-medium">{m.raw || "(blank)"}</p>
                  <Badge tone={m.status === "exact" || m.status === "confirmed" ? "leaf" : m.status === "suggested" ? "haldi" : "neutral"}>
                    {m.status === "suggested" ? `Suggested, ${fmtNum(m.score * 100)}% alike` : titleCase(m.status)}
                  </Badge>
                  {step === 3 ? (
                    <Select aria-label={`Match for ${m.raw}`} value={matches[m.raw] ?? m.dish_id ?? "new"} onChange={(e) => setMatches((x) => ({ ...x, [m.raw]: e.target.value }))}>
                      <option value="new">Create a new dish</option>
                      {dishOptions.map((d) => (
                        <option key={d.id} value={d.id}>
                          {d.name}
                        </option>
                      ))}
                    </Select>
                  ) : (
                    <p className="text-sm text-ink-2">{m.dish_name ?? "New dish"}</p>
                  )}
                </div>
              ))}
            </div>
          </Section>

          {report.issues.length ? (
            <Section title="Problems found" sub="Rows with errors are skipped; warnings are imported but flagged.">
              <ul className="rim max-h-72 divide-y divide-rim overflow-y-auto">
                {report.issues.slice(0, 120).map((i, k) => (
                  <li key={k} className="flex gap-3 px-4 py-2 text-sm">
                    <Badge tone={i.level === "error" ? "chilli" : i.level === "warning" ? "haldi" : "neutral"}>{titleCase(i.level)}</Badge>
                    <span className="num w-16 shrink-0 text-muted">{i.row ? `Row ${i.row}` : "File"}</span>
                    <span className="text-ink-2">{i.message}</span>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}

          {step === 3 ? (
            <div className="flex flex-wrap items-center gap-4">
              {Object.keys(matches).length ? (
                <Button busy={validate.isPending} onClick={runValidate}>
                  Re-check with my matches
                </Button>
              ) : null}
              <Switch checked={createNew} onChange={setCreateNew} label="Create new dishes for unmatched names" />
              <Button variant="primary" busy={commit.isPending} onClick={runCommit}>
                Import {fmtNum(report.valid_rows)} rows
              </Button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-base text-leaf">
                Imported {fmtNum(report.committed_rows ?? job.committed_rows)} rows{report.skipped_rows ? `, skipped ${fmtNum(report.skipped_rows)}` : ""}. A training run is queued.
              </p>
              <Button variant="ghost" onClick={() => setJob(null)}>
                Import another file
              </Button>
            </div>
          )}
        </div>
      ) : null}

      {imports.data?.length ? (
        <Section title="Previous imports" className="mt-8">
          <ul className="rim divide-y divide-rim">
            {imports.data.slice(0, 6).map((j) => (
              <li key={j.id} className="flex flex-wrap items-center justify-between gap-2 px-5 py-2.5 text-base">
                <span className="font-medium">{j.filename}</span>
                <span className="text-sm text-muted">
                  {titleCase(j.status)}, {fmtNum(j.committed_rows || j.row_count)} rows, {fmtDateTime(j.committed_at ?? j.created_at)}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}

function Calendar() {
  const { today } = useCanteen();
  const { can } = useAuth();
  const toast = useToast();
  const cal = useCalendar(today, addDays(today, 60));
  const add = useAddCalendar();
  const [form, setForm] = useState({ day: addDays(today, 7), kind: "event", label: "", days: "1", attendance: "" });
  const grouped = useMemo(() => {
    const seen = new Map<string, { label: string; kind: string; start: string; end: string; attendance?: number | null }>();
    for (const e of cal.data ?? []) {
      if (e.kind === "term_start") continue;
      const k = `${e.kind}|${e.label}`;
      const cur = seen.get(k);
      if (cur && addDays(cur.end, 1) === e.day) cur.end = e.day;
      else if (!cur) seen.set(k, { label: e.label, kind: e.kind, start: e.day, end: e.day, attendance: e.expected_attendance });
      else seen.set(`${k}|${e.day}`, { label: e.label, kind: e.kind, start: e.day, end: e.day, attendance: e.expected_attendance });
    }
    return [...seen.values()].sort((a, b) => a.start.localeCompare(b.start));
  }, [cal.data]);
  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <div>
        {cal.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <ul className="rim divide-y divide-rim">
            {grouped.map((e) => (
              <li key={`${e.kind}${e.start}${e.label}`} className="flex items-center justify-between gap-3 px-5 py-2.5">
                <div>
                  <p className="font-medium">{e.label}</p>
                  <p className="text-sm text-muted">
                    {fmtDay(e.start)}
                    {e.end !== e.start ? ` to ${fmtDay(e.end)}` : ""}
                    {e.attendance ? `, about ${fmtNum(e.attendance)} people` : ""}
                  </p>
                </div>
                <Badge tone={e.kind === "exam" ? "rain" : e.kind === "holiday" || e.kind === "vacation" ? "neutral" : e.kind === "disruption" ? "chilli" : "haldi"}>{titleCase(e.kind)}</Badge>
              </li>
            ))}
            {!grouped.length ? <li className="px-5 py-4 text-muted">Nothing in the next two months.</li> : null}
          </ul>
        )}
      </div>
      {can("calendar:write") ? (
        <form
          className="rim flex flex-col gap-3 px-5 py-4"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await add.mutateAsync({ day: form.day, kind: form.kind as "event", label: form.label, days: Number(form.days) || 1, expected_attendance: form.attendance ? Number(form.attendance) : null });
              toast("ok", "Added. Forecasts for those days will take it into account.");
              setForm((f) => ({ ...f, label: "", attendance: "" }));
            } catch (err) {
              toast("error", err instanceof ApiError ? err.message : "Could not add it.");
            }
          }}
        >
          <p className="font-[640]">Add to the calendar</p>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Starts" htmlFor="cal-day">
              <Input id="cal-day" type="date" value={form.day} onChange={(e) => setForm({ ...form, day: e.target.value })} />
            </Field>
            <Field label="Days" htmlFor="cal-days">
              <Input id="cal-days" inputMode="numeric" value={form.days} onChange={(e) => setForm({ ...form, days: e.target.value.replace(/\D/g, "") })} />
            </Field>
          </div>
          <Field label="Kind" htmlFor="cal-kind">
            <Select id="cal-kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="event">Event on campus</option>
              <option value="fest">Fest</option>
              <option value="exam">Exams</option>
              <option value="holiday">Holiday</option>
              <option value="vacation">Vacation</option>
              <option value="term_start">Term starts</option>
            </Select>
          </Field>
          <Field label="Name" htmlFor="cal-label">
            <Input id="cal-label" value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} required />
          </Field>
          {form.kind === "event" || form.kind === "fest" ? (
            <Field label="Expected visitors (optional)" htmlFor="cal-att">
              <Input id="cal-att" inputMode="numeric" value={form.attendance} onChange={(e) => setForm({ ...form, attendance: e.target.value.replace(/\D/g, "") })} />
            </Field>
          ) : null}
          <Button type="submit" variant="primary" busy={add.isPending} disabled={!form.label}>
            Add
          </Button>
        </form>
      ) : null}
    </div>
  );
}

export default function Data() {
  return (
    <div>
      <PageHeader title="Data and imports" sub="FoodWise learns from your own history. The more days it has, the more it can do; nothing is shared with other colleges unless you opt in." />
      <Section title="Readiness">
        <Readiness />
      </Section>
      <Section title="Import history">
        <Wizard />
      </Section>
      <Section title="Academic calendar" sub="Exams, holidays, fests and events change who eats. Forecasts read this calendar.">
        <Calendar />
      </Section>
    </div>
  );
}

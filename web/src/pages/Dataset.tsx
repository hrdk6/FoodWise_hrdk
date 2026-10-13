import { ChevronLeft, ChevronRight, Download, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { download } from "@/api/client";
import { useDatasetRows, useDatasetSummary } from "@/api/hooks";
import { useCanteen } from "@/app/auth";
import { fmtDay, fmtNum } from "@/lib/format";
import { useToast } from "@/ui/overlays";
import { Badge, Button, EmptyState, ErrorState, Input, PageHeader, Section, Select, Skeleton, Tabs, cx } from "@/ui/primitives";

const PAGE_SIZE = 50;
const LONG_DAY: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", year: "numeric" };

function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function Dataset() {
  const { canteen } = useCanteen();
  const toast = useToast();
  const summary = useDatasetSummary(canteen?.id);
  const [table, setTable] = useState("logs");
  const [page, setPage] = useState(1);
  const [meal, setMeal] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [search, setSearch] = useState("");
  const q = useDebounced(search);
  useEffect(() => setPage(1), [table, meal, start, end, q, canteen?.id]);

  const query = useMemo(
    () => (canteen ? { canteen_id: canteen.id, table, page, page_size: PAGE_SIZE, start, end, meal, q } : null),
    [canteen, table, page, start, end, meal, q],
  );
  const rows = useDatasetRows(query);
  const s = summary.data;
  const info = s?.tables.find((t) => t.key === table);
  const d = rows.data;
  const pages = d ? Math.max(1, Math.ceil(d.total / d.page_size)) : 1;

  const save = async () => {
    if (!canteen) return;
    const p = new URLSearchParams({ canteen_id: canteen.id, table });
    if (start) p.set("start", start);
    if (end) p.set("end", end);
    if (meal) p.set("meal", meal);
    if (q) p.set("q", q);
    try {
      await download(`/api/v1/dataset/export?${p}`, `foodwise-${table}.csv`);
    } catch {
      toast("error", "The file could not be downloaded.");
    }
  };

  if (!canteen) return <EmptyState title="No canteen yet">Add a canteen to see its data.</EmptyState>;

  return (
    <div>
      <PageHeader
        title="Training data"
        sub={`Everything the forecast models learn from for ${canteen.name}. Open any table, filter it, and download it as CSV.`}
      />

      {summary.error ? <ErrorState error={summary.error} retry={() => summary.refetch()} /> : null}
      {summary.isLoading ? <Skeleton className="h-28 w-full" /> : null}
      {s ? (
        <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            [fmtNum(s.days), "days of service history"],
            [fmtNum(s.dishes), "dishes"],
            [fmtNum(s.tables.find((t) => t.key === "logs")?.rows), "service log rows (date x meal x dish)"],
            [fmtNum(s.feature_count), "input features per row"],
          ].map(([big, small]) => (
            <div key={small} className="rim px-5 py-4">
              <p className="num text-3xl font-[680] text-ink">{big}</p>
              <p className="mt-1 text-sm text-muted">{small}</p>
            </div>
          ))}
        </div>
      ) : null}
      {s ? (
        <p className="mb-6 rounded-[8px] bg-haldi-soft px-4 py-3 text-base text-haldi-deep">
          <strong>{s.kind}.</strong> The model predicts <strong>{s.target}</strong>. {s.note}
        </p>
      ) : null}

      {s ? <Tabs value={table} onChange={setTable} tabs={s.tables.map((t) => ({ value: t.key, label: t.label, count: t.rows }))} /> : null}

      <div className="mt-4">
        {info ? (
          <p className="mb-3 text-base text-ink-2">
            {info.description}
            {info.first_day && info.last_day ? ` Covers ${fmtDay(info.first_day, LONG_DAY)} to ${fmtDay(info.last_day, LONG_DAY)}.` : ""}
          </p>
        ) : null}

        <div className="mb-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm text-ink-2">
            From
            <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} className="w-40" />
          </label>
          <label className="flex flex-col gap-1 text-sm text-ink-2">
            To
            <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="w-40" />
          </label>
          {s && s.meals.length && table !== "calendar" && table !== "weather" ? (
            <label className="flex flex-col gap-1 text-sm text-ink-2">
              Meal
              <Select value={meal} onChange={(e) => setMeal(e.target.value)} className="w-40">
                <option value="">All meals</option>
                {s.meals.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </Select>
            </label>
          ) : null}
          <label className="flex min-w-48 flex-1 flex-col gap-1 text-sm text-ink-2">
            Search dish or label
            <span className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" aria-hidden />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="e.g. rajma" className="pl-9" />
            </span>
          </label>
          <Button variant="ghost" icon={<Download className="size-4" />} onClick={save}>
            Download CSV
          </Button>
        </div>

        {rows.error ? <ErrorState error={rows.error} retry={() => rows.refetch()} /> : null}
        {rows.isLoading ? <Skeleton className="h-96 w-full" /> : null}
        {d && !d.rows.length ? <EmptyState title="No rows">Nothing matches these filters.</EmptyState> : null}
        {d && d.rows.length ? (
          <div className={cx("rim overflow-x-auto", rows.isFetching && "opacity-70")}>
            <table className="min-w-full text-sm">
              <thead className="bg-steel-2 text-left text-ink-2">
                <tr>
                  {d.columns.map((c) => (
                    <th key={c.key} className={cx("whitespace-nowrap px-3 py-2 font-[620]", c.numeric && "text-right")}>
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-rim">
                {d.rows.map((r, i) => (
                  <tr key={i} className="hover:bg-steel-2/50">
                    {r.map((v, j) => (
                      <td key={j} className={cx("whitespace-nowrap px-3 py-1.5", d.columns[j].numeric && "num text-right", j === 0 && "font-medium text-ink")}>
                        {v === null || v === "" ? <span className="text-muted">-</span> : typeof v === "boolean" ? (v ? "yes" : "no") : String(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}

        {d ? (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm text-ink-2">
            <span>
              {fmtNum(d.total)} rows, showing {fmtNum((d.page - 1) * d.page_size + 1)} to {fmtNum(Math.min(d.page * d.page_size, d.total))}
            </span>
            <span className="flex items-center gap-2">
              <Button variant="ghost" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)} icon={<ChevronLeft className="size-4" />}>
                Previous
              </Button>
              <span className="num">
                Page {page} of {pages}
              </span>
              <Button variant="ghost" size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)} icon={<ChevronRight className="size-4" />}>
                Next
              </Button>
            </span>
          </div>
        ) : null}
      </div>

      {s && table === "features" ? (
        <Section title="What the features mean" sub="Each row above has these inputs. The models predict demand from them.">
          <div className="grid gap-3 md:grid-cols-2">
            {s.feature_groups.map((g) => (
              <div key={g.group} className="rim px-4 py-3">
                <p className="mb-1.5 font-[640]">{g.group}</p>
                <div className="flex flex-wrap gap-1.5">
                  {g.columns.map((c) => (
                    <Badge key={c} tone="neutral">
                      {c}
                    </Badge>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Section>
      ) : null}
    </div>
  );
}

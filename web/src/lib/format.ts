export const TZ = "Asia/Kolkata";

const UNIT_LABEL: Record<string, [string, string]> = {
  plates: ["plate", "plates"],
  pieces: ["piece", "pieces"],
  kg: ["kg", "kg"],
  litres: ["litre", "litres"],
};

export function unitLabel(unit: string, qty = 2) {
  const [one, many] = UNIT_LABEL[unit] ?? [unit, unit];
  return qty === 1 ? one : many;
}

export function fmtNum(v: number | null | undefined, digits = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return v.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function fmtQty(v: number | null | undefined, unit: string) {
  if (v === null || v === undefined) return "–";
  const digits = unit === "kg" || unit === "litres" ? 1 : 0;
  return `${fmtNum(v, digits)} ${unitLabel(unit, v)}`;
}

export function fmtPct(v: number | null | undefined, digits = 0, signed = false) {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  const s = (v * 100).toFixed(digits);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

export function fmtRupees(v: number | null | undefined) {
  if (v === null || v === undefined) return "–";
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}

export function fmtKg(v: number | null | undefined, digits = 0) {
  if (v === null || v === undefined) return "–";
  return `${fmtNum(v, digits)} kg`;
}

/** YYYY-MM-DD for a date in the college's timezone. */
export function isoDay(d: Date = new Date(), tz = TZ) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

export function addDays(day: string, n: number) {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function dayDiff(a: string, b: string) {
  return Math.round((new Date(`${a}T12:00:00Z`).getTime() - new Date(`${b}T12:00:00Z`).getTime()) / 86400000);
}

/** "Tomorrow", "Today", "Yesterday", or "Thu 15 Oct". */
export function relDay(day: string, today = isoDay()) {
  const diff = dayDiff(day, today);
  if (diff === 0) return "Today";
  if (diff === 1) return "Tomorrow";
  if (diff === -1) return "Yesterday";
  return fmtDay(day);
}

export function fmtDay(day: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" }) {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString("en-GB", { ...opts, timeZone: "UTC" });
}

export function fmtTime(iso: string | null | undefined, tz = TZ) {
  if (!iso) return "–";
  return new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: tz });
}

export function fmtDateTime(iso: string | null | undefined, tz = TZ) {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: tz,
  });
}

export function minutesUntil(iso: string | null | undefined) {
  if (!iso) return null;
  return Math.round((new Date(iso).getTime() - Date.now()) / 60000);
}

export function fmtDuration(minutes: number) {
  if (minutes < 0) return "passed";
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

export function titleCase(s: string) {
  return s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export const ROLE_LABEL: Record<string, string> = {
  organization: "Organisation account",
  admin: "FoodWise admin",
};

export const KIND_LABEL: Record<string, string> = {
  community_kitchen: "Community kitchen",
  food_bank: "Food bank",
  shelter: "Shelter",
  children_home: "Children's home",
  elder_home: "Elders' home",
  ngo: "NGO",
  social_facility: "Social service",
  animal_shelter: "Animal shelter or gaushala",
  compost: "Compost or biogas",
};

"""Data-quality engine: hard rules, statistical checks, dish matching and a quality score."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher
from statistics import median

ABBREVIATIONS = {
    "veg": "vegetable",
    "panner": "paneer",
    "chkn": "chicken",
    "chiken": "chicken",
    "dhal": "dal",
    "daal": "dal",
    "chawal": "rice",
    "roti": "chapati",
    "chapatti": "chapati",
    "chappati": "chapati",
    "biriyani": "biryani",
    "briyani": "biryani",
    "aaloo": "aloo",
    "alu": "aloo",
}


def normalise_name(name: str) -> str:
    tokens = re.sub(r"[^a-z0-9 ]+", " ", name.lower()).split()
    return " ".join(ABBREVIATIONS.get(t, t) for t in tokens)


def similarity(a: str, b: str) -> float:
    na, nb = normalise_name(a), normalise_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, na, nb).ratio()
    sorted_seq = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(seq, sorted_seq, jaccard), 3)


@dataclass
class DishMatch:
    raw: str
    dish_id: str | None
    dish_name: str | None
    score: float
    status: str  # exact | suggested | new


def match_dish(raw: str, dishes: list[tuple[str, str, list[str]]]) -> DishMatch:
    """dishes: (id, canonical name, aliases). >= 0.92 auto-matches, >= 0.6 needs a human confirm."""
    best: tuple[float, str | None, str | None] = (0.0, None, None)
    for dish_id, name, aliases in dishes:
        for candidate in [name, *aliases]:
            s = similarity(raw, candidate)
            if s > best[0]:
                best = (s, dish_id, name)
    score, dish_id, dish_name = best
    if score >= 0.92:
        return DishMatch(raw, dish_id, dish_name, score, "exact")
    if score >= 0.6:
        return DishMatch(raw, dish_id, dish_name, score, "suggested")
    return DishMatch(raw, None, None, score, "new")


@dataclass
class Issue:
    row: int | None
    level: str  # error | warning | info
    code: str
    message: str


@dataclass
class QualityReport:
    rows: int
    valid_rows: int
    issues: list[Issue] = field(default_factory=list)
    score: float = 100.0
    date_range: tuple[str, str] | None = None
    days: int = 0
    gaps: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for i in self.issues:
            out[i.level] += 1
        return dict(out)


def check_rows(rows: list[dict], *, known_meals: set[str], dish_units: dict[str, str]) -> QualityReport:
    """rows: parsed import rows with keys date, canteen, meal, dish_id, unit, prepared_qty, served_qty,
    leftover_qty, sold_out, _row. Rows with errors are excluded from `valid_rows`."""
    issues: list[Issue] = []
    bad_rows: set[int] = set()
    seen: dict[tuple, int] = {}

    def err(r: int, code: str, msg: str) -> None:
        issues.append(Issue(r, "error", code, msg))
        bad_rows.add(r)

    for row in rows:
        r = row["_row"]
        prepared, served, left = row.get("prepared_qty"), row.get("served_qty"), row.get("leftover_qty") or 0.0
        if row.get("date") is None:
            err(r, "bad_date", "Date is missing or not in YYYY-MM-DD format.")
            continue
        if row.get("meal") not in known_meals:
            err(r, "unknown_meal", f"Meal '{row.get('meal_raw')}' is not one of this canteen's meals.")
        if not row.get("dish_id"):
            err(r, "unknown_dish", f"Dish '{row.get('dish_raw')}' is not matched to a dish.")
        if prepared is None or served is None:
            err(r, "missing_qty", "Prepared and served quantities are required.")
            continue
        if min(prepared, served, left) < 0:
            err(r, "negative_qty", "Quantities cannot be negative.")
        if prepared + 1e-6 < (served + left) * 0.99:
            err(r, "prepared_lt_served", f"Prepared ({prepared:g}) is less than served plus leftover ({served + left:g}).")
        key = (row.get("canteen_id"), row["date"], row.get("meal"), row.get("dish_id"))
        if key in seen:
            err(r, "duplicate", f"Duplicate of row {seen[key]} (same canteen, date, meal and dish).")
        else:
            seen[key] = r
        expected_unit = dish_units.get(row.get("dish_id") or "")
        if expected_unit and row.get("unit") and row["unit"] != expected_unit:
            issues.append(Issue(r, "warning", "unit_change", f"Unit '{row['unit']}' differs from the dish's usual '{expected_unit}'."))

    valid = [row for row in rows if row["_row"] not in bad_rows and row.get("date") is not None]

    # Robust z-score outliers per dish (median absolute deviation).
    by_dish: dict[str, list[dict]] = defaultdict(list)
    for row in valid:
        by_dish[row["dish_id"]].append(row)
    for dish_rows in by_dish.values():
        values = [r["served_qty"] for r in dish_rows]
        if len(values) < 8:
            continue
        med = median(values)
        mad = median(abs(v - med) for v in values) or 1e-9
        for row in dish_rows:
            z = 0.6745 * (row["served_qty"] - med) / mad
            if abs(z) > 5:
                issues.append(Issue(row["_row"], "warning", "outlier", f"Served {row['served_qty']:g} is far from this dish's usual {med:g}."))
        # Long constant runs suggest copy-paste logging.
        ordered = sorted(dish_rows, key=lambda r: r["date"])
        run = 1
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            run = run + 1 if cur["prepared_qty"] == prev["prepared_qty"] and cur["served_qty"] == prev["served_qty"] else 1
            if run == 7:
                issues.append(Issue(cur["_row"], "warning", "constant_run", "Seven identical entries in a row. Check for copied values."))

    dates = sorted({row["date"] for row in valid})
    gaps: list[str] = []
    if dates:
        for a, b in zip(dates, dates[1:], strict=False):
            if (b - a).days > 3:
                gaps.append(f"{a.isoformat()} to {b.isoformat()}")
        if gaps:
            issues.append(Issue(None, "info", "gaps", f"{len(gaps)} gap(s) longer than three days, for example {gaps[0]}."))

    report = QualityReport(rows=len(rows), valid_rows=len(valid), issues=issues, gaps=gaps)
    if dates:
        report.date_range = (dates[0].isoformat(), dates[-1].isoformat())
        report.days = len(dates)
    report.score = quality_score(len(rows), report.counts())
    return report


def quality_score(rows: int, counts: dict[str, int]) -> float:
    if rows == 0:
        return 0.0
    error_rate = counts.get("error", 0) / rows
    warning_rate = counts.get("warning", 0) / rows
    score = 100 - 120 * error_rate - 40 * warning_rate - 2 * counts.get("info", 0)
    return round(max(0.0, min(100.0, score)), 1)


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_float(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_bool(value) -> bool:
    return str(value or "").strip().lower() in {"true", "yes", "y", "1", "sold out", "soldout"}

"""The data behind the forecasts, open for inspection.

Shows every table the models learn from for one canteen (service logs, calendar, weather, RSVPs,
student feedback) and the exact feature matrix built from them, which is what the training code
sees. Read-only; a reviewer can page through, filter and download any table as CSV.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select

from foodwise.core.deps import Ctx, need
from foodwise.core.errors import NotFound, Unprocessable
from foodwise.core.timeutil import local_today
from foodwise.db.models import CalendarDay, Canteen, Dish, Feedback, Rsvp, ServiceLog, Tenant, WeatherDay
from foodwise.ml.data import dishes_frame, history_frame, inputs_for
from foodwise.ml.features import CATEGORICAL_FEATURES, NUMERIC_FEATURES, FeatureSpec, build

router = APIRouter(prefix="/dataset", tags=["dataset"])

TABLES: dict[str, tuple[str, str]] = {
    "logs": ("Service logs", "What the kitchen cooked and what students ate: one row per date, meal and dish. The target the models predict is the served quantity."),
    "calendar": ("Academic calendar", "Exams, holidays, vacations, fests, events and disruptions known in advance."),
    "weather": ("Weather", "Temperature and rain probability for each day (a forecast the evening before)."),
    "rsvp": ("RSVPs", "How many students said they would eat or skip each meal, counted per date and meal."),
    "feedback": ("Student feedback", "Ratings and portion comments on dishes. Used for dish quality, not as a demand input by default."),
    "features": ("Model features", "The exact matrix the models train on: each row's inputs, built only from information available the evening before."),
}

FEATURE_GROUPS = [
    ("Calendar", ["dow", "is_weekend", "month", "is_month_end", "week_of_term", "is_exam", "is_holiday", "is_vacation", "is_fest", "event_size", "disruption"]),
    ("Weather", ["temp_c", "rain_prob", "is_rainy"]),
    ("RSVPs and headcount", ["expected_diners", "rsvp_eating_rate", "rsvp_skip_rate", "rsvp_known"]),
    ("Footfall history", ["footfall_lag1", "footfall_lag7", "footfall_mean7", "footfall_mean28", "footfall_dow_mean", "log_footfall_mean7"]),
    ("Dish history", ["dish_last", "dish_lag7", "dish_mean4", "dish_ewm", "dish_share4", "dish_dow_mean", "dish_dow_n", "dish_n", "days_since_served", "log_dish_mean4"]),
    ("Menu context", ["n_dishes_meal", "n_same_course"]),
]


class TableInfo(BaseModel):
    key: str
    label: str
    description: str
    rows: int
    first_day: date | None
    last_day: date | None


class FeatureGroup(BaseModel):
    group: str
    columns: list[str]


class DatasetSummary(BaseModel):
    canteen: str
    kind: str
    days: int
    dishes: int
    meals: list[str]
    tables: list[TableInfo]
    feature_count: int
    feature_groups: list[FeatureGroup]
    target: str
    note: str


class Column(BaseModel):
    key: str
    label: str
    numeric: bool


class DatasetPage(BaseModel):
    table: str
    columns: list[Column]
    rows: list[list[str | float | int | bool | None]]
    total: int
    page: int
    page_size: int


def _canteen(ctx: Ctx, canteen_id: uuid.UUID) -> Canteen:
    ctx.check_canteen(canteen_id)
    c = ctx.db.get(Canteen, canteen_id)
    if c is None:
        raise NotFound("Canteen not found.")
    return c


def _frame(ctx: Ctx, canteen: Canteen, table: str) -> pd.DataFrame:
    db = ctx.db
    cid = canteen.id
    if table == "logs":
        rows = db.execute(
            select(ServiceLog.service_date, ServiceLog.meal, Dish.name, ServiceLog.unit, ServiceLog.prepared_qty,
                   ServiceLog.served_qty, ServiceLog.unserved_qty, ServiceLog.plate_waste_qty, ServiceLog.spoilage_qty,
                   ServiceLog.footfall, ServiceLog.sold_out, ServiceLog.source)
            .join(Dish, Dish.id == ServiceLog.dish_id).where(ServiceLog.canteen_id == cid)
        ).all()
        return pd.DataFrame(rows, columns=["date", "meal", "dish", "unit", "prepared", "served", "left_over",
                                           "plate_waste", "spoilage", "footfall", "sold_out", "source"])
    if table == "calendar":
        rows = db.execute(
            select(CalendarDay.day, CalendarDay.kind, CalendarDay.label, CalendarDay.meal,
                   CalendarDay.expected_attendance, CalendarDay.disruption)
            .where(or_(CalendarDay.canteen_id.is_(None), CalendarDay.canteen_id == cid))
        ).all()
        return pd.DataFrame(rows, columns=["date", "kind", "label", "meal", "expected_attendance", "disruption"])
    if table == "weather":
        rows = db.execute(
            select(WeatherDay.day, WeatherDay.temp_c, WeatherDay.rain_prob, WeatherDay.condition, WeatherDay.source)
            .where(WeatherDay.canteen_id == cid)
        ).all()
        return pd.DataFrame(rows, columns=["date", "temp_c", "rain_prob", "condition", "source"])
    if table == "rsvp":
        rows = db.execute(
            select(Rsvp.service_date, Rsvp.meal, Rsvp.status, func.count()).where(Rsvp.canteen_id == cid)
            .group_by(Rsvp.service_date, Rsvp.meal, Rsvp.status)
        ).all()
        df = pd.DataFrame(rows, columns=["date", "meal", "status", "n"])
        if df.empty:
            return pd.DataFrame(columns=["date", "meal", "eating", "skipping"])
        wide = df.pivot_table(index=["date", "meal"], columns="status", values="n", aggfunc="sum", fill_value=0).reset_index()
        for col in ("eating", "skipping"):
            if col not in wide:
                wide[col] = 0
        return wide[["date", "meal", "eating", "skipping"]]
    if table == "feedback":
        rows = db.execute(
            select(Feedback.service_date, Feedback.meal, Dish.name, Feedback.rating, Feedback.portion, Feedback.sentiment)
            .join(Dish, Dish.id == Feedback.dish_id).where(Feedback.canteen_id == cid)
        ).all()
        return pd.DataFrame(rows, columns=["date", "meal", "dish", "rating", "portion", "sentiment"])
    if table == "features":
        return _features(ctx, canteen)
    raise Unprocessable("Unknown table.")


_FEATURE_CACHE: dict[tuple, pd.DataFrame] = {}


def _features(ctx: Ctx, canteen: Canteen) -> pd.DataFrame:
    """The training matrix, built exactly as the trainer builds it."""
    db = ctx.db
    tenant = db.get(Tenant, ctx.tenant_id)
    as_of = local_today(tenant.timezone) + timedelta(days=1)
    n, last = db.execute(
        select(func.count(), func.max(ServiceLog.updated_at)).where(ServiceLog.canteen_id == canteen.id)
    ).one()
    key = (str(canteen.id), n, str(last))
    if key in _FEATURE_CACHE:
        return _FEATURE_CACHE[key]
    hist = history_frame(db, canteen.id, before=as_of)
    if hist.empty:
        return pd.DataFrame()
    start = hist["service_date"].min().date()
    spec = FeatureSpec(use_feedback=False)
    frame = build(inputs_for(db, canteen, hist, start, as_of), spec)
    frame = frame[frame["demand"].notna()].reset_index(drop=True)
    names = dishes_frame(db).set_index("dish_id")["name"]
    out = pd.DataFrame({"date": frame["service_date"].dt.date, "meal": frame["meal"],
                        "dish": frame["dish_id"].map(names), "demand": frame["demand"]})
    for col in spec.columns:
        if col in frame:
            out[col] = frame[col]
    out = out.sort_values(["date", "meal", "dish"], ascending=[False, True, True]).reset_index(drop=True)
    _FEATURE_CACHE.clear()
    _FEATURE_CACHE[key] = out
    return out


def _filtered(df: pd.DataFrame, start: date | None, end: date | None, meal: str | None, q: str | None) -> pd.DataFrame:
    if df.empty:
        return df
    if "date" in df and (start or end):
        d = pd.to_datetime(df["date"]).dt.date
        keep = np.ones(len(df), dtype=bool)
        if start:
            keep &= (d >= start).to_numpy()
        if end:
            keep &= (d <= end).to_numpy()
        df = df[keep]
    if meal and "meal" in df:
        df = df[df["meal"] == meal]
    if q:
        needle = q.lower()
        text_cols = [c for c in ("dish", "label", "kind", "portion", "condition") if c in df]
        if text_cols:
            mask = np.zeros(len(df), dtype=bool)
            for c in text_cols:
                mask |= df[c].astype(str).str.lower().str.contains(needle, regex=False).to_numpy()
            df = df[mask]
    return df


def _sorted(df: pd.DataFrame) -> pd.DataFrame:
    if "date" in df and len(df):
        keys = [c for c in ("date", "meal", "dish", "kind") if c in df]
        asc = [False] + [True] * (len(keys) - 1)
        return df.sort_values(keys, ascending=asc, kind="mergesort")
    return df


def _cell(v):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return None
    if isinstance(v, (np.floating, float)):
        return round(float(v), 3)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (date, pd.Timestamp)):
        return v.strftime("%Y-%m-%d")
    if pd.isna(v):
        return None
    return v if isinstance(v, (str, int, bool)) else str(v)


@router.get("/summary", response_model=DatasetSummary)
def summary(canteen_id: uuid.UUID, ctx: Ctx = Depends(need("logs:read"))) -> DatasetSummary:
    canteen = _canteen(ctx, canteen_id)
    db = ctx.db
    cid = canteen.id
    days = int(db.execute(select(func.count(func.distinct(ServiceLog.service_date))).where(ServiceLog.canteen_id == cid)).scalar_one() or 0)
    dishes = int(db.execute(select(func.count(func.distinct(ServiceLog.dish_id))).where(ServiceLog.canteen_id == cid)).scalar_one() or 0)
    meals = [m for (m,) in db.execute(select(ServiceLog.meal).where(ServiceLog.canteen_id == cid).distinct().order_by(ServiceLog.meal))]
    infos: list[TableInfo] = []
    for key, (label, desc) in TABLES.items():
        if key == "features":
            df = _features(ctx, canteen)
            n = len(df)
            first = min(df["date"]) if n else None
            last = max(df["date"]) if n else None
        else:
            df = _frame(ctx, canteen, key)
            n = len(df)
            first = pd.to_datetime(df["date"]).min().date() if n else None
            last = pd.to_datetime(df["date"]).max().date() if n else None
        infos.append(TableInfo(key=key, label=label, description=desc, rows=n, first_day=first, last_day=last))
    simulated = db.execute(select(func.count()).select_from(WeatherDay).where(WeatherDay.canteen_id == cid, WeatherDay.source == "simulated")).scalar_one()
    kind = "Synthetic demo history (simulated college)" if simulated else "College records"
    known = set(NUMERIC_FEATURES) | set(CATEGORICAL_FEATURES)
    groups = [FeatureGroup(group=g, columns=[c for c in cols if c in known]) for g, cols in FEATURE_GROUPS]
    listed = {c for g in groups for c in g.columns}
    rest = [c for c in list(NUMERIC_FEATURES) + list(CATEGORICAL_FEATURES) if c not in listed]
    if rest:
        groups.append(FeatureGroup(group="Other", columns=rest))
    return DatasetSummary(
        canteen=canteen.name, kind=kind,
        days=days, dishes=dishes, meals=meals, tables=infos, feature_count=len(known), feature_groups=groups,
        target="demand = portions served (sold-out rows are treated as a lower bound, not a true demand)",
        note="Every feature for a day is computed only from information available the evening before. Rows are never used to predict themselves.",
    )


@router.get("/rows", response_model=DatasetPage)
def rows(canteen_id: uuid.UUID, table: str = Query(...), page: int = Query(1, ge=1), page_size: int = Query(50, ge=10, le=200),
         start: date | None = None, end: date | None = None, meal: str | None = None, q: str | None = Query(None, max_length=60),
         ctx: Ctx = Depends(need("logs:read"))) -> DatasetPage:
    canteen = _canteen(ctx, canteen_id)
    if table not in TABLES:
        raise Unprocessable("Unknown table.")
    df = _sorted(_filtered(_frame(ctx, canteen, table), start, end, meal, q))
    total = len(df)
    part = df.iloc[(page - 1) * page_size: page * page_size]
    cols = [Column(key=c, label=c, numeric=bool(pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]))) for c in df.columns]
    return DatasetPage(table=table, columns=cols, rows=[[_cell(v) for v in r] for r in part.itertuples(index=False, name=None)],
                       total=total, page=page, page_size=page_size)


@router.get("/export")
def export(canteen_id: uuid.UUID, table: str = Query(...), start: date | None = None, end: date | None = None,
           meal: str | None = None, q: str | None = Query(None, max_length=60), ctx: Ctx = Depends(need("logs:read"))):
    canteen = _canteen(ctx, canteen_id)
    if table not in TABLES:
        raise Unprocessable("Unknown table.")
    df = _sorted(_filtered(_frame(ctx, canteen, table), start, end, meal, q))

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(list(df.columns))
        yield buf.getvalue()
        for r in df.itertuples(index=False, name=None):
            buf.seek(0)
            buf.truncate()
            w.writerow(["" if (c := _cell(v)) is None else c for v in r])
            yield buf.getvalue()

    return StreamingResponse(gen(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="foodwise-{table}.csv"'})

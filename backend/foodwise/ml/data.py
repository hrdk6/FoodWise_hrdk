"""Load one canteen's history and context from the database into feature-builder inputs."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from foodwise.db.models import (
    CalendarDay,
    Canteen,
    Dish,
    Feedback,
    MenuEntry,
    Rsvp,
    ServiceLog,
    WeatherDay,
)
from foodwise.ml.features import Inputs

HISTORY_DAYS = 420


def dishes_frame(db: Session) -> pd.DataFrame:
    rows = db.execute(
        select(Dish.id, Dish.name, Dish.course, Dish.diet, Dish.unit, Dish.portion_kg, Dish.cost_per_unit,
               Dish.food_category)
    ).all()
    df = pd.DataFrame(rows, columns=["dish_id", "name", "course", "diet", "unit", "portion_kg", "cost_per_unit",
                                     "food_category"])
    df["dish_id"] = df["dish_id"].astype(str)
    return df


def history_frame(db: Session, canteen_id: uuid.UUID, before: date, days: int = HISTORY_DAYS) -> pd.DataFrame:
    start = before - timedelta(days=days)
    rows = db.execute(
        select(
            ServiceLog.service_date, ServiceLog.meal, ServiceLog.dish_id, ServiceLog.served_qty,
            ServiceLog.prepared_qty, ServiceLog.sold_out, ServiceLog.footfall, ServiceLog.unserved_qty,
            ServiceLog.plate_waste_qty, ServiceLog.spoilage_qty,
        ).where(ServiceLog.canteen_id == canteen_id, ServiceLog.service_date >= start, ServiceLog.service_date < before)
    ).all()
    df = pd.DataFrame(rows, columns=["service_date", "meal", "dish_id", "served", "prepared", "sold_out", "footfall",
                                     "unserved", "plate_waste", "spoilage"])
    df["dish_id"] = df["dish_id"].astype(str)
    df["service_date"] = pd.to_datetime(df["service_date"])
    df["footfall"] = df["footfall"].astype(float)
    df["sold_out"] = df["sold_out"].astype(bool)
    return df


def menu_frame(db: Session, canteen_id: uuid.UUID, day: date, meals: list[str] | None = None) -> pd.DataFrame:
    q = select(MenuEntry.service_date, MenuEntry.meal, MenuEntry.dish_id).where(
        MenuEntry.canteen_id == canteen_id, MenuEntry.service_date == day
    )
    if meals:
        q = q.where(MenuEntry.meal.in_(meals))
    df = pd.DataFrame(db.execute(q).all(), columns=["service_date", "meal", "dish_id"])
    df["dish_id"] = df["dish_id"].astype(str)
    df["service_date"] = pd.to_datetime(df["service_date"])
    for col in ["served", "prepared", "footfall"]:
        df[col] = np.nan
    df["sold_out"] = False
    return df


def context(db: Session, canteen: Canteen, start: date, end: date) -> dict[str, pd.DataFrame]:
    cal = pd.DataFrame(
        db.execute(
            select(CalendarDay.day, CalendarDay.kind, CalendarDay.meal, CalendarDay.expected_attendance,
                   CalendarDay.disruption).where(
                CalendarDay.day >= start, CalendarDay.day <= end,
                or_(CalendarDay.canteen_id.is_(None), CalendarDay.canteen_id == canteen.id),
            )
        ).all(),
        columns=["day", "kind", "meal", "expected_attendance", "disruption"],
    )
    cal["expected_attendance"] = cal["expected_attendance"].fillna(0).astype(float)
    weather = pd.DataFrame(
        db.execute(
            select(WeatherDay.day, WeatherDay.temp_c, WeatherDay.rain_prob).where(
                WeatherDay.canteen_id == canteen.id, WeatherDay.day >= start, WeatherDay.day <= end
            )
        ).all(),
        columns=["day", "temp_c", "rain_prob"],
    )
    rsvp = pd.DataFrame(
        db.execute(
            select(
                Rsvp.service_date, Rsvp.meal,
                func.count().filter(Rsvp.status == "eating"),
                func.count().filter(Rsvp.status == "skipping"),
            )
            .where(Rsvp.canteen_id == canteen.id, Rsvp.service_date >= start, Rsvp.service_date <= end)
            .group_by(Rsvp.service_date, Rsvp.meal)
        ).all(),
        columns=["service_date", "meal", "eating", "skipping"],
    )
    feedback = pd.DataFrame(
        db.execute(
            select(Feedback.service_date, Feedback.dish_id, Feedback.rating, Feedback.weight, Feedback.portion).where(
                Feedback.canteen_id == canteen.id, Feedback.service_date >= start, Feedback.service_date <= end
            )
        ).all(),
        columns=["service_date", "dish_id", "rating", "weight", "portion"],
    )
    feedback["dish_id"] = feedback["dish_id"].astype(str)
    term_starts = [
        pd.Timestamp(d)
        for (d,) in db.execute(
            select(CalendarDay.day).where(CalendarDay.kind == "term_start", CalendarDay.day <= end)
        ).all()
    ]
    return {"calendar": cal, "weather": weather, "rsvp": rsvp, "feedback": feedback, "term_starts": term_starts}


def inputs_for(db: Session, canteen: Canteen, rows: pd.DataFrame, start: date, end: date) -> Inputs:
    ctx = context(db, canteen, start, end)
    return Inputs(
        rows=rows,
        dishes=dishes_frame(db),
        calendar=ctx["calendar"],
        weather=ctx["weather"],
        rsvp=ctx["rsvp"],
        feedback=ctx["feedback"],
        expected_diners=canteen.expected_diners,
        term_starts=ctx["term_starts"],
    )


def distinct_log_days(db: Session, canteen_id: uuid.UUID, before: date | None = None) -> int:
    q = select(func.count(func.distinct(ServiceLog.service_date))).where(ServiceLog.canteen_id == canteen_id)
    if before:
        q = q.where(ServiceLog.service_date < before)
    return int(db.execute(q).scalar_one() or 0)

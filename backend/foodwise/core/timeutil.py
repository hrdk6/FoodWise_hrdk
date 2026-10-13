"""Tenant-local time. Canteen cutoffs and service windows are wall-clock times in the college's timezone."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(UTC)


def tz(name: str | None) -> ZoneInfo:
    return ZoneInfo(name or "Asia/Kolkata")


def local_now(tz_name: str | None) -> datetime:
    return datetime.now(tz(tz_name))


def local_today(tz_name: str | None) -> date:
    return local_now(tz_name).date()


def parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")[:2]
    return time(int(hh), int(mm))


def at_local(day: date, hhmm: str | time, tz_name: str | None) -> datetime:
    t = parse_hhmm(hhmm) if isinstance(hhmm, str) else hhmm
    return datetime.combine(day, t, tzinfo=tz(tz_name))


def meal_window(meal: dict, day: date, tz_name: str | None) -> tuple[datetime, datetime]:
    start = at_local(day, meal.get("start", "12:00"), tz_name)
    end = at_local(day, meal.get("end", "14:00"), tz_name)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def daterange(start: date, end: date):
    """Inclusive date range."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)

"""Weather for forecast days: Open-Meteo (free, no key) for the next 14 days, otherwise the
canteen's own climatology for that month. Stored per canteen and day so forecasts are reproducible."""

from __future__ import annotations

import logging
from datetime import date

import httpx
from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from foodwise.config import get_settings
from foodwise.core import telemetry
from foodwise.core.timeutil import local_today
from foodwise.db.models import Canteen, WeatherDay

log = logging.getLogger("foodwise.weather")


def _condition(rain_prob: float) -> str:
    return "rain" if rain_prob >= 0.6 else "cloudy" if rain_prob >= 0.3 else "clear"


def ensure_weather(db: Session, tenant_id, canteen: Canteen, day: date, tz_name: str) -> WeatherDay:
    existing = db.execute(
        select(WeatherDay).where(WeatherDay.canteen_id == canteen.id, WeatherDay.day == day)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    temp, rain, source = None, None, "climatology"
    ahead = (day - local_today(tz_name)).days
    if get_settings().weather_provider == "open-meteo" and 0 <= ahead <= 14:
        try:
            with telemetry.external("open-meteo"):
                resp = httpx.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": canteen.lat,
                        "longitude": canteen.lon,
                        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                        "timezone": tz_name,
                        "start_date": day.isoformat(),
                        "end_date": day.isoformat(),
                    },
                    timeout=4.0,
                )
                resp.raise_for_status()
            daily = resp.json()["daily"]
            temp = (daily["temperature_2m_max"][0] + daily["temperature_2m_min"][0]) / 2
            rain = (daily["precipitation_probability_max"][0] or 0) / 100
            source = "open-meteo"
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            log.warning("weather provider unavailable, using climatology: %s", exc)
    if temp is None:
        row = db.execute(
            select(func.avg(WeatherDay.temp_c), func.avg(WeatherDay.rain_prob)).where(
                WeatherDay.canteen_id == canteen.id, extract("month", WeatherDay.day) == day.month
            )
        ).one()
        temp = float(row[0]) if row[0] is not None else 27.0
        rain = float(row[1]) if row[1] is not None else 0.2
    wd = WeatherDay(
        tenant_id=tenant_id,
        canteen_id=canteen.id,
        day=day,
        temp_c=round(temp, 1),
        rain_prob=round(rain, 2),
        condition=_condition(rain),
        source=source,
    )
    db.add(wd)
    db.flush()
    return wd

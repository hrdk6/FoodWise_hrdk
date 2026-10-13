"""Keep a demo college running after it was seeded.

The digital twin continues day by day: for every finished day without logs, the simulated kitchen
cooks the approved plan (approving anything still waiting, as the manager would), the world's true
demand decides what is served and what is left, students leave feedback and RSVPs, and FoodWise
plans the next days. The world stays anchored to the seed date, so the calendar does not drift.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from foodwise.core import security
from foodwise.core.timeutil import at_local, local_today
from foodwise.db.base import system_session, tenant_session
from foodwise.db.models import (
    Canteen,
    Dish,
    Feedback,
    Forecast,
    MenuEntry,
    Rsvp,
    ServiceLog,
    Tenant,
    WeatherDay,
)
from foodwise.domain.textanalysis import analyze
from foodwise.ml import serving
from foodwise.sim.world import CANTEENS, DISH_BY_KEY, World, rng

log = logging.getLogger("foodwise.demo")
MAX_CATCH_UP_DAYS = 30


def advance_demo(tenant_id: uuid.UUID) -> dict:
    with system_session() as db:
        tenant = db.get(Tenant, tenant_id)
        anchor_s = ((tenant.settings or {}).get("demo") or {}).get("anchor") if tenant else None
        if tenant is None or not tenant.is_demo or not anchor_s:
            return {"skipped": "not a seeded demo college"}
        tz_name = tenant.timezone
    anchor = date.fromisoformat(anchor_s)
    world = World(anchor, history_days=0, future_days=0)
    today = local_today(tz_name)
    done: dict[str, int] = {}

    with tenant_session(tenant_id) as db:
        tenant = db.get(Tenant, tenant_id)
        dish_ids = {d.name: d.id for d in db.execute(select(Dish)).scalars()}
        key_by_name = {d.name: k for k, d in DISH_BY_KEY.items()}
        for cdef in CANTEENS:
            canteen = db.execute(select(Canteen).where(Canteen.name == cdef.name)).scalar_one_or_none()
            if canteen is None:
                continue
            last = db.execute(select(func.max(ServiceLog.service_date)).where(ServiceLog.canteen_id == canteen.id)).scalar()
            start = max((last or today - timedelta(days=1)) + timedelta(days=1), today - timedelta(days=MAX_CATCH_UP_DAYS))
            _ensure_horizon(db, tenant, canteen, cdef, world, dish_ids, today)
            days = 0
            day = start
            while day < today:
                _simulate_day(db, tenant, canteen, cdef, world, day, dish_ids, key_by_name)
                days += 1
                day += timedelta(days=1)
            for d in (today, today + timedelta(days=1)):
                exists = db.execute(select(Forecast.id).where(Forecast.canteen_id == canteen.id, Forecast.service_date == d).limit(1)).first()
                if not exists:
                    serving.generate(db, tenant, canteen, d)
            done[cdef.key] = days
            db.commit()
    log.info("demo advanced: %s", done)
    return {"simulated_days": done}


def _ensure_horizon(db, tenant, canteen, cdef, world: World, dish_ids, today: date) -> None:
    """Menus, weather and RSVPs exist for the coming days, as a real college would have them."""
    for offset in range(-MAX_CATCH_UP_DAYS, 15):
        day = today + timedelta(days=offset)
        if not db.execute(select(MenuEntry.id).where(MenuEntry.canteen_id == canteen.id, MenuEntry.service_date == day).limit(1)).first():
            for meal, dishes in world.menu(cdef, day).items():
                for dk in dishes:
                    db.add(MenuEntry(tenant_id=tenant.id, canteen_id=canteen.id, service_date=day, meal=meal,
                                     dish_id=dish_ids[DISH_BY_KEY[dk].name]))
        temp, prob, _ = world.weather(day)
        db.execute(insert(WeatherDay).values(
            id=uuid.uuid4(), tenant_id=tenant.id, canteen_id=canteen.id, day=day, temp_c=temp, rain_prob=prob,
            condition="rain" if prob >= 0.6 else "cloudy" if prob >= 0.3 else "clear", source="simulated",
        ).on_conflict_do_nothing())
    if cdef.kind == "mess":
        for day in (today, today + timedelta(days=1)):
            if db.execute(select(Rsvp.id).where(Rsvp.canteen_id == canteen.id, Rsvp.service_date == day).limit(1)).first():
                continue
            residents = [security.hash_device(tenant.id, f"sim-device-{i}") for i in range(1300)]
            for meal in world.menu(cdef, day):
                eating, skipping = world.rsvp(cdef, day, meal, world.footfall(cdef, day, meal))
                picks = rng(world.seed, "rsvp-pick", cdef.key, day.toordinal(), meal).permutation(len(residents))[: eating + skipping]
                db.execute(insert(Rsvp), [
                    {"id": uuid.uuid4(), "tenant_id": tenant.id, "canteen_id": canteen.id, "service_date": day, "meal": meal,
                     "device_hash": residents[i], "status": "eating" if j < eating else "skipping"}
                    for j, i in enumerate(picks)
                ])
    db.flush()


def _simulate_day(db, tenant, canteen, cdef, world: World, day: date, dish_ids, key_by_name) -> None:
    forecasts = db.execute(select(Forecast).where(Forecast.canteen_id == canteen.id, Forecast.service_date == day,
                                                  Forecast.kind != "shadow")).scalars().all()
    if not forecasts:
        forecasts = serving.generate(db, tenant, canteen, day)
    logged = {(m, d) for m, d in db.execute(select(ServiceLog.meal, ServiceLog.dish_id).where(
        ServiceLog.canteen_id == canteen.id, ServiceLog.service_date == day)).all()}
    names = {v: k for k, v in dish_ids.items()}
    for f in forecasts:
        if f.status == "proposed":
            f.status = "approved"
        if (f.meal, f.dish_id) in logged:
            continue
        dk = key_by_name.get(names.get(f.dish_id, ""))
        if dk is None:
            continue
        menu = world.menu(cdef, day).get(f.meal, [])
        ff = world.footfall(cdef, day, f.meal)
        demand = world.demand(cdef, day, f.meal, dk, ff, menu)
        plan = f.override_qty if f.status == "overridden" and f.override_qty else f.recommended_qty
        d = DISH_BY_KEY[dk]
        cooked = math.ceil(plan * 2) / 2 if d.unit in ("kg", "litres") else float(math.ceil(plan))
        served = min(demand, cooked)
        meal_def = next(m for m in cdef.meals if m["key"] == f.meal)
        db.add(ServiceLog(
            tenant_id=tenant.id, canteen_id=canteen.id, service_date=day, meal=f.meal, dish_id=f.dish_id, unit=d.unit,
            prepared_qty=cooked, served_qty=round(served, 1), unserved_qty=round(cooked - served, 1),
            plate_waste_qty=round(served * max(0.01, 0.06 - 0.04 * world.quality(dk, day)), 1), spoilage_qty=0.0,
            footfall=ff, sold_out=demand > cooked + (0.25 if d.unit in ("kg", "litres") else 0.5),
            cooked_at=at_local(day, meal_def["start"], tenant.timezone) - timedelta(minutes=75), source="kitchen",
        ))
        for k, fb in enumerate(world.feedback(cdef, day, f.meal, dk, ff)):
            analysis = analyze(fb["text"]) if fb["text"] else None
            db.add(Feedback(
                tenant_id=tenant.id, canteen_id=canteen.id, service_date=day, meal=f.meal, dish_id=f.dish_id,
                rating=fb["rating"], aspects=fb["aspects"], portion=fb["portion"],
                text=analysis.clean_text if analysis else None, language=analysis.language if analysis else None,
                sentiment=analysis.sentiment if analysis else None, text_aspects=analysis.aspects if analysis else {},
                device_hash=f"sim-{day.toordinal()}-{f.meal}-{dk}-{k}", weight=1.0,
            ))
    db.flush()

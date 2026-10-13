"""Service logs: the kitchen sheet, offline-friendly bulk sync, and waste summaries.

Sync is idempotent by construction: each entry upserts on its natural key
(canteen, date, meal, dish), so replaying a queued batch after a dropped connection is harmless.
When two devices edited the same entry offline, the later client edit wins and the response
reports the conflict so the device can show it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select

from foodwise.core import audit
from foodwise.core.deps import Ctx, need
from foodwise.core.errors import NotFound, Unprocessable
from foodwise.core.timeutil import at_local, local_now, meal_window
from foodwise.db.models import Canteen, Dish, Forecast, MenuEntry, ServiceLog, Tenant
from foodwise.domain.settings import effective_settings
from foodwise.services.incidents import surplus_threshold

router = APIRouter(prefix="/logs", tags=["logs"])


class SheetDish(BaseModel):
    dish_id: uuid.UUID
    name: str
    name_hi: str | None
    unit: str
    course: str
    planned_qty: float | None
    plan_status: str | None
    prepared_qty: float | None
    served_qty: float | None
    unserved_qty: float | None
    plate_waste_qty: float | None
    spoilage_qty: float | None
    sold_out: bool | None
    updated_at: datetime | None


class SheetMeal(BaseModel):
    key: str
    label: str
    start: str
    end: str
    status: str  # upcoming | serving | done
    footfall: int | None
    dishes: list[SheetDish]


class Sheet(BaseModel):
    canteen_id: uuid.UUID
    date: date
    meals: list[SheetMeal]


@router.get("/sheet", response_model=Sheet)
def kitchen_sheet(canteen_id: uuid.UUID, day: date | None = None, ctx: Ctx = Depends(need("logs:read"))) -> Sheet:
    ctx.check_canteen(canteen_id)
    canteen = ctx.db.get(Canteen, canteen_id)
    if canteen is None:
        raise NotFound("Canteen not found.")
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    now = local_now(tenant.timezone)
    day = day or now.date()
    rows = ctx.db.execute(
        select(MenuEntry.meal, Dish, ServiceLog, Forecast)
        .join(Dish, Dish.id == MenuEntry.dish_id)
        .outerjoin(ServiceLog, and_(ServiceLog.canteen_id == MenuEntry.canteen_id, ServiceLog.service_date == MenuEntry.service_date,
                                    ServiceLog.meal == MenuEntry.meal, ServiceLog.dish_id == MenuEntry.dish_id))
        .outerjoin(Forecast, and_(Forecast.canteen_id == MenuEntry.canteen_id, Forecast.service_date == MenuEntry.service_date,
                                  Forecast.meal == MenuEntry.meal, Forecast.dish_id == MenuEntry.dish_id,
                                  Forecast.kind.in_(["model", "baseline", "prior"])))
        .where(MenuEntry.canteen_id == canteen_id, MenuEntry.service_date == day)
        .order_by(Dish.course, Dish.name)
    ).all()
    meals = []
    for m in canteen.meals:
        start, end = meal_window(m, day, tenant.timezone)
        status = "upcoming" if now < start else "serving" if now < end else "done"
        dishes, footfall = [], None
        for meal, d, log, f in rows:
            if meal != m["key"]:
                continue
            planned = (f.override_qty if f.status == "overridden" else f.recommended_qty) if f else None
            if log is not None and log.footfall:
                footfall = log.footfall
            dishes.append(SheetDish(
                dish_id=d.id, name=d.name, name_hi=d.name_hi, unit=d.unit, course=d.course, planned_qty=planned,
                plan_status=f.status if f else None,
                prepared_qty=log.prepared_qty if log else None, served_qty=log.served_qty if log else None,
                unserved_qty=log.unserved_qty if log else None, plate_waste_qty=log.plate_waste_qty if log else None,
                spoilage_qty=log.spoilage_qty if log else None, sold_out=log.sold_out if log else None,
                updated_at=log.updated_at if log else None,
            ))
        meals.append(SheetMeal(key=m["key"], label=m["label"], start=m["start"], end=m["end"], status=status,
                               footfall=footfall, dishes=dishes))
    return Sheet(canteen_id=canteen_id, date=day, meals=meals)


class EntryIn(BaseModel):
    client_id: str = Field(max_length=64)
    canteen_id: uuid.UUID
    date: date
    meal: str = Field(max_length=20)
    dish_id: uuid.UUID
    prepared_qty: float = Field(ge=0, le=100000)
    unserved_qty: float = Field(default=0, ge=0, le=100000)
    plate_waste_qty: float = Field(default=0, ge=0, le=100000)
    spoilage_qty: float = Field(default=0, ge=0, le=100000)
    served_qty: float | None = Field(default=None, ge=0, le=100000)
    sold_out: bool = False
    footfall: int | None = Field(default=None, ge=0, le=100000)
    edited_at: datetime | None = None


class BulkIn(BaseModel):
    entries: list[EntryIn] = Field(min_length=1, max_length=300)


class EntryResult(BaseModel):
    client_id: str
    status: str  # saved | conflict_resolved | rejected
    message: str | None = None
    flags: list[str] = []


class SurplusHint(BaseModel):
    canteen_id: uuid.UUID
    date: date
    meal: str
    unserved_kg: float
    required_kg: float
    message: str


class BulkOut(BaseModel):
    results: list[EntryResult]
    surplus: list[SurplusHint]


def _flags(db, e: EntryIn, served: float) -> list[str]:
    """Flag entries far from this dish's usual quantity (likely a typo or a unit mix-up)."""
    med, n = db.execute(
        select(func.percentile_cont(0.5).within_group(ServiceLog.served_qty), func.count())
        .where(ServiceLog.canteen_id == e.canteen_id, ServiceLog.meal == e.meal, ServiceLog.dish_id == e.dish_id,
               ServiceLog.service_date >= e.date - timedelta(days=90), ServiceLog.service_date < e.date)
    ).one()
    if n and n >= 6 and med and (served > med * 3 or served < med / 4):
        return ["unusual_quantity"]
    return []


@router.post("/bulk", response_model=BulkOut)
def bulk_upsert(body: BulkIn, ctx: Ctx = Depends(need("logs:write"))) -> BulkOut:
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    results: list[EntryResult] = []
    touched: set[tuple] = set()
    dishes = {d.id: d for d in ctx.db.execute(select(Dish)).scalars()}
    canteens: dict[uuid.UUID, Canteen] = {}
    for e in body.entries:
        try:
            ctx.check_canteen(e.canteen_id)
        except NotFound:
            results.append(EntryResult(client_id=e.client_id, status="rejected", message="Canteen not found."))
            continue
        canteen = canteens.get(e.canteen_id) or ctx.db.get(Canteen, e.canteen_id)
        canteens[e.canteen_id] = canteen
        dish = dishes.get(e.dish_id)
        if canteen is None or dish is None:
            results.append(EntryResult(client_id=e.client_id, status="rejected", message="Unknown canteen or dish."))
            continue
        if e.meal not in {m["key"] for m in canteen.meals}:
            results.append(EntryResult(client_id=e.client_id, status="rejected", message=f"Unknown meal '{e.meal}'."))
            continue
        served = e.served_qty if e.served_qty is not None else max(0.0, e.prepared_qty - e.unserved_qty - e.spoilage_qty)
        if e.prepared_qty + 1e-6 < (served + e.unserved_qty + e.spoilage_qty) * 0.99:
            results.append(EntryResult(client_id=e.client_id, status="rejected",
                                       message="Cooked must be at least served plus left over."))
            continue
        existing = ctx.db.execute(
            select(ServiceLog).where(ServiceLog.canteen_id == e.canteen_id, ServiceLog.service_date == e.date,
                                     ServiceLog.meal == e.meal, ServiceLog.dish_id == e.dish_id)
        ).scalar_one_or_none()
        status = "saved"
        if existing is not None and e.edited_at and existing.updated_at and existing.updated_at > e.edited_at:
            results.append(EntryResult(client_id=e.client_id, status="conflict_resolved",
                                       message="A newer entry from another device was kept."))
            continue
        flags = _flags(ctx.db, e, served)
        if existing is None:
            existing = ServiceLog(tenant_id=ctx.tenant_id, canteen_id=e.canteen_id, service_date=e.date, meal=e.meal,
                                  dish_id=e.dish_id, unit=dish.unit, source="kitchen", created_by=ctx.principal.user_id)
            ctx.db.add(existing)
            meal_def = next(m for m in canteen.meals if m["key"] == e.meal)
            existing.cooked_at = at_local(e.date, meal_def["start"], tenant.timezone) - timedelta(minutes=60)
        existing.prepared_qty = e.prepared_qty
        existing.served_qty = served
        existing.unserved_qty = e.unserved_qty
        existing.plate_waste_qty = e.plate_waste_qty
        existing.spoilage_qty = e.spoilage_qty
        existing.sold_out = e.sold_out
        existing.quality_flags = flags
        if e.footfall is not None:
            existing.footfall = e.footfall
        existing.updated_at = datetime.now(UTC)
        touched.add((e.canteen_id, e.date, e.meal))
        results.append(EntryResult(client_id=e.client_id, status=status, flags=flags))
    ctx.db.flush()

    hints = []
    settings = effective_settings(tenant.settings)
    for canteen_id, day, meal in touched:
        kg = ctx.db.execute(
            select(func.coalesce(func.sum(ServiceLog.unserved_qty * Dish.portion_kg), 0))
            .join(Dish, Dish.id == ServiceLog.dish_id)
            .where(ServiceLog.canteen_id == canteen_id, ServiceLog.service_date == day, ServiceLog.meal == meal)
        ).scalar_one()
        thr = surplus_threshold(ctx.db, canteen_id, settings["thresholds"])
        if kg >= thr["required_kg"] and day >= local_now(tenant.timezone).date() - timedelta(days=1):
            hints.append(SurplusHint(canteen_id=canteen_id, date=day, meal=meal, unserved_kg=round(float(kg), 1),
                                     required_kg=thr["required_kg"],
                                     message=f"{kg:.0f} kg left over after {meal}. That is a large surplus; start a rescue while it is still safe."))
    audit.record(ctx.db, ctx.principal, "logs.saved", meta={"entries": len(body.entries)})
    ctx.db.commit()
    return BulkOut(results=results, surplus=hints)


class LogRow(BaseModel):
    date: date
    meal: str
    dish_id: uuid.UUID
    dish: str
    unit: str
    prepared_qty: float
    served_qty: float
    unserved_qty: float
    plate_waste_qty: float
    spoilage_qty: float
    sold_out: bool
    footfall: int | None
    source: str
    quality_flags: list[str]


@router.get("", response_model=list[LogRow])
def list_logs(canteen_id: uuid.UUID, start: date, end: date, meal: str | None = None,
              ctx: Ctx = Depends(need("logs:read"))) -> list[LogRow]:
    ctx.check_canteen(canteen_id)
    if (end - start).days > 93:
        raise Unprocessable("Ask for at most three months at a time, or export the CSV report.")
    q = (select(ServiceLog, Dish.name).join(Dish, Dish.id == ServiceLog.dish_id)
         .where(ServiceLog.canteen_id == canteen_id, ServiceLog.service_date >= start, ServiceLog.service_date <= end)
         .order_by(ServiceLog.service_date.desc(), ServiceLog.meal, Dish.name))
    if meal:
        q = q.where(ServiceLog.meal == meal)
    return [LogRow(date=log.service_date, meal=log.meal, dish_id=log.dish_id, dish=name, unit=log.unit,
                   prepared_qty=log.prepared_qty, served_qty=log.served_qty, unserved_qty=log.unserved_qty,
                   plate_waste_qty=log.plate_waste_qty, spoilage_qty=log.spoilage_qty, sold_out=log.sold_out,
                   footfall=log.footfall, source=log.source, quality_flags=list(log.quality_flags or []))
            for log, name in ctx.db.execute(q)]


class WasteDay(BaseModel):
    date: date
    prepared_kg: float
    served_kg: float
    unserved_kg: float
    plate_waste_kg: float
    spoilage_kg: float
    sold_out_dishes: int
    dishes: int
    guided: bool


@router.get("/waste", response_model=list[WasteDay])
def waste_by_day(canteen_id: uuid.UUID | None = None, days: int = Query(60, ge=7, le=365),
                 ctx: Ctx = Depends(need("logs:read"))) -> list[WasteDay]:
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    since = local_now(tenant.timezone).date() - timedelta(days=days)
    kg = Dish.portion_kg
    q = (
        select(
            ServiceLog.service_date,
            func.sum(ServiceLog.prepared_qty * kg), func.sum(ServiceLog.served_qty * kg),
            func.sum(ServiceLog.unserved_qty * kg), func.sum(ServiceLog.plate_waste_qty * kg),
            func.sum(ServiceLog.spoilage_qty * kg), func.count().filter(ServiceLog.sold_out), func.count(),
            func.bool_or(ServiceLog.source == "kitchen"),
        )
        .join(Dish, Dish.id == ServiceLog.dish_id)
        .where(ServiceLog.service_date >= since)
        .group_by(ServiceLog.service_date)
        .order_by(ServiceLog.service_date)
    )
    if canteen_id:
        ctx.check_canteen(canteen_id)
        q = q.where(ServiceLog.canteen_id == canteen_id)
    elif ctx.principal.canteen_ids:
        q = q.where(ServiceLog.canteen_id.in_(ctx.principal.canteen_ids))
    guided_days = set(ctx.db.execute(
        select(Forecast.service_date).where(Forecast.service_date >= since, Forecast.kind.in_(["model", "baseline", "prior"]),
                                            Forecast.status.in_(["approved", "overridden"])).distinct()
    ).scalars())
    return [WasteDay(date=d, prepared_kg=round(p or 0, 1), served_kg=round(s or 0, 1), unserved_kg=round(u or 0, 1),
                     plate_waste_kg=round(pw or 0, 1), spoilage_kg=round(sp or 0, 1), sold_out_dishes=so or 0,
                     dishes=n or 0, guided=d in guided_days)
            for d, p, s, u, pw, sp, so, n, _ in ctx.db.execute(q)]

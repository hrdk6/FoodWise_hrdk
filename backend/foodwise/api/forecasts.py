"""Prep plans: forecasts, approvals and overrides, what-if, accuracy, and disruption quick actions."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, select

from foodwise.core import audit
from foodwise.core.deps import Ctx, need
from foodwise.core.errors import Conflict, NotFound, Unprocessable
from foodwise.core.timeutil import local_today
from foodwise.db.models import (
    CalendarDay,
    Canteen,
    Dish,
    Forecast,
    ModelVersion,
    ServiceLog,
    Tenant,
    User,
    WeatherDay,
)
from foodwise.domain.newsvendor import QUANTILE_LEVELS, round_for_unit
from foodwise.ml import serving, zoo
from foodwise.ml.monitoring import tracked
from foodwise.services import webhooks

router = APIRouter(tags=["forecasts"])


class Driver(BaseModel):
    key: str
    label: str
    effect: float


class Actual(BaseModel):
    prepared: float
    served: float
    unserved: float
    sold_out: bool


class ForecastRow(BaseModel):
    id: uuid.UUID
    dish_id: uuid.UUID
    dish: str
    dish_hi: str | None
    course: str
    unit: str
    kind: str
    levels: list[float]
    quantiles: list[float]
    p10: float
    p50: float
    p90: float
    recommended: float
    plan_qty: float
    status: str
    override_qty: float | None
    override_reason: str | None
    decided_by: str | None
    decided_at: datetime | None
    changed_after_approval: bool
    drivers: list[Driver]
    context: dict
    actual: Actual | None
    shadow_p50: float | None


class MealPlan(BaseModel):
    key: str
    label: str
    start: str
    end: str
    dishes: list[ForecastRow]


class ModelBrief(BaseModel):
    id: uuid.UUID | None
    version: int | None
    algorithm: str
    label: str
    stage: str | None
    kind: str
    wape: float | None
    trained_at: datetime | None


class DayContext(BaseModel):
    temp_c: float | None
    rain_prob: float | None
    condition: str | None
    weather_source: str | None
    events: list[str]


class Plan(BaseModel):
    canteen_id: uuid.UUID
    canteen: str
    date: date
    critical_ratio: float | None
    model: ModelBrief | None
    context: DayContext
    meals: list[MealPlan]
    counts: dict[str, int]
    generated_at: datetime | None


def _plan(ctx: Ctx, canteen: Canteen, day: date) -> Plan:
    rows = ctx.db.execute(
        select(Forecast, Dish, ServiceLog)
        .join(Dish, Dish.id == Forecast.dish_id)
        .outerjoin(ServiceLog, and_(ServiceLog.canteen_id == Forecast.canteen_id, ServiceLog.service_date == Forecast.service_date,
                                    ServiceLog.meal == Forecast.meal, ServiceLog.dish_id == Forecast.dish_id))
        .where(Forecast.canteen_id == canteen.id, Forecast.service_date == day)
        .order_by(Dish.course, Dish.name)
    ).all()
    shadow = {(f.meal, f.dish_id): f.p50 for f, _, _ in rows if f.kind == "shadow"}
    main = [(f, d, log) for f, d, log in rows if f.kind != "shadow"]
    users = {u.id: u.name for u in ctx.db.execute(select(User).where(User.id.in_({f.decided_by for f, _, _ in main if f.decided_by}))).scalars()}
    meals = []
    counts = {"proposed": 0, "approved": 0, "overridden": 0}
    for m in canteen.meals:
        dishes = []
        for f, d, log in main:
            if f.meal != m["key"]:
                continue
            counts[f.status] = counts.get(f.status, 0) + 1
            dishes.append(ForecastRow(
                id=f.id, dish_id=d.id, dish=d.name, dish_hi=d.name_hi, course=d.course, unit=f.unit, kind=f.kind,
                levels=f.levels, quantiles=f.quantiles, p10=round(f.p10, 1), p50=round(f.p50, 1), p90=round(f.p90, 1),
                recommended=f.recommended_qty,
                plan_qty=f.override_qty if f.status == "overridden" and f.override_qty is not None else f.recommended_qty,
                status=f.status, override_qty=f.override_qty, override_reason=f.override_reason,
                decided_by=users.get(f.decided_by), decided_at=f.decided_at, changed_after_approval=f.changed_after_approval,
                drivers=[Driver(**x) for x in f.drivers or []], context=f.context or {},
                actual=Actual(prepared=log.prepared_qty, served=log.served_qty, unserved=log.unserved_qty, sold_out=log.sold_out)
                if log else None,
                shadow_p50=shadow.get((f.meal, f.dish_id)),
            ))
        meals.append(MealPlan(key=m["key"], label=m["label"], start=m["start"], end=m["end"], dishes=dishes))
    model = None
    if main:
        f0 = main[0][0]
        mv = ctx.db.get(ModelVersion, f0.model_version_id) if f0.model_version_id else None
        model = ModelBrief(
            id=mv.id if mv else None, version=mv.version if mv else None, algorithm=f0.algorithm,
            label=zoo.LABELS.get(f0.algorithm, f0.algorithm), stage=mv.stage if mv else None, kind=f0.kind,
            wape=(mv.metrics or {}).get("wape") if mv else None, trained_at=mv.created_at if mv else None,
        )
    w = ctx.db.execute(select(WeatherDay).where(WeatherDay.canteen_id == canteen.id, WeatherDay.day == day)).scalar_one_or_none()
    events = [c.label for c in ctx.db.execute(
        select(CalendarDay).where(CalendarDay.day == day, (CalendarDay.canteen_id.is_(None)) | (CalendarDay.canteen_id == canteen.id))
    ).scalars() if c.kind != "term_start"]
    return Plan(
        canteen_id=canteen.id, canteen=canteen.name, date=day,
        critical_ratio=main[0][0].critical_ratio if main else None, model=model,
        context=DayContext(temp_c=w.temp_c if w else None, rain_prob=w.rain_prob if w else None,
                           condition=w.condition if w else None, weather_source=w.source if w else None,
                           events=sorted(set(events))),
        meals=meals, counts=counts,
        generated_at=max((f.generated_at for f, _, _ in main), default=None),
    )


def _canteen(ctx: Ctx, canteen_id: uuid.UUID) -> Canteen:
    ctx.check_canteen(canteen_id)
    c = ctx.db.get(Canteen, canteen_id)
    if c is None:
        raise NotFound("Canteen not found.")
    return c


@router.get("/forecasts/plan", response_model=Plan)
def get_plan(canteen_id: uuid.UUID, day: date, ctx: Ctx = Depends(need("forecast:read"))) -> Plan:
    return _plan(ctx, _canteen(ctx, canteen_id), day)


class GenerateIn(BaseModel):
    canteen_id: uuid.UUID
    date: date


@router.post("/forecasts/generate", response_model=Plan)
def generate(body: GenerateIn, ctx: Ctx = Depends(need("forecast:generate"))) -> Plan:
    canteen = _canteen(ctx, body.canteen_id)
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    today = local_today(tenant.timezone)
    if body.date < today or body.date > today + timedelta(days=14):
        raise Unprocessable("Forecasts can be generated for today and the next 14 days.")
    saved = serving.generate(ctx.db, tenant, canteen, body.date)
    if not saved:
        raise Unprocessable(f"There is no menu for {canteen.name} on {body.date:%d %b}. Add the menu first.")
    webhooks.emit(ctx.db, tenant.id, "forecast.generated", {"canteen_id": str(canteen.id), "date": body.date.isoformat()})
    audit.record(ctx.db, ctx.principal, "forecast.generated", target_type="canteen", target_id=canteen.id,
                 meta={"date": body.date.isoformat(), "kind": saved[0].kind})
    ctx.db.commit()
    return _plan(ctx, canteen, body.date)


class DecisionIn(BaseModel):
    action: Literal["approve", "override", "reset"]
    qty: float | None = Field(default=None, ge=0, le=100000)
    reason: str | None = Field(default=None, max_length=300)


@router.patch("/forecasts/{forecast_id}", response_model=ForecastRow)
def decide(forecast_id: uuid.UUID, body: DecisionIn, ctx: Ctx = Depends(need("forecast:decide"))) -> ForecastRow:
    f = ctx.db.get(Forecast, forecast_id)
    if f is None or f.kind == "shadow":
        raise NotFound("Forecast not found.")
    ctx.check_canteen(f.canteen_id)
    if body.action == "override":
        if body.qty is None:
            raise Unprocessable("Enter the quantity you want to cook.")
        if not (body.reason or "").strip():
            raise Unprocessable("Add a short reason for the override. It is how FoodWise learns when overrides help.")
        f.status = "overridden"
        f.override_qty = round_for_unit(body.qty, f.unit)
        f.override_reason = body.reason.strip()
    elif body.action == "approve":
        f.status = "approved"
        f.override_qty = None
        f.override_reason = None
    else:
        f.status = "proposed"
        f.override_qty = None
        f.override_reason = None
    f.decided_by = ctx.principal.user_id if body.action != "reset" else None
    f.decided_at = datetime.now(UTC) if body.action != "reset" else None
    f.changed_after_approval = False
    audit.record(ctx.db, ctx.principal, f"forecast.{body.action}", target_type="forecast", target_id=f.id,
                 meta={"recommended": f.recommended_qty, "qty": f.override_qty, "reason": f.override_reason})
    if body.action != "reset":
        webhooks.emit(ctx.db, ctx.tenant_id, "forecast.approved", {
            "forecast_id": str(f.id), "canteen_id": str(f.canteen_id), "date": f.service_date.isoformat(),
            "meal": f.meal, "dish_id": str(f.dish_id), "qty": f.override_qty or f.recommended_qty,
        })
    ctx.db.commit()
    plan = _plan(ctx, ctx.db.get(Canteen, f.canteen_id), f.service_date)
    return next(d for m in plan.meals for d in m.dishes if d.id == f.id)


class ApproveAllIn(BaseModel):
    canteen_id: uuid.UUID
    date: date
    meal: str | None = None


@router.post("/forecasts/approve", response_model=Plan)
def approve_all(body: ApproveAllIn, ctx: Ctx = Depends(need("forecast:decide"))) -> Plan:
    canteen = _canteen(ctx, body.canteen_id)
    q = select(Forecast).where(Forecast.canteen_id == canteen.id, Forecast.service_date == body.date,
                               Forecast.kind != "shadow", Forecast.status == "proposed")
    if body.meal:
        q = q.where(Forecast.meal == body.meal)
    n = 0
    now = datetime.now(UTC)
    for f in ctx.db.execute(q).scalars():
        f.status, f.decided_by, f.decided_at, f.changed_after_approval = "approved", ctx.principal.user_id, now, False
        n += 1
    if n == 0:
        raise Conflict("Nothing is waiting for approval.")
    audit.record(ctx.db, ctx.principal, "forecast.approve_all", target_type="canteen", target_id=canteen.id,
                 meta={"date": body.date.isoformat(), "meal": body.meal, "count": n})
    webhooks.emit(ctx.db, ctx.tenant_id, "forecast.approved", {"canteen_id": str(canteen.id), "date": body.date.isoformat(),
                                                               "meal": body.meal, "count": n})
    ctx.db.commit()
    return _plan(ctx, canteen, body.date)


# --------------------------------------------------------------------------- what-if


class ScenarioIn(BaseModel):
    canteen_id: uuid.UUID
    date: date
    meal: str | None = None
    headcount_pct: float = Field(default=0, ge=-0.95, le=3)
    rain_prob: float | None = Field(default=None, ge=0, le=1)
    temp_c: float | None = Field(default=None, ge=-10, le=50)
    exam: bool | None = None
    holiday: bool | None = None
    event_attendance: int | None = Field(default=None, ge=0, le=100000)
    disruption: bool | None = None
    shortage_to_waste_ratio: float | None = Field(default=None, ge=0.1, le=50)
    menu: dict[str, list[uuid.UUID]] | None = None


class WhatIfDish(BaseModel):
    meal: str
    dish_id: uuid.UUID
    dish: str
    unit: str
    base_recommended: float
    recommended: float
    quantiles: list[float]
    base_quantiles: list[float]
    expected_waste: float
    expected_shortage: float
    drivers: list[Driver]


class WhatIfOut(BaseModel):
    levels: list[float]
    critical_ratio: float
    dishes: list[WhatIfDish]
    totals: dict[str, float]


@router.post("/forecasts/whatif", response_model=WhatIfOut)
def whatif(body: ScenarioIn, ctx: Ctx = Depends(need("forecast:read"))) -> WhatIfOut:
    canteen = _canteen(ctx, body.canteen_id)
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    meals = [body.meal] if body.meal else None
    base = serving.predict_day(ctx.db, tenant, canteen, body.date, meals=meals, explain_drivers=False)
    menu = {k: [str(x) for x in v] for k, v in body.menu.items()} if body.menu else None
    sc = serving.Scenario(
        headcount_pct=body.headcount_pct, rain_prob=body.rain_prob, temp_c=body.temp_c, exam=body.exam,
        holiday=body.holiday, event_attendance=body.event_attendance, disruption=body.disruption, menu=menu,
        shortage_to_waste_ratio=body.shortage_to_waste_ratio,
    )
    scen = serving.predict_day(ctx.db, tenant, canteen, body.date, meals=meals, scenario=sc)
    ctx.db.rollback()  # weather lookups may have added rows; a what-if never changes stored state
    if not scen:
        raise Unprocessable("There is no menu for that day. Pick dishes for the scenario.")
    dishes = {d.id: d for d in ctx.db.execute(select(Dish)).scalars()}
    base_map = {(p.meal, p.dish_id): p for p in base}
    out, kg = [], {"base_kg": 0.0, "scenario_kg": 0.0, "expected_waste_kg": 0.0, "expected_shortage_kg": 0.0}
    for p in scen:
        d = dishes.get(uuid.UUID(p.dish_id))
        b = base_map.get((p.meal, p.dish_id))
        factor = 1.0 if d is None or d.unit in ("kg", "litres") else d.portion_kg
        kg["base_kg"] += (b.recommended if b else 0) * factor
        kg["scenario_kg"] += p.recommended * factor
        kg["expected_waste_kg"] += p.expected_waste * factor
        kg["expected_shortage_kg"] += p.expected_shortage * factor
        out.append(WhatIfDish(
            meal=p.meal, dish_id=uuid.UUID(p.dish_id), dish=d.name if d else p.dish_id, unit=p.unit,
            base_recommended=b.recommended if b else 0.0, recommended=p.recommended, quantiles=p.quantiles,
            base_quantiles=b.quantiles if b else [0.0] * len(QUANTILE_LEVELS),
            expected_waste=p.expected_waste, expected_shortage=p.expected_shortage,
            drivers=[Driver(**x) for x in p.drivers],
        ))
    return WhatIfOut(levels=list(QUANTILE_LEVELS), critical_ratio=scen[0].critical_ratio, dishes=out,
                     totals={k: round(v, 1) for k, v in kg.items()})


# --------------------------------------------------------------------------- accuracy and forecast value added


class AccuracyDay(BaseModel):
    date: date
    actual_kg: float
    p50_kg: float
    plan_kg: float
    prepared_kg: float
    p10_kg: float
    p90_kg: float


class OverrideReason(BaseModel):
    reason: str
    count: int
    helped: int
    hurt: int


class Accuracy(BaseModel):
    days: list[AccuracyDay]
    wape: float | None
    bias: float | None
    coverage80: float | None
    overrides: int
    override_model_error: float | None
    override_human_error: float | None
    fva: float | None
    verdict: str
    reasons: list[OverrideReason]


@router.get("/forecasts/accuracy", response_model=Accuracy)
def accuracy(canteen_id: uuid.UUID, days: int = Query(28, ge=7, le=180), ctx: Ctx = Depends(need("forecast:read"))) -> Accuracy:
    canteen = _canteen(ctx, canteen_id)
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    since = local_today(tenant.timezone) - timedelta(days=days)
    df = tracked(ctx.db, canteen.id, since)
    if df.empty:
        return Accuracy(days=[], wape=None, bias=None, coverage80=None, overrides=0, override_model_error=None,
                        override_human_error=None, fva=None, verdict="Not enough tracked days yet.", reasons=[])
    kg = {str(d.id): (1.0 if d.unit in ("kg", "litres") else d.portion_kg) for d in ctx.db.execute(select(Dish)).scalars()}
    df["kg"] = df["dish_id"].astype(str).map(kg).fillna(0.3)
    df["plan"] = np.where(df["status"] == "overridden", df["override_qty"].fillna(df["recommended"]), df["recommended"])
    by_day = []
    for d, g in df.groupby("service_date"):
        by_day.append(AccuracyDay(
            date=d, actual_kg=round(float((g["actual"] * g["kg"]).sum()), 1), p50_kg=round(float((g["p50"] * g["kg"]).sum()), 1),
            plan_kg=round(float((g["plan"] * g["kg"]).sum()), 1), prepared_kg=round(float((g["prepared"] * g["kg"]).sum()), 1),
            p10_kg=round(float((g["p10"] * g["kg"]).sum()), 1), p90_kg=round(float((g["p90"] * g["kg"]).sum()), 1),
        ))
    ok = df[~df["sold_out"]]
    y = ok["actual"].to_numpy(float)
    wape = float(np.abs(y - ok["p50"]).sum() / y.sum()) if y.sum() else None
    bias = float((ok["p50"] - y).sum() / y.sum()) if y.sum() else None
    cov = float(np.mean((y >= ok["p10"]) & (y <= ok["p90"]))) if len(ok) else None
    ov = df[df["status"] == "overridden"]
    reasons, model_err, human_err, fva = [], None, None, None
    if len(ov):
        # Compare each plan against the demand that was actually observed (served, or a lower bound when sold out).
        model_err = float(np.abs(ov["recommended"] - ov["actual"]).sum() / ov["actual"].sum())
        human_err = float(np.abs(ov["override_qty"] - ov["actual"]).sum() / ov["actual"].sum())
        fva = model_err - human_err
        for reason, g in ov.groupby(ov["override_reason"].fillna("No reason")):
            helped = int((np.abs(g["override_qty"] - g["actual"]) < np.abs(g["recommended"] - g["actual"])).sum())
            reasons.append(OverrideReason(reason=reason, count=len(g), helped=helped, hurt=len(g) - helped))
        reasons.sort(key=lambda r: -r.count)
    if fva is None:
        verdict = "No overrides in this period."
    elif fva > 0.01:
        verdict = f"Overrides helped: they cut error on those dishes by {fva:.1%} of demand."
    elif fva < -0.01:
        verdict = f"Overrides hurt: the model's numbers would have been {abs(fva):.1%} closer on those dishes."
    else:
        verdict = "Overrides made no measurable difference."
    return Accuracy(days=by_day, wape=round(wape, 4) if wape is not None else None,
                    bias=round(bias, 4) if bias is not None else None, coverage80=round(cov, 4) if cov is not None else None,
                    overrides=len(ov), override_model_error=round(model_err, 4) if model_err is not None else None,
                    override_human_error=round(human_err, 4) if human_err is not None else None,
                    fva=round(fva, 4) if fva is not None else None, verdict=verdict, reasons=reasons)


# --------------------------------------------------------------------------- disruptions


class DisruptionIn(BaseModel):
    canteen_id: uuid.UUID
    date: date
    meal: str
    kind: Literal["event_cancelled", "exam_moved", "power_cut", "holiday_declared", "other"]
    expected_drop_pct: float = Field(ge=0, le=0.95, description="Share of diners expected to stay away")
    already_cooked: bool
    note: str | None = Field(default=None, max_length=300)


class ScaleDown(BaseModel):
    forecast_id: uuid.UUID | None
    dish_id: uuid.UUID
    dish: str
    unit: str
    current_qty: float
    advised_qty: float


class DisruptionOut(BaseModel):
    calendar_id: uuid.UUID
    already_cooked: bool
    message: str
    scale_down: list[ScaleDown]
    surplus_draft: list[dict]


LABELS = {"event_cancelled": "Event cancelled", "exam_moved": "Exam moved", "power_cut": "Power cut",
          "holiday_declared": "Holiday declared", "other": "Disruption"}


@router.post("/disruptions", response_model=DisruptionOut, status_code=201)
def disruption(body: DisruptionIn, ctx: Ctx = Depends(need("forecast:decide"))) -> DisruptionOut:
    canteen = _canteen(ctx, body.canteen_id)
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    if body.meal not in {m["key"] for m in canteen.meals}:
        raise Unprocessable(f"{canteen.name} does not serve '{body.meal}'.")
    cal = CalendarDay(tenant_id=ctx.tenant_id, canteen_id=canteen.id, day=body.date, kind="disruption",
                      label=f"{LABELS[body.kind]}{': ' + body.note if body.note else ''}", meal=body.meal,
                      disruption=body.kind)
    ctx.db.add(cal)
    ctx.db.flush()
    current = {
        f.dish_id: f for f in ctx.db.execute(
            select(Forecast).where(Forecast.canteen_id == canteen.id, Forecast.service_date == body.date,
                                   Forecast.meal == body.meal, Forecast.kind != "shadow")
        ).scalars()
    }
    dishes = {d.id: d for d in ctx.db.execute(select(Dish)).scalars()}
    advice, draft = [], []
    if body.already_cooked:
        for dish_id, f in current.items():
            planned = f.override_qty if f.status == "overridden" and f.override_qty else f.recommended_qty
            spare = round_for_unit(planned * body.expected_drop_pct, f.unit)
            if spare > 0:
                draft.append({"dish_id": str(dish_id), "name": dishes[dish_id].name, "qty": spare, "unit": f.unit})
        message = ("The food is already cooked, so the best move is a rescue. Check these estimated surplus "
                   "quantities, then send the offer.")
    else:
        preds = serving.predict_day(ctx.db, tenant, canteen, body.date, meals=[body.meal],
                                    scenario=serving.Scenario(headcount_pct=-body.expected_drop_pct, disruption=True),
                                    explain_drivers=False)
        for p in preds:
            did = uuid.UUID(p.dish_id)
            f = current.get(did)
            cur = (f.override_qty if f and f.status == "overridden" and f.override_qty else f.recommended_qty) if f else 0
            advice.append(ScaleDown(forecast_id=f.id if f else None, dish_id=did, dish=dishes[did].name, unit=p.unit,
                                    current_qty=cur, advised_qty=p.recommended))
        saved_kg = sum((a.current_qty - a.advised_qty) * (1 if a.unit in ("kg", "litres") else dishes[a.dish_id].portion_kg)
                       for a in advice)
        message = (f"Cook less: the re-forecast saves about {max(saved_kg, 0):.0f} kg of food. Apply the new "
                   "quantities as overrides to update the kitchen sheet.")
    audit.record(ctx.db, ctx.principal, "disruption.reported", target_type="canteen", target_id=canteen.id,
                 meta=body.model_dump(mode="json"))
    ctx.db.commit()
    return DisruptionOut(calendar_id=cal.id, already_cooked=body.already_cooked, message=message, scale_down=advice,
                         surplus_draft=draft)

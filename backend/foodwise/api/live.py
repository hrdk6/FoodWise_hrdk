"""Live service tracker: staff log remaining stock a few times per service; FoodWise projects the
final leftover from a depletion curve and advises on batch cooking or an early surplus rescue.

Depletion curve: the share of a meal's demand served by elapsed fraction t of the service window,
F(t) = 1 - (1 - t)^k. Diners arrive early (k > 1). k defaults to 1.6 per meal until the canteen's
own checkpoints are numerous enough to fit it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, select

from foodwise.core import audit
from foodwise.core.deps import Ctx, need
from foodwise.core.errors import Conflict, NotFound, Unprocessable
from foodwise.core.timeutil import local_now, meal_window
from foodwise.db.models import Canteen, Dish, Forecast, LiveSession, Tenant
from foodwise.domain.newsvendor import round_for_unit
from foodwise.domain.settings import effective_settings
from foodwise.services.incidents import surplus_threshold

router = APIRouter(prefix="/live", tags=["live"])
K = 1.6
BATCHES = (0.6, 0.25, 0.15)


def depletion(t: float, k: float = K) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** k


class DishLive(BaseModel):
    dish_id: uuid.UUID
    dish: str
    unit: str
    prepared: float
    remaining: float | None
    projected_served: float | None
    projected_leftover: float | None
    runs_out_at: datetime | None
    advice: str | None
    batches: list[float]


class LiveOut(BaseModel):
    session_id: uuid.UUID
    canteen_id: uuid.UUID
    meal: str
    meal_label: str
    started_at: datetime
    ends_at: datetime
    elapsed: float
    checkpoints: int
    dishes: list[DishLive]
    projected_leftover_kg: float
    surplus_warning: str | None


def _view(ctx: Ctx, s: LiveSession) -> LiveOut:
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    canteen = ctx.db.get(Canteen, s.canteen_id)
    now = datetime.now(UTC)
    span = (s.ends_at - s.started_at).total_seconds()
    t = (now - s.started_at).total_seconds() / span if span > 0 else 1.0
    last = s.checkpoints[-1] if s.checkpoints else None
    t_last = ((datetime.fromisoformat(last["at"]) - s.started_at).total_seconds() / span) if last else None
    dishes = {str(d.id): d for d in ctx.db.execute(select(Dish).where(Dish.id.in_([uuid.UUID(k) for k in s.prepared]))).scalars()}
    out, leftover_kg = [], 0.0
    for dish_id, prepared in s.prepared.items():
        d = dishes.get(dish_id)
        if d is None:
            continue
        remaining = projected = left = runs_out = None
        advice = None
        if last and dish_id in last["remaining"] and t_last and t_last > 0.05:
            remaining = float(last["remaining"][dish_id])
            served = max(0.0, prepared - remaining)
            frac = depletion(t_last)
            projected = served / frac if frac > 0 else served
            left = prepared - projected
            if left < 0:
                # Solve F(t*) * projected = prepared for the run-out time.
                target = prepared / projected
                t_star = 1 - (1 - target) ** (1 / K)
                runs_out = s.started_at + (s.ends_at - s.started_at) * t_star
                extra = round_for_unit(-left, d.unit)
                advice = f"Cook another batch of about {extra:g} {d.unit} now"
            elif left > 0.15 * prepared and t_last < 0.7:
                advice = "Hold back the last batch; demand is running below plan"
            if left > 0:
                leftover_kg += left * (1.0 if d.unit in ("kg", "litres") else d.portion_kg)
        out.append(DishLive(
            dish_id=d.id, dish=d.name, unit=d.unit, prepared=prepared, remaining=remaining,
            projected_served=round(projected, 1) if projected is not None else None,
            projected_leftover=round(left, 1) if left is not None else None, runs_out_at=runs_out, advice=advice,
            batches=[round_for_unit(prepared * b, d.unit) for b in BATCHES],
        ))
    thr = surplus_threshold(ctx.db, canteen.id, effective_settings(tenant.settings)["thresholds"])
    warning = None
    if leftover_kg >= thr["required_kg"]:
        warning = (f"About {leftover_kg:.0f} kg is likely to be left over. Call a nearby place now so someone can "
                   "collect it soon after service ends.")
    label = next((m["label"] for m in canteen.meals if m["key"] == s.meal), s.meal)
    return LiveOut(session_id=s.id, canteen_id=s.canteen_id, meal=s.meal, meal_label=label, started_at=s.started_at,
                   ends_at=s.ends_at, elapsed=round(min(1.0, max(0.0, t)), 3), checkpoints=len(s.checkpoints), dishes=out,
                   projected_leftover_kg=round(leftover_kg, 1), surplus_warning=warning)


class StartIn(BaseModel):
    canteen_id: uuid.UUID
    meal: str


@router.post("/start", response_model=LiveOut, status_code=201)
def start(body: StartIn, ctx: Ctx = Depends(need("logs:write"))) -> LiveOut:
    ctx.check_canteen(body.canteen_id)
    canteen = ctx.db.get(Canteen, body.canteen_id)
    if canteen is None:
        raise NotFound("Canteen not found.")
    meal = next((m for m in canteen.meals if m["key"] == body.meal), None)
    if meal is None:
        raise Unprocessable("That meal is not served here.")
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    today = local_now(tenant.timezone).date()
    existing = ctx.db.execute(select(LiveSession).where(LiveSession.canteen_id == canteen.id, LiveSession.service_date == today,
                                                        LiveSession.meal == body.meal)).scalar_one_or_none()
    if existing is not None:
        return _view(ctx, existing)
    plan = ctx.db.execute(select(Forecast).where(and_(Forecast.canteen_id == canteen.id, Forecast.service_date == today,
                                                      Forecast.meal == body.meal, Forecast.kind != "shadow"))).scalars().all()
    if not plan:
        raise Conflict("There is no plan for this meal today, so there is nothing to track against.")
    prepared = {str(f.dish_id): (f.override_qty if f.status == "overridden" and f.override_qty else f.recommended_qty) for f in plan}
    start_at, end_at = meal_window(meal, today, tenant.timezone)
    now = datetime.now(UTC)
    s = LiveSession(tenant_id=ctx.tenant_id, canteen_id=canteen.id, service_date=today, meal=body.meal,
                    started_at=min(now, start_at) if now >= start_at else now,
                    ends_at=end_at if end_at > now else now + (end_at - start_at), prepared=prepared, checkpoints=[])
    ctx.db.add(s)
    audit.record(ctx.db, ctx.principal, "live.started", target_type="canteen", target_id=canteen.id, meta={"meal": body.meal})
    ctx.db.commit()
    return _view(ctx, s)


class CheckpointIn(BaseModel):
    remaining: dict[uuid.UUID, float] = Field(min_length=1)


@router.post("/{session_id}/checkpoint", response_model=LiveOut)
def checkpoint(session_id: uuid.UUID, body: CheckpointIn, ctx: Ctx = Depends(need("logs:write"))) -> LiveOut:
    s = ctx.db.get(LiveSession, session_id)
    if s is None:
        raise NotFound("Live session not found.")
    ctx.check_canteen(s.canteen_id)
    for dish_id, qty in body.remaining.items():
        if str(dish_id) not in s.prepared or qty < 0 or qty > s.prepared[str(dish_id)] * 1.5 + 1:
            raise Unprocessable("Remaining quantities must be for dishes on this meal and not above what was cooked.")
    s.checkpoints = [*s.checkpoints, {"at": datetime.now(UTC).isoformat(),
                                      "remaining": {str(k): float(v) for k, v in body.remaining.items()}}]
    ctx.db.commit()
    return _view(ctx, s)


@router.get("", response_model=LiveOut | None)
def current(canteen_id: uuid.UUID, ctx: Ctx = Depends(need("logs:read"))) -> LiveOut | None:
    ctx.check_canteen(canteen_id)
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    today = local_now(tenant.timezone).date()
    s = ctx.db.execute(select(LiveSession).where(LiveSession.canteen_id == canteen_id, LiveSession.service_date == today)
                       .order_by(LiveSession.started_at.desc()).limit(1)).scalar_one_or_none()
    return _view(ctx, s) if s else None

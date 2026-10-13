"""Surplus incidents: report leftover food, record which nearby places take it, and a live stream."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from foodwise.core import audit
from foodwise.core.deps import Ctx, current_principal, need
from foodwise.core.errors import Forbidden, NotFound, Unprocessable
from foodwise.core.events import broadcaster
from foodwise.core.rbac import has_permission
from foodwise.core.security import Principal
from foodwise.core.timeutil import at_local, local_today
from foodwise.db.base import tenant_session
from foodwise.db.models import Canteen, Donation, IncidentEvent, SurplusIncident, Tenant
from foodwise.domain.incident_state import ACTIVE
from foodwise.services import incidents as svc
from foodwise.services import webhooks

router = APIRouter(tags=["incidents"])


class ItemIn(BaseModel):
    dish_id: uuid.UUID
    qty: float = Field(gt=0, le=100000)
    storage: Literal["hot_held", "chilled", "ambient"] = "hot_held"
    cooked_at: datetime | None = None


class IncidentIn(BaseModel):
    canteen_id: uuid.UUID
    trigger: Literal["manual", "end_of_service", "live_tracker", "disruption"] = "manual"
    meal: str | None = None
    items: list[ItemIn] = Field(min_length=1, max_length=30)
    notes: str | None = Field(default=None, max_length=500)
    force: bool = False


class IncidentBrief(BaseModel):
    id: uuid.UUID
    canteen_id: uuid.UUID
    canteen: str
    status: str
    trigger: str
    route: str
    total_portions: float
    total_kg: float
    safe_until: datetime | None
    created_at: datetime
    closed_at: datetime | None
    places: list[str]
    impact: dict | None


class EventOut(BaseModel):
    id: uuid.UUID
    at: datetime
    kind: str
    message: str
    actor: str | None


class DonationOut(BaseModel):
    id: uuid.UUID
    place_ref: str
    place_name: str
    place_kind: str
    place_lat: float | None
    place_lon: float | None
    place_phone: str | None
    place_address: str | None
    distance_km: float | None
    portions: float
    weight_kg: float | None
    status: str
    contact_method: str
    contact_name: str | None
    notes: str | None
    handed_over_at: datetime | None
    created_at: datetime


class IncidentDetail(IncidentBrief):
    items: list[dict]
    threshold: dict
    pickup_point: str | None
    notes: str | None
    canteen_lat: float
    canteen_lon: float
    events: list[EventOut]
    donations: list[DonationOut]
    remaining_portions: float
    share_text: str


def _donation_out(d: Donation) -> DonationOut:
    return DonationOut(
        id=d.id, place_ref=d.place_ref, place_name=d.place_name, place_kind=d.place_kind, place_lat=d.place_lat,
        place_lon=d.place_lon, place_phone=d.place_phone, place_address=d.place_address, distance_km=d.distance_km,
        portions=d.portions, weight_kg=d.weight_kg, status=d.status, contact_method=d.contact_method,
        contact_name=d.contact_name, notes=d.notes, handed_over_at=d.handed_over_at, created_at=d.created_at,
    )


def _brief(db, inc: SurplusIncident, canteen_names: dict) -> IncidentBrief:
    places = [n for (n,) in db.execute(
        select(Donation.place_name).where(Donation.incident_id == inc.id, Donation.status.in_(["agreed", "handed_over"]))
        .order_by(Donation.created_at)).all()]
    return IncidentBrief(id=inc.id, canteen_id=inc.canteen_id, canteen=canteen_names.get(inc.canteen_id, ""),
                         status=inc.status, trigger=inc.trigger, route=inc.route, total_portions=inc.total_portions,
                         total_kg=inc.total_kg, safe_until=inc.safe_until, created_at=inc.created_at,
                         closed_at=inc.closed_at, places=places, impact=inc.impact)


def _detail(db, tenant: Tenant, inc: SurplusIncident) -> IncidentDetail:
    canteen = db.get(Canteen, inc.canteen_id)
    base = _brief(db, inc, {canteen.id: canteen.name})
    events = db.execute(select(IncidentEvent).where(IncidentEvent.incident_id == inc.id)
                        .order_by(IncidentEvent.created_at, IncidentEvent.id)).scalars().all()
    donations = db.execute(select(Donation).where(Donation.incident_id == inc.id).order_by(Donation.created_at)).scalars()
    return IncidentDetail(
        **base.model_dump(), items=inc.items, threshold=inc.threshold, pickup_point=inc.pickup_point, notes=inc.notes,
        canteen_lat=canteen.lat, canteen_lon=canteen.lon,
        events=[EventOut(id=e.id, at=e.created_at, kind=e.kind, message=e.message, actor=e.actor) for e in events],
        donations=[_donation_out(d) for d in donations], remaining_portions=svc.remaining_portions(db, inc),
        share_text=svc.share_text(tenant, canteen, inc),
    )


def _get(ctx: Ctx, incident_id: uuid.UUID) -> SurplusIncident:
    inc = ctx.db.get(SurplusIncident, incident_id)
    if inc is None:
        raise NotFound("Surplus not found.")
    ctx.check_canteen(inc.canteen_id)
    return inc


def _done(ctx: Ctx, inc: SurplusIncident) -> IncidentDetail:
    ctx.db.commit()
    return _detail(ctx.db, ctx.db.get(Tenant, ctx.tenant_id), inc)


@router.get("/incidents", response_model=list[IncidentBrief])
def list_incidents(status: Literal["active", "closed", "all"] = "all", canteen_id: uuid.UUID | None = None,
                   ctx: Ctx = Depends(need("incident:read"))) -> list[IncidentBrief]:
    q = select(SurplusIncident).order_by(SurplusIncident.created_at.desc()).limit(200)
    if status == "active":
        q = q.where(SurplusIncident.status.in_(ACTIVE))
    elif status == "closed":
        q = q.where(SurplusIncident.status.not_in(ACTIVE))
    if canteen_id:
        ctx.check_canteen(canteen_id)
        q = q.where(SurplusIncident.canteen_id == canteen_id)
    names = {c.id: c.name for c in ctx.db.execute(select(Canteen)).scalars()}
    return [_brief(ctx.db, i, names) for i in ctx.db.execute(q).scalars()]


@router.post("/incidents", response_model=IncidentDetail, status_code=201)
def create(body: IncidentIn, ctx: Ctx = Depends(need("incident:create"))) -> IncidentDetail:
    ctx.check_canteen(body.canteen_id)
    canteen = ctx.db.get(Canteen, body.canteen_id)
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    now = datetime.now(UTC)
    meal = next((m for m in canteen.meals if m["key"] == body.meal), canteen.meals[0])
    default_cooked = min(now, at_local(local_today(tenant.timezone), meal["start"], tenant.timezone) - timedelta(minutes=45))
    items = [{"dish_id": i.dish_id, "qty": i.qty, "storage": i.storage, "cooked_at": i.cooked_at or default_cooked}
             for i in body.items]
    if any(i["cooked_at"] > now + timedelta(minutes=5) for i in items):
        raise Unprocessable("Cooked time cannot be in the future.")
    inc = svc.create_incident(ctx.db, tenant, canteen, trigger=body.trigger, items=items, notes=body.notes,
                              actor_id=ctx.principal.user_id, actor_label=ctx.principal.name, force=body.force, now=now)
    audit.record(ctx.db, ctx.principal, "incident.created", target_type="incident", target_id=inc.id,
                 meta={"status": inc.status, "kg": inc.total_kg})
    webhooks.emit(ctx.db, tenant.id, "incident.created", {"incident_id": str(inc.id), "canteen_id": str(canteen.id),
                                                          "status": inc.status, "kg": inc.total_kg})
    return _done(ctx, inc)


@router.get("/incidents/draft", response_model=list[dict])
def draft(canteen_id: uuid.UUID, day: date, meal: str, ctx: Ctx = Depends(need("incident:create"))) -> list[dict]:
    ctx.check_canteen(canteen_id)
    return svc.recent_surplus_draft(ctx.db, ctx.db.get(Canteen, canteen_id), day, meal)


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: uuid.UUID, ctx: Ctx = Depends(need("incident:read"))) -> IncidentDetail:
    inc = _get(ctx, incident_id)
    return _detail(ctx.db, ctx.db.get(Tenant, ctx.tenant_id), inc)


@router.get("/incidents/{incident_id}/events")
async def stream(incident_id: uuid.UUID, request: Request, principal: Principal = Depends(current_principal)):
    """Server-sent events: one `snapshot` message whenever the surplus changes (and every 5 s),
    so a phone in the kitchen and a laptop in the office stay in step."""
    if not has_permission(principal.role, "incident:read") or principal.tenant_id is None:
        raise Forbidden("This account cannot do that.")

    def load() -> dict | None:
        with tenant_session(principal.tenant_id) as db:
            inc = db.get(SurplusIncident, incident_id)
            if inc is None:
                return None
            return _detail(db, db.get(Tenant, principal.tenant_id), inc).model_dump(mode="json")

    first = await asyncio.to_thread(load)
    if first is None:
        raise NotFound("Surplus not found.")

    async def gen():
        topic = f"incident:{incident_id}"
        q = broadcaster.subscribe(topic)
        try:
            yield f"event: snapshot\ndata: {json.dumps(first)}\n\n"
            last = json.dumps(first, sort_keys=True)
            while not await request.is_disconnected():
                try:
                    await asyncio.wait_for(q.get(), timeout=5.0)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                snap = await asyncio.to_thread(load)
                if snap is None:
                    break
                body = json.dumps(snap, sort_keys=True)
                if body != last:
                    last = body
                    yield f"event: snapshot\ndata: {json.dumps(snap)}\n\n"
        finally:
            broadcaster.unsubscribe(topic, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class PlaceIn(BaseModel):
    ref: str | None = Field(default=None, max_length=80, description="From a nearby-places result; omit for a place typed in by hand.")
    name: str = Field(min_length=2, max_length=200)
    kind: str = Field(default="ngo", max_length=30)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)
    distance_km: float | None = Field(default=None, ge=0, le=500)


class DonationIn(BaseModel):
    place: PlaceIn
    portions: float = Field(gt=0, le=100000)
    contact_method: Literal["call", "whatsapp", "in_person", "other"] = "call"
    contact_name: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=300)


@router.post("/incidents/{incident_id}/donations", response_model=IncidentDetail, status_code=201)
def add_donation(incident_id: uuid.UUID, body: DonationIn, ctx: Ctx = Depends(need("incident:create"))) -> IncidentDetail:
    """Record that a place agreed to take some of the food."""
    from foodwise.services.places import manual_ref

    inc = _get(ctx, incident_id)
    place = body.place.model_dump()
    if not place.get("ref") or not str(place["ref"]).startswith(("osm:", "manual:", "legacy:")):
        place["ref"] = manual_ref(body.place.name)
    d = svc.agree(ctx.db, inc, place=place, portions=body.portions, contact_method=body.contact_method,
                  contact_name=body.contact_name, notes=body.notes, actor_id=ctx.principal.user_id, actor=ctx.principal.name)
    audit.record(ctx.db, ctx.principal, "donation.agreed", target_type="incident", target_id=inc.id,
                 meta={"donation": str(d.id), "place": d.place_name, "portions": d.portions})
    webhooks.emit(ctx.db, ctx.tenant_id, "incident.arranged", {"incident_id": str(inc.id), "place": d.place_name,
                                                               "portions": d.portions})
    return _done(ctx, inc)


class HandoverIn(BaseModel):
    weight_kg: float = Field(gt=0, le=10000)


@router.post("/incidents/{incident_id}/donations/{donation_id}/handed-over", response_model=IncidentDetail)
def handed_over(incident_id: uuid.UUID, donation_id: uuid.UUID, body: HandoverIn,
                ctx: Ctx = Depends(need("incident:create"))) -> IncidentDetail:
    inc = _get(ctx, incident_id)
    d = svc.hand_over(ctx.db, inc, donation_id, weight_kg=body.weight_kg, actor=ctx.principal.name)
    audit.record(ctx.db, ctx.principal, "donation.handed_over", target_type="incident", target_id=inc.id,
                 meta={"donation": str(d.id), "kg": body.weight_kg})
    if inc.status == "closed":
        webhooks.emit(ctx.db, ctx.tenant_id, "incident.closed", {"incident_id": str(inc.id), "impact": inc.impact})
    return _done(ctx, inc)


class ReasonIn(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


@router.post("/incidents/{incident_id}/donations/{donation_id}/fell-through", response_model=IncidentDetail)
def donation_fell_through(incident_id: uuid.UUID, donation_id: uuid.UUID, body: ReasonIn,
                          ctx: Ctx = Depends(need("incident:create"))) -> IncidentDetail:
    inc = _get(ctx, incident_id)
    svc.fell_through(ctx.db, inc, donation_id, reason=body.reason, actor=ctx.principal.name)
    audit.record(ctx.db, ctx.principal, "donation.fell_through", target_type="incident", target_id=inc.id,
                 meta={"donation": str(donation_id)})
    return _done(ctx, inc)


class CloseIn(BaseModel):
    disposal: Literal["compost", "animal_feed", "biogas", "bin"] | None = None


@router.post("/incidents/{incident_id}/close", response_model=IncidentDetail)
def close(incident_id: uuid.UUID, body: CloseIn, ctx: Ctx = Depends(need("incident:manage"))) -> IncidentDetail:
    """Close the surplus; anything not handed over is recorded as disposed."""
    inc = _get(ctx, incident_id)
    svc.close(ctx.db, inc, f"Closed by {ctx.principal.name}", disposal=body.disposal)
    webhooks.emit(ctx.db, ctx.tenant_id, "incident.closed", {"incident_id": str(inc.id), "impact": inc.impact})
    audit.record(ctx.db, ctx.principal, "incident.closed", target_type="incident", target_id=inc.id)
    return _done(ctx, inc)


class CancelIn(BaseModel):
    reason: str = Field(min_length=2, max_length=300)


@router.post("/incidents/{incident_id}/cancel", response_model=IncidentDetail)
def cancel(incident_id: uuid.UUID, body: CancelIn, ctx: Ctx = Depends(need("incident:manage"))) -> IncidentDetail:
    inc = _get(ctx, incident_id)
    svc.cancel(ctx.db, inc, body.reason, ctx.principal.name)
    audit.record(ctx.db, ctx.principal, "incident.cancelled", target_type="incident", target_id=inc.id, meta={"reason": body.reason})
    return _done(ctx, inc)

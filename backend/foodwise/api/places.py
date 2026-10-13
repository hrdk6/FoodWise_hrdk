"""Nearby places that can take surplus food, found around the user's device or the canteen.

The location arrives in the request body (not the URL, so it never lands in access logs), is
used to rank places by distance, and is not stored.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from foodwise.core.deps import Ctx, need
from foodwise.core.errors import NotFound
from foodwise.db.models import Canteen, Donation, SurplusIncident, Tenant
from foodwise.domain.places import KINDS
from foodwise.services import places as svc

router = APIRouter(prefix="/places", tags=["places"])

ATTRIBUTION = "Places © OpenStreetMap contributors, ODbL"


class NearbyIn(BaseModel):
    canteen_id: uuid.UUID
    lat: float | None = Field(default=None, ge=-90, le=90, description="The device's location; the canteen's if omitted.")
    lon: float | None = Field(default=None, ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    radius_km: float | None = Field(default=None, gt=0, le=50)
    route: Literal["people", "divert"] | None = None
    incident_id: uuid.UUID | None = None


class PlaceHistory(BaseModel):
    given: int
    fell_through: int
    kg: float
    last: datetime | None


class PlaceOut(BaseModel):
    ref: str
    name: str
    kind: str
    kind_label: str
    lat: float
    lon: float
    address: str | None
    phone: str | None
    website: str | None
    opening_hours: str | None
    note: str | None
    source: str
    distance_km: float
    eta_min: float
    open_now: bool | None
    fits_window: bool
    score: float
    breakdown: dict
    notes: list[str]
    history: PlaceHistory
    agreed_here: bool


class NearbyOut(BaseModel):
    origin_lat: float
    origin_lon: float
    origin: Literal["device", "canteen"]
    canteen_lat: float
    canteen_lon: float
    radius_km: int
    max_radius_km: int
    route: str
    places: list[PlaceOut]
    counts: dict[str, int]
    fetched_at: datetime | None
    stale: bool
    error: str | None
    attribution: str


class KindOut(BaseModel):
    key: str
    label: str
    route: str


@router.get("/kinds", response_model=list[KindOut])
def kinds(ctx: Ctx = Depends(need("places:read"))) -> list[KindOut]:
    return [KindOut(key=k, label=v.label, route=v.route) for k, v in KINDS.items()]


@router.post("/nearby", response_model=NearbyOut)
def nearby(body: NearbyIn, ctx: Ctx = Depends(need("places:read"))) -> NearbyOut:
    ctx.check_canteen(body.canteen_id)
    canteen = ctx.db.get(Canteen, body.canteen_id)
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    inc = None
    if body.incident_id:
        inc = ctx.db.get(SurplusIncident, body.incident_id)
        if inc is None:
            raise NotFound("Surplus not found.")
    route = body.route or (inc.route if inc is not None else "people")
    from_device = body.lat is not None and body.lon is not None
    lat, lon = (body.lat, body.lon) if from_device else (canteen.lat, canteen.lon)
    res = svc.nearby(ctx.db, tenant, lat=lat, lon=lon, radius_km=body.radius_km, route=route, incident=inc)
    here: set[str] = set()
    if inc is not None:
        here = {r for (r,) in ctx.db.execute(select(Donation.place_ref).where(
            Donation.incident_id == inc.id, Donation.status.in_(["agreed", "handed_over"]))).all()}
    counts: dict[str, int] = {}
    out = []
    for s in res.suggestions:
        counts[s.place.kind] = counts.get(s.place.kind, 0) + 1
        out.append(PlaceOut(
            ref=s.place.ref, name=s.place.name, kind=s.place.kind, kind_label=KINDS[s.place.kind].label, lat=s.place.lat,
            lon=s.place.lon, address=s.place.address, phone=s.place.phone, website=s.place.website,
            opening_hours=s.place.opening_hours, note=s.place.note, source=s.place.source, distance_km=s.distance_km,
            eta_min=s.eta_min, open_now=s.open_now, fits_window=s.fits_window, score=s.score, breakdown=s.breakdown,
            notes=s.notes, history=PlaceHistory(given=s.history.given, fell_through=s.history.fell_through,
                                                kg=round(s.history.kg, 1), last=s.history.last),
            agreed_here=s.place.ref in here,
        ))
    from foodwise.domain.settings import effective_settings

    return NearbyOut(
        origin_lat=round(lat, 4), origin_lon=round(lon, 4), origin="device" if from_device else "canteen",
        canteen_lat=canteen.lat, canteen_lon=canteen.lon, radius_km=res.radius_km,
        max_radius_km=int(effective_settings(tenant.settings)["places"]["max_radius_km"]), route=route,
        places=out, counts=counts, fetched_at=res.fetched_at, stale=res.stale, error=res.error, attribution=ATTRIBUTION,
    )


class GivenPlace(BaseModel):
    place_ref: str
    name: str
    kind: str
    kind_label: str
    phone: str | None
    address: str | None
    lat: float | None
    lon: float | None
    times: int
    fell_through: int
    kg: float
    last: datetime


@router.get("/history", response_model=list[GivenPlace])
def given_before(ctx: Ctx = Depends(need("places:read"))) -> list[GivenPlace]:
    """Places this organisation has given food to, most recent first."""
    rows = ctx.db.execute(
        select(Donation.place_ref, func.max(Donation.place_name), func.max(Donation.place_kind),
               func.max(Donation.place_phone), func.max(Donation.place_address), func.max(Donation.place_lat),
               func.max(Donation.place_lon), func.count().filter(Donation.status == "handed_over"),
               func.count().filter(Donation.status == "fell_through"),
               func.coalesce(func.sum(Donation.weight_kg).filter(Donation.status == "handed_over"), 0),
               func.max(Donation.created_at))
        .group_by(Donation.place_ref).order_by(func.max(Donation.created_at).desc()).limit(100)
    ).all()
    return [GivenPlace(place_ref=r[0], name=r[1], kind=r[2], kind_label=KINDS.get(r[2], KINDS["ngo"]).label, phone=r[3],
                       address=r[4], lat=r[5], lon=r[6], times=r[7], fell_through=r[8], kg=round(float(r[9]), 1),
                       last=r[10]) for r in rows]

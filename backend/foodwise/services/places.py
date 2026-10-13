"""Find places near a point that can take surplus food, using OpenStreetMap.

Location privacy: the point is rounded to about 1 km before it is used as a cache key or sent
to Overpass, and it is never stored against an organisation or a person.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from foodwise.config import get_settings
from foodwise.core import telemetry
from foodwise.core.geo import haversine_km
from foodwise.core.timeutil import tz
from foodwise.db.base import system_session
from foodwise.db.models import Donation, PlaceCache, SurplusIncident, Tenant
from foodwise.domain import places as P
from foodwise.domain.settings import effective_settings

log = logging.getLogger("foodwise.places")
FRESH_FOR = timedelta(days=3)
EMPTY_FRESH_FOR = timedelta(hours=6)  # an empty area is re-checked sooner: someone may have mapped it
RADII = (2, 5, 10, 15, 25)


def user_agent() -> str:
    return f"FoodWise/1.0 (food rescue for college kitchens; {get_settings().contact})"


def snap_radius(km: float) -> int:
    return next((r for r in RADII if r >= km), RADII[-1])


def cell(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, 2), round(lon, 2)


def overpass(query: str) -> list[dict]:
    """Ask each configured Overpass server in turn. Raises the last error if all fail."""
    last: Exception | None = None
    for url in get_settings().overpass_urls:
        try:
            with telemetry.external("openstreetmap"):
                resp = httpx.post(url, data={"data": query}, timeout=30.0, headers={"User-Agent": user_agent()})
                resp.raise_for_status()
                body = resp.json()
                # Overpass reports a query that ran out of time as a 200 with a remark and no data.
                if "runtime error" in (body.get("remark") or ""):
                    raise ValueError(body["remark"][:200])
                return body.get("elements", [])
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("overpass %s failed: %s", url, exc)
            last = exc
    raise last or RuntimeError("no overpass server configured")


@dataclass
class Found:
    places: list[P.Place]
    fetched_at: datetime | None
    stale: bool
    error: str | None


def _key(route: str, clat: float, clon: float, radius: int) -> str:
    return f"v1:{route}:{clat:.2f}:{clon:.2f}:{radius}"


def find(route: str, lat: float, lon: float, radius_km: float) -> Found:
    """Cached for a few days per (route, ~1 km cell, radius). A search can be answered from a
    cached wider search of the same cell, and stale results are served if OSM is down."""
    clat, clon = cell(lat, lon)
    radius = snap_radius(radius_km)
    key = _key(route, clat, clon, radius)
    now = datetime.now(UTC)
    stale, stale_at = None, None
    with system_session() as db:
        for r in [x for x in RADII if x >= radius]:
            cached = db.get(PlaceCache, _key(route, clat, clon, r))
            if cached is None:
                continue
            within = [P.Place(**p) for p in cached.results]
            if r > radius:
                within = [p for p in within if haversine_km(clat, clon, p.lat, p.lon) <= radius]
            fresh_for = FRESH_FOR if cached.results else EMPTY_FRESH_FOR
            if now - cached.fetched_at < fresh_for:
                return Found(within, cached.fetched_at, False, None)
            if stale is None:
                stale, stale_at = within, cached.fetched_at
    if get_settings().places_provider == "none":
        return Found(stale or [], stale_at, stale is not None, None if stale else "Place search is turned off.")
    query = P.QUERIES[route].format(r=int(radius * 1000), lat=clat, lon=clon)
    try:
        elements = overpass(query)
    except Exception:  # noqa: BLE001
        message = "Could not reach OpenStreetMap just now."
        return Found(stale or [], stale_at, stale is not None, message if not stale else f"{message} Showing saved results.")
    found = P.dedupe([p for p in (P.from_osm(e) for e in elements) if p and P.KINDS[p.kind].route == route])
    with system_session() as db:
        stmt = insert(PlaceCache).values(key=key, results=[asdict(p) for p in found], fetched_at=now)
        db.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"results": stmt.excluded.results,
                                                                            "fetched_at": stmt.excluded.fetched_at}))
        db.commit()
    return Found(found, now, False, None)


def warm(lat: float, lon: float, radius_km: float) -> dict:
    """Refresh both searches around a canteen ahead of time (daily job), so the rescue page is
    instant and still works if OpenStreetMap is slow at the moment food is left over."""
    out = {}
    for route in ("people", "divert"):
        f = find(route, lat, lon, radius_km)
        out[route] = {"places": len(f.places), "error": f.error}
    return out


def history(db: Session) -> dict[str, P.History]:
    """This organisation's track record with each place (tenant-scoped session)."""
    rows = db.execute(
        select(Donation.place_ref, Donation.status, func.count(), func.coalesce(func.sum(Donation.weight_kg), 0),
               func.max(Donation.created_at))
        .group_by(Donation.place_ref, Donation.status)
    ).all()
    out: dict[str, P.History] = {}
    for ref, status, n, kg, last in rows:
        h = out.setdefault(ref, P.History())
        if status == "handed_over":
            h.given += n
            h.kg += float(kg)
        elif status == "fell_through":
            h.fell_through += n
        h.last = max(h.last, last) if h.last and last else (last or h.last)
    return out


def remembered(db: Session, route: str, origin: tuple[float, float], radius_km: float) -> list[P.Place]:
    """Places this organisation gave to before (typed in by hand, or from an older search) that
    are inside the radius, so they show up even when the map does not list them."""
    latest = (select(Donation.place_ref, func.max(Donation.created_at).label("at"))
              .where(Donation.place_lat.is_not(None), ~Donation.place_ref.like("demo:%"))
              .group_by(Donation.place_ref).subquery())
    rows = db.execute(select(Donation).join(latest, (Donation.place_ref == latest.c.place_ref)
                                            & (Donation.created_at == latest.c.at))).scalars()
    out = []
    for d in rows:
        if P.KINDS.get(d.place_kind, P.KINDS["ngo"]).route != route:
            continue
        if haversine_km(origin[0], origin[1], d.place_lat, d.place_lon) > radius_km:
            continue
        out.append(P.Place(ref=d.place_ref, name=d.place_name, kind=d.place_kind if d.place_kind in P.KINDS else "ngo",
                           lat=d.place_lat, lon=d.place_lon, address=d.place_address, phone=d.place_phone,
                           source="history"))
    return out


@dataclass
class Nearby:
    origin: tuple[float, float]
    radius_km: int
    route: str
    suggestions: list[P.Suggestion]
    fetched_at: datetime | None
    stale: bool
    error: str | None


def nearby(db: Session, tenant: Tenant, *, lat: float, lon: float, radius_km: float | None = None,
           route: str = "people", incident: SurplusIncident | None = None, now: datetime | None = None) -> Nearby:
    settings = effective_settings(tenant.settings)
    cfg = settings["places"]
    radius = snap_radius(min(float(radius_km or cfg["radius_km"]), float(cfg["max_radius_km"])))
    found = find(route, lat, lon, radius)
    have = {p.ref for p in found.places}
    extra = [p for p in remembered(db, route, (lat, lon), radius) if p.ref not in have]
    in_radius = [p for p in found.places if haversine_km(lat, lon, p.lat, p.lon) <= radius * 1.05]
    now = now or datetime.now(UTC)
    zone = tz(tenant.timezone)
    safe_until = incident.safe_until.astimezone(zone) if incident is not None and incident.safe_until else None
    ranked = P.rank(
        P.dedupe([*in_radius, *extra]), origin=(lat, lon), radius_km=radius, now_local=now.astimezone(zone),
        safe_until_local=safe_until, buffer_min=float(settings["safety"]["handling_buffer_min"]), history=history(db),
    )
    return Nearby(origin=(lat, lon), radius_km=radius, route=route, suggestions=ranked, fetched_at=found.fetched_at,
                  stale=found.stale, error=found.error)


def manual_ref(name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-")[:40]
    return f"manual:{slug or uuid.uuid4().hex[:8]}"

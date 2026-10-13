"""Surplus rescue: detect, check food safety, find a nearby place, record the handover.

FoodWise does not message anyone on its own. It shows the kitchen which places nearby can use
the food (from OpenStreetMap, see services.places), with a ready-to-send message, a phone link
and directions. Staff record who agreed to take how much, and the handover; impact is computed
from what was actually handed over.

Concurrency: agreeing a donation locks the incident row, so two people recording donations at
the same moment cannot give away more portions than exist.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from foodwise.core import events, jobs
from foodwise.core.errors import Conflict, NotFound, Unprocessable
from foodwise.core.timeutil import tz
from foodwise.db.models import (
    Canteen,
    Dish,
    Donation,
    IncidentEvent,
    ServiceLog,
    SurplusIncident,
    Tenant,
)
from foodwise.domain import places as P
from foodwise.domain import safety
from foodwise.domain.incident_state import transition
from foodwise.domain.settings import effective_settings

log = logging.getLogger("foodwise.incidents")
STANDARD_PORTION_KG = 0.25


def portions_for(qty: float, unit: str, portion_kg: float) -> float:
    if unit == "plates":
        return qty
    return qty * portion_kg / STANDARD_PORTION_KG


def add_event(db: Session, inc: SurplusIncident, kind: str, message: str, actor: str = "system", **data) -> None:
    # Wall-clock time per event (not the transaction's start), so events in one request keep their order.
    db.add(IncidentEvent(tenant_id=inc.tenant_id, incident_id=inc.id, kind=kind, message=message, actor=actor, data=data,
                         created_at=datetime.now(UTC)))
    events.publish(db, f"incident:{inc.id}", {"kind": kind})
    events.publish(db, f"tenant:{inc.tenant_id}:incidents", {"incident_id": str(inc.id), "kind": kind})


def surplus_threshold(db: Session, canteen_id: uuid.UUID, cfg: dict) -> dict:
    """Large = at least the larger of the absolute minimum and this canteen's historical P95 waste."""
    p95_kg = None
    if cfg.get("use_p95_waste", True):
        p95_kg = db.execute(
            text(
                """
                SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY kg) FROM (
                  SELECT sum(l.unserved_qty * d.portion_kg) AS kg
                  FROM service_logs l JOIN dishes d ON d.id = l.dish_id
                  WHERE l.canteen_id = :c AND l.service_date >= current_date - 90
                  GROUP BY l.service_date, l.meal) s
                """
            ),
            {"c": canteen_id},
        ).scalar()
    required_kg = max(float(cfg["surplus_min_kg"]), float(p95_kg or 0))
    return {
        "min_portions": cfg["surplus_min_portions"],
        "min_kg": cfg["surplus_min_kg"],
        "p95_kg": round(float(p95_kg), 1) if p95_kg is not None else None,
        "required_kg": round(required_kg, 1),
    }


def create_incident(
    db: Session,
    tenant: Tenant,
    canteen: Canteen,
    *,
    trigger: str,
    items: list[dict],
    notes: str | None,
    actor_id: uuid.UUID | None,
    actor_label: str,
    force: bool = False,
    now: datetime | None = None,
) -> SurplusIncident:
    """items: [{dish_id, qty, cooked_at, storage}]"""
    now = now or datetime.now(UTC)
    settings = effective_settings(tenant.settings)
    dish_ids = [uuid.UUID(str(i["dish_id"])) for i in items]
    dishes = {d.id: d for d in db.execute(select(Dish).where(Dish.id.in_(dish_ids))).scalars()}
    rows, total_portions, total_kg = [], 0.0, 0.0
    for item in items:
        d = dishes.get(uuid.UUID(str(item["dish_id"])))
        if d is None:
            raise Unprocessable("One of the dishes is not on this organisation's list.")
        qty = float(item["qty"])
        if qty <= 0:
            raise Unprocessable("Quantities must be more than zero.")
        cooked_at = item["cooked_at"] if isinstance(item["cooked_at"], datetime) else datetime.fromisoformat(str(item["cooked_at"]))
        kg = qty * (1.0 if d.unit in ("kg", "litres") else d.portion_kg)
        portions = portions_for(qty, d.unit, d.portion_kg)
        total_portions += portions
        total_kg += kg
        rows.append({
            "dish_id": str(d.id), "name": d.name, "qty": qty, "unit": d.unit, "kg": round(kg, 2),
            "portions": round(portions, 1), "cooked_at": cooked_at.isoformat(), "storage": item.get("storage", "hot_held"),
            "diet": d.diet, "food_category": d.food_category, "allergens": list(d.allergens or []),
            "cost": round(qty * d.cost_per_unit, 0),
        })

    threshold = surplus_threshold(db, canteen.id, settings["thresholds"])
    meets = total_kg >= threshold["required_kg"] or total_portions >= threshold["min_portions"]
    threshold["meets"] = meets
    inc = SurplusIncident(
        tenant_id=tenant.id, canteen_id=canteen.id, status="detected", trigger=trigger, items=rows,
        total_portions=round(total_portions), total_kg=round(total_kg, 1), threshold=threshold,
        pickup_point=canteen.pickup_point, notes=notes, created_by=actor_id,
    )
    db.add(inc)
    db.flush()
    add_event(db, inc, "detected", f"{round(total_portions)} portions ({total_kg:.1f} kg) reported", actor_label,
              trigger=trigger)

    if not meets and not force:
        transition(inc, "rejected")
        add_event(db, inc, "rejected",
                  f"Below the large-surplus threshold ({threshold['required_kg']:.0f} kg or "
                  f"{threshold['min_portions']} portions). Logged for learning.")
        return inc

    assessment = safety.assess(
        [{"food_category": r["food_category"], "storage": r["storage"],
          "cooked_at": datetime.fromisoformat(r["cooked_at"])} for r in rows],
        now, settings["safety"],
    )
    for item, s in zip(rows, assessment.items, strict=True):
        item["safe_until"] = s.safe_until.isoformat() if s.safe_until else None
        item["route"] = s.route
    inc.items = rows
    inc.safe_until = assessment.safe_until
    inc.route = assessment.route
    transition(inc, "open")
    if assessment.route == "divert":
        add_event(db, inc, "safety", "Not safe to give to people: " + "; ".join(assessment.reasons)
                  + ". Look for an animal shelter, gaushala or compost site instead.")
        return inc
    left = int(assessment.minutes_left)
    add_event(db, inc, "safety", f"Safety check passed: safe for about {left // 60} h {left % 60} min",
              safe_until=inc.safe_until.isoformat() if inc.safe_until else None)
    if inc.safe_until:
        jobs.enqueue(db, "surplus_expiry", {"incident_id": str(inc.id)}, tenant_id=tenant.id, run_at=inc.safe_until,
                     dedupe_key=f"expiry:{inc.id}")
    return inc


# --------------------------------------------------------------------------- donations


def agreed_portions(db: Session, inc: SurplusIncident) -> float:
    return float(db.execute(
        select(func.coalesce(func.sum(Donation.portions), 0))
        .where(Donation.incident_id == inc.id, Donation.status.in_(["agreed", "handed_over"]))
    ).scalar_one())


def remaining_portions(db: Session, inc: SurplusIncident) -> float:
    return max(0.0, float(inc.total_portions) - agreed_portions(db, inc))


def agree(db: Session, inc: SurplusIncident, *, place: dict, portions: float, contact_method: str,
          contact_name: str | None, notes: str | None, actor_id: uuid.UUID | None, actor: str,
          now: datetime | None = None) -> Donation:
    """A place agreed to take `portions`. Locks the incident so concurrent agreements cannot
    over-allocate the food."""
    now = now or datetime.now(UTC)
    inc = db.execute(select(SurplusIncident).where(SurplusIncident.id == inc.id).with_for_update()).scalar_one()
    if inc.status not in ("open", "arranged"):
        raise Conflict("This surplus is no longer available to give away.")
    if inc.safe_until and inc.safe_until <= now and inc.route == "people":
        raise Conflict("The safe window has passed. This food should not be given to people now.")
    left = remaining_portions(db, inc)
    if portions <= 0:
        raise Unprocessable("Enter how many portions they will take.")
    if portions > left + 0.5:
        raise Conflict(f"Only {left:.0f} portions are still unallocated.", code="over_allocated")
    kind = place.get("kind") if place.get("kind") in P.KINDS else "ngo"
    if inc.route == "divert" and P.KINDS[kind].route == "people":
        raise Unprocessable("This food is not safe for people any more. Choose an animal shelter, gaushala or compost site.")
    d = Donation(
        tenant_id=inc.tenant_id, incident_id=inc.id, place_ref=str(place["ref"])[:80], place_name=str(place["name"])[:200],
        place_kind=kind, place_lat=place.get("lat"), place_lon=place.get("lon"), place_phone=place.get("phone"),
        place_address=place.get("address"), distance_km=place.get("distance_km"), portions=round(min(portions, left), 1),
        status="agreed", contact_method=contact_method, contact_name=contact_name, notes=notes, created_by=actor_id,
    )
    db.add(d)
    db.flush()
    if inc.first_agreed_at is None:
        inc.first_agreed_at = now
    transition(inc, "arranged")
    add_event(db, inc, "agreed", f"{d.place_name} agreed to take {d.portions:.0f} portions", actor,
              donation=str(d.id), method=contact_method)
    return d


def _donation(db: Session, inc: SurplusIncident, donation_id: uuid.UUID) -> Donation:
    d = db.get(Donation, donation_id)
    if d is None or d.incident_id != inc.id:
        raise NotFound("Donation not found.")
    return d


def hand_over(db: Session, inc: SurplusIncident, donation_id: uuid.UUID, *, weight_kg: float, actor: str,
              now: datetime | None = None) -> Donation:
    d = _donation(db, inc, donation_id)
    if d.status != "agreed":
        raise Conflict("Only an agreed donation can be marked as handed over.")
    if weight_kg <= 0:
        raise Unprocessable("Enter the weight handed over, in kg.")
    d.status = "handed_over"
    d.weight_kg = round(weight_kg, 1)
    d.handed_over_at = now or datetime.now(UTC)
    db.flush()
    add_event(db, inc, "handed_over", f"Handed {weight_kg:.1f} kg to {d.place_name}", actor, donation=str(d.id))
    _maybe_close(db, inc)
    return d


def fell_through(db: Session, inc: SurplusIncident, donation_id: uuid.UUID, *, reason: str | None, actor: str) -> Donation:
    d = _donation(db, inc, donation_id)
    if d.status != "agreed":
        raise Conflict("Only an agreed donation can fall through.")
    d.status = "fell_through"
    d.notes = ((d.notes + " | ") if d.notes else "") + (reason or "Fell through")
    db.flush()
    add_event(db, inc, "fell_through", f"{d.place_name} could not take it{': ' + reason if reason else ''}", actor,
              donation=str(d.id))
    pending = db.execute(select(func.count()).select_from(Donation).where(
        Donation.incident_id == inc.id, Donation.status.in_(["agreed", "handed_over"]))).scalar_one()
    if inc.status == "arranged" and pending == 0:
        transition(inc, "open")
    return d


def _maybe_close(db: Session, inc: SurplusIncident) -> None:
    """Close once nothing is waiting and too little is left to be worth another place."""
    tenant = db.get(Tenant, inc.tenant_id)
    min_useful = float(effective_settings(tenant.settings)["places"]["min_useful_portions"])
    waiting = db.execute(select(func.count()).select_from(Donation).where(
        Donation.incident_id == inc.id, Donation.status == "agreed")).scalar_one()
    if waiting == 0 and remaining_portions(db, inc) < min_useful:
        close(db, inc, "All food handed over")


def close(db: Session, inc: SurplusIncident, reason: str = "Closed", disposal: str | None = None) -> None:
    if inc.status not in ("open", "arranged"):
        raise Conflict("Only an open surplus can be closed.")
    waiting = db.execute(select(func.count()).select_from(Donation).where(
        Donation.incident_id == inc.id, Donation.status == "agreed")).scalar_one()
    if waiting:
        raise Conflict("Mark each agreed donation as handed over or fallen through first.")
    tenant = db.get(Tenant, inc.tenant_id)
    cfg = effective_settings(tenant.settings)["impact"]
    kg = float(db.execute(select(func.coalesce(func.sum(Donation.weight_kg), 0)).where(
        Donation.incident_id == inc.id, Donation.status == "handed_over")).scalar_one())
    places = [n for (n,) in db.execute(select(Donation.place_name).where(
        Donation.incident_id == inc.id, Donation.status == "handed_over").order_by(Donation.created_at)).all()]
    share = min(1.0, kg / inc.total_kg) if inc.total_kg else 0.0
    rupees = sum(float(i.get("cost", 0)) for i in inc.items) * share
    to_people = inc.route == "people"
    inc.impact = {
        "kg": round(kg, 1),
        "meals": round(kg / cfg["kg_per_meal"]) if to_people else 0,
        "co2e_kg": round(kg * cfg["co2e_kg_per_kg_food"], 1),
        "rupees": round(rupees) if to_people else 0,
        "minutes_to_agree": round((inc.first_agreed_at - inc.created_at).total_seconds() / 60, 1) if inc.first_agreed_at else None,
        "route": "people" if to_people else "divert",
        "disposed_kg": round(max(0.0, inc.total_kg - kg), 1),
        "disposal": disposal,
        "places": places,
    }
    transition(inc, "closed")
    inc.closed_at = datetime.now(UTC)
    msg = f"{reason}. {kg:.1f} kg handed over"
    if to_people and kg:
        msg += f", about {inc.impact['meals']} meals"
    if inc.impact["disposed_kg"] >= 0.5:
        msg += f"; {inc.impact['disposed_kg']:.1f} kg {'disposed' if not disposal else 'sent to ' + disposal}"
    add_event(db, inc, "closed", msg + ".")


def expire(db: Session, incident_id: uuid.UUID, now: datetime | None = None) -> str:
    """At the end of the safe window the food must not go to people any more. If nobody has
    agreed to take it, the surplus switches to animal shelters, gaushalas and compost."""
    now = now or datetime.now(UTC)
    inc = db.execute(select(SurplusIncident).where(SurplusIncident.id == incident_id).with_for_update()).scalar_one_or_none()
    if inc is None or inc.route != "people" or inc.status not in ("open", "arranged"):
        return "skipped"
    if inc.status == "arranged":
        add_event(db, inc, "safe_window", "The safe window has passed. Hand over only what was already collected.")
        return "warned"
    inc.route = "divert"
    inc.items = [{**i, "route": "divert"} for i in inc.items]
    add_event(db, inc, "safe_window", "The safe window passed before anyone took the food. Do not give it to people "
                                      "now; offer it to an animal shelter, gaushala or compost site, or dispose of it.")
    return "diverted"


def cancel(db: Session, inc: SurplusIncident, reason: str, actor: str) -> None:
    transition(inc, "cancelled")
    for d in db.execute(select(Donation).where(Donation.incident_id == inc.id, Donation.status == "agreed")).scalars():
        d.status = "fell_through"
    inc.closed_at = datetime.now(UTC)
    add_event(db, inc, "cancelled", f"Cancelled: {reason}", actor)


def share_text(tenant: Tenant, canteen: Canteen, inc: SurplusIncident) -> str:
    """The message staff send by WhatsApp or read out on the phone."""
    zone = tz(tenant.timezone)
    food = ", ".join(f"{i['name']} ({i['qty']:g} {i['unit']})" for i in inc.items if i.get("route", inc.route) == inc.route)
    if inc.route == "people":
        until = inc.safe_until.astimezone(zone).strftime("%H:%M") if inc.safe_until else "soon"
        return (f"Namaste, this is the kitchen at {canteen.name}, {tenant.name}. We have about "
                f"{inc.total_portions:.0f} portions of freshly cooked food left today: {food}. It is safe to eat until "
                f"{until}. Could you collect some or all of it from {inc.pickup_point or canteen.name}? Please bring "
                f"clean, covered containers. Thank you.")
    return (f"Namaste, this is the kitchen at {canteen.name}, {tenant.name}. We have about {inc.total_kg:.0f} kg of "
            f"cooked food that is no longer fit for people ({food}). Could you use it for animal feed or compost? "
            f"Pickup from {inc.pickup_point or canteen.name}.")


def recent_surplus_draft(db: Session, canteen: Canteen, service_date, meal: str) -> list[dict]:
    """Prefill a surplus report from today's log for one meal (unserved quantities)."""
    rows = db.execute(
        select(ServiceLog, Dish).join(Dish, Dish.id == ServiceLog.dish_id).where(
            ServiceLog.canteen_id == canteen.id, ServiceLog.service_date == service_date, ServiceLog.meal == meal,
            ServiceLog.unserved_qty > 0,
        )
    ).all()
    return [{"dish_id": str(d.id), "name": d.name, "qty": float(log.unserved_qty), "unit": d.unit} for log, d in rows]

"""Public endpoints for students. No account needed; every call is authorised by a signed,
expiring QR-code token (purpose "session").

Abuse controls: per-IP and per-device rate limits, one rating per dish per session per device,
duplicate-text and burst detection (suspected spam is down-weighted, not dropped), and PII is
scrubbed from free text before it is stored.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from foodwise.core import security
from foodwise.core.deps import client_ip
from foodwise.core.errors import NotFound, Unprocessable
from foodwise.core.ratelimit import limiter
from foodwise.core.timeutil import at_local, local_now, meal_window
from foodwise.db.base import tenant_session
from foodwise.db.models import (
    Canteen,
    Dish,
    Feedback,
    MenuEntry,
    Rsvp,
    Tenant,
    Vote,
)
from foodwise.domain.settings import effective_settings
from foodwise.domain.textanalysis import analyze, spam_score
from foodwise.services.dish_images import image_url

router = APIRouter(prefix="/public", tags=["public"])
GRACE_H = 3


def session_token(tenant_id: uuid.UUID, canteen: Canteen, day: date, meal: str, tz_name: str) -> tuple[str, datetime]:
    m = next(x for x in canteen.meals if x["key"] == meal)
    _, end = meal_window(m, day, tz_name)
    expires = end + timedelta(hours=GRACE_H)
    ttl = max(60, int((expires - datetime.now(UTC)).total_seconds()))
    token = security.sign_link("session", {"t": str(tenant_id), "c": str(canteen.id), "d": day.isoformat(), "m": meal}, ttl)
    return token, expires


def _session(token: str) -> dict:
    try:
        return security.verify_link("session", token)
    except security.LinkError as exc:
        raise NotFound("This QR code has expired. Scan the code on today's table.", code="session_expired") from exc


def _device(tenant_id, device_id: str | None) -> str:
    if not device_id or len(device_id) < 8 or len(device_id) > 80:
        raise Unprocessable("Missing device identifier. Reload the page.")
    return security.hash_device(tenant_id, device_id)


class SessionDish(BaseModel):
    id: uuid.UUID
    name: str
    name_hi: str | None
    diet: str
    course: str
    my_rating: int | None
    image_url: str | None = None
    image_credit: str | None = None


class RsvpSlot(BaseModel):
    date: date
    meal: str
    label: str
    status: str | None
    open: bool


class VoteInfo(BaseModel):
    week_start: date
    candidates: list[SessionDish]
    my_vote: uuid.UUID | None


class SessionOut(BaseModel):
    college: str
    canteen: str
    meal: str
    meal_label: str
    date: date
    dishes: list[SessionDish]
    rsvp: list[RsvpSlot]
    vote: VoteInfo | None


VOTE_CANDIDATES = ["Veg biryani", "Chole bhature", "Pav bhaji", "Dal makhani"]


def _rsvp_slots(db, tenant: Tenant, canteen: Canteen, device: str) -> list[RsvpSlot]:
    settings = effective_settings(tenant.settings)
    now = local_now(tenant.timezone)
    tomorrow = now.date() + timedelta(days=1)
    cutoff = at_local(now.date(), settings["rsvp"]["cutoff"], tenant.timezone)
    mine = {
        (r.service_date, r.meal): r.status
        for r in db.execute(select(Rsvp).where(Rsvp.canteen_id == canteen.id, Rsvp.device_hash == device,
                                               Rsvp.service_date >= now.date())).scalars()
    }
    return [RsvpSlot(date=tomorrow, meal=m["key"], label=m["label"], status=mine.get((tomorrow, m["key"])), open=now < cutoff)
            for m in canteen.meals]


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


@router.get("/session/{token}", response_model=SessionOut)
def get_session(token: str, request: Request, x_device_id: str | None = Header(default=None)) -> SessionOut:
    limiter.hit(f"pub-ip:{client_ip(request)}", 120, 60)
    s = _session(token)
    with tenant_session(s["t"]) as db:
        tenant = db.get(Tenant, uuid.UUID(s["t"]))
        canteen = db.get(Canteen, uuid.UUID(s["c"]))
        if tenant is None or canteen is None:
            raise NotFound("This QR code is no longer valid.")
        device = _device(tenant.id, x_device_id) if x_device_id else None
        day = date.fromisoformat(s["d"])
        dishes = db.execute(
            select(Dish).join(MenuEntry, MenuEntry.dish_id == Dish.id)
            .where(MenuEntry.canteen_id == canteen.id, MenuEntry.service_date == day, MenuEntry.meal == s["m"])
            .order_by(Dish.course, Dish.name)
        ).scalars().all()
        mine = {}
        if device:
            mine = {f.dish_id: f.rating for f in db.execute(
                select(Feedback).where(Feedback.canteen_id == canteen.id, Feedback.service_date == day,
                                       Feedback.meal == s["m"], Feedback.device_hash == device)).scalars()}
        vote = None
        if canteen.kind == "mess":
            cands = db.execute(select(Dish).where(Dish.name.in_(VOTE_CANDIDATES)).order_by(Dish.name)).scalars().all()
            week = _week_start(day)
            my_vote = db.execute(select(Vote.dish_id).where(Vote.canteen_id == canteen.id, Vote.week_start == week,
                                                             Vote.device_hash == device)).scalar() if device else None
            if cands:
                vote = VoteInfo(week_start=week, my_vote=my_vote, candidates=[
                    SessionDish(id=d.id, name=d.name, name_hi=d.name_hi, diet=d.diet, course=d.course, my_rating=None,
                                image_url=image_url(d), image_credit=d.image_credit) for d in cands])
        meal_label = next((m["label"] for m in canteen.meals if m["key"] == s["m"]), s["m"])
        return SessionOut(
            college=tenant.name, canteen=canteen.name, meal=s["m"], meal_label=meal_label, date=day,
            dishes=[SessionDish(id=d.id, name=d.name, name_hi=d.name_hi, diet=d.diet, course=d.course,
                                my_rating=mine.get(d.id), image_url=image_url(d), image_credit=d.image_credit)
                    for d in dishes],
            rsvp=_rsvp_slots(db, tenant, canteen, device) if canteen.kind == "mess" and device else [],
            vote=vote,
        )


class FeedbackIn(BaseModel):
    token: str = Field(max_length=600)
    dish_id: uuid.UUID
    rating: int = Field(ge=1, le=5)
    aspects: dict[Literal["taste", "quantity", "temperature", "freshness", "variety", "hygiene"], Literal[-1, 1]] = {}
    portion: Literal["too_much", "right", "too_little"] | None = None
    text: str | None = Field(default=None, max_length=600)


class Thanks(BaseModel):
    ok: bool = True
    message: str


@router.post("/feedback", response_model=Thanks)
def post_feedback(body: FeedbackIn, request: Request, x_device_id: str | None = Header(default=None)) -> Thanks:
    ip = client_ip(request)
    limiter.hit(f"pub-fb-ip:{ip}", 40, 60)
    s = _session(body.token)
    with tenant_session(s["t"]) as db:
        tenant = db.get(Tenant, uuid.UUID(s["t"]))
        device = _device(tenant.id, x_device_id)
        limiter.hit(f"pub-fb-dev:{device}", 20, 60)
        day = date.fromisoformat(s["d"])
        on_menu = db.execute(select(MenuEntry.id).where(
            MenuEntry.canteen_id == uuid.UUID(s["c"]), MenuEntry.service_date == day, MenuEntry.meal == s["m"],
            MenuEntry.dish_id == body.dish_id)).first()
        if not on_menu:
            raise Unprocessable("That dish was not served at this meal.")
        settings = effective_settings(tenant.settings)
        analysis = analyze(body.text) if body.text and body.text.strip() else None
        recent_identical = 0
        if analysis:
            recent_identical = db.execute(select(func.count()).select_from(Feedback).where(
                Feedback.canteen_id == uuid.UUID(s["c"]), Feedback.text == analysis.clean_text,
                Feedback.created_at > datetime.now(UTC) - timedelta(hours=6))).scalar_one()
        burst = db.execute(select(func.count()).select_from(Feedback).where(
            Feedback.ip_hash == security.hash_device(tenant.id, f"ip:{ip}"),
            Feedback.created_at > datetime.now(UTC) - timedelta(minutes=10))).scalar_one()
        spam = spam_score(analysis.clean_text if analysis else None, recent_identical, burst)
        values = dict(
            id=uuid.uuid4(), tenant_id=tenant.id, canteen_id=uuid.UUID(s["c"]), service_date=day, meal=s["m"],
            dish_id=body.dish_id, rating=body.rating, aspects=dict(body.aspects), portion=body.portion,
            text=analysis.clean_text if analysis else None,
            text_expires_at=datetime.now(UTC) + timedelta(days=settings["feedback"]["text_retention_days"]) if analysis else None,
            language=analysis.language if analysis else None, sentiment=analysis.sentiment if analysis else None,
            text_aspects=analysis.aspects if analysis else {}, device_hash=device,
            ip_hash=security.hash_device(tenant.id, f"ip:{ip}"), spam_score=spam, weight=round(max(0.1, 1 - spam), 2),
        )
        stmt = insert(Feedback).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "canteen_id", "service_date", "meal", "dish_id", "device_hash"],
            set_={k: stmt.excluded[k] for k in ("rating", "aspects", "portion", "text", "text_expires_at", "language",
                                                 "sentiment", "text_aspects", "spam_score", "weight")},
        )
        db.execute(stmt)
        db.commit()
    return Thanks(message="Thanks. The mess team reviews these every week.")


class RsvpIn(BaseModel):
    token: str = Field(max_length=600)
    date: date
    meal: str = Field(max_length=20)
    status: Literal["eating", "skipping"]


@router.post("/rsvp", response_model=list[RsvpSlot])
def post_rsvp(body: RsvpIn, request: Request, x_device_id: str | None = Header(default=None)) -> list[RsvpSlot]:
    limiter.hit(f"pub-rsvp-ip:{client_ip(request)}", 30, 60)
    s = _session(body.token)
    with tenant_session(s["t"]) as db:
        tenant = db.get(Tenant, uuid.UUID(s["t"]))
        canteen = db.get(Canteen, uuid.UUID(s["c"]))
        device = _device(tenant.id, x_device_id)
        slots = _rsvp_slots(db, tenant, canteen, device)
        slot = next((x for x in slots if x.date == body.date and x.meal == body.meal), None)
        if slot is None:
            raise Unprocessable("You can RSVP for tomorrow's meals only.")
        if not slot.open:
            raise Unprocessable("RSVPs for tomorrow have closed. They reopen after midnight.")
        stmt = insert(Rsvp).values(id=uuid.uuid4(), tenant_id=tenant.id, canteen_id=canteen.id, service_date=body.date,
                                   meal=body.meal, device_hash=device, status=body.status)
        db.execute(stmt.on_conflict_do_update(
            index_elements=["tenant_id", "canteen_id", "service_date", "meal", "device_hash"],
            set_={"status": stmt.excluded.status, "updated_at": func.now()}))
        db.commit()
        return _rsvp_slots(db, tenant, canteen, device)


class VoteIn(BaseModel):
    token: str = Field(max_length=600)
    dish_id: uuid.UUID


@router.post("/vote", response_model=Thanks)
def post_vote(body: VoteIn, request: Request, x_device_id: str | None = Header(default=None)) -> Thanks:
    limiter.hit(f"pub-vote-ip:{client_ip(request)}", 20, 60)
    s = _session(body.token)
    with tenant_session(s["t"]) as db:
        tenant = db.get(Tenant, uuid.UUID(s["t"]))
        device = _device(tenant.id, x_device_id)
        dish = db.get(Dish, body.dish_id)
        if dish is None or dish.name not in VOTE_CANDIDATES:
            raise Unprocessable("That dish is not on this week's ballot.")
        week = _week_start(date.fromisoformat(s["d"]))
        stmt = insert(Vote).values(id=uuid.uuid4(), tenant_id=tenant.id, canteen_id=uuid.UUID(s["c"]), week_start=week,
                                   dish_id=body.dish_id, device_hash=device)
        db.execute(stmt.on_conflict_do_update(index_elements=["tenant_id", "canteen_id", "week_start", "device_hash"],
                                              set_={"dish_id": stmt.excluded.dish_id}))
        db.commit()
    return Thanks(message=f"Vote counted for {dish.name}.")

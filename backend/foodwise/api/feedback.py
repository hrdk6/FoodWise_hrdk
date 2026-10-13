"""What students think: dish scores, comments, QR sessions, RSVPs, votes, and menu suggestions."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from foodwise.api.public import session_token
from foodwise.config import get_settings
from foodwise.core.deps import Ctx, need
from foodwise.core.errors import NotFound, Unprocessable
from foodwise.core.timeutil import local_today
from foodwise.db.models import Canteen, Dish, Feedback, MenuEntry, Rsvp, ServiceLog, Tenant, Vote
from foodwise.domain.preference import bayesian_score, diagnose
from foodwise.domain.settings import effective_settings

router = APIRouter(prefix="/feedback", tags=["feedback"])


class DishScore(BaseModel):
    dish_id: uuid.UUID
    dish: str
    course: str
    n: int
    shown: bool
    score: float | None
    raw_mean: float | None
    trend: float | None
    liked: int
    okay: int
    disliked: int
    aspects: dict[str, int]
    portion: dict[str, int]
    sentiment: float | None
    waste_rate: float | None
    sellout_rate: float | None
    diagnosis: str | None


class Summary(BaseModel):
    canteen_id: uuid.UUID
    days: int
    responses: int
    response_rate: float | None
    canteen_mean: float | None
    k_anonymity: int
    dishes: list[DishScore]


@router.get("/summary", response_model=Summary)
def summary(canteen_id: uuid.UUID, days: int = Query(28, ge=7, le=180), ctx: Ctx = Depends(need("feedback:read"))) -> Summary:
    ctx.check_canteen(canteen_id)
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    cfg = effective_settings(tenant.settings)["feedback"]
    today = local_today(tenant.timezone)
    since = today - timedelta(days=days)
    rows = ctx.db.execute(
        select(Feedback.dish_id, Feedback.service_date, Feedback.rating, Feedback.weight, Feedback.aspects,
               Feedback.portion, Feedback.sentiment)
        .where(Feedback.canteen_id == canteen_id, Feedback.service_date >= since - timedelta(days=days))
    ).all()
    mu_rows = [r for r in rows if r.service_date >= since]
    mu = sum(r.rating for r in mu_rows) / len(mu_rows) if mu_rows else None
    by_dish: dict[uuid.UUID, list] = defaultdict(list)
    for r in rows:
        by_dish[r.dish_id].append(r)
    outcomes = {
        dish_id: (prep, unserved, sold, n)
        for dish_id, prep, unserved, sold, n in ctx.db.execute(
            select(ServiceLog.dish_id, func.sum(ServiceLog.prepared_qty), func.sum(ServiceLog.unserved_qty),
                   func.count().filter(ServiceLog.sold_out), func.count())
            .where(ServiceLog.canteen_id == canteen_id, ServiceLog.service_date >= since)
            .group_by(ServiceLog.dish_id)
        ).all()
    }
    per_service = (
        select(func.max(ServiceLog.footfall).label("ff"))
        .where(ServiceLog.canteen_id == canteen_id, ServiceLog.service_date >= since)
        .group_by(ServiceLog.service_date, ServiceLog.meal)
        .subquery()
    )
    served_ff = ctx.db.execute(select(func.sum(per_service.c.ff))).scalar()
    dishes = {d.id: d for d in ctx.db.execute(select(Dish)).scalars()}
    out = []
    for dish_id, items in by_dish.items():
        d = dishes.get(dish_id)
        if d is None:
            continue
        current = [r for r in items if r.service_date >= since]
        previous = [r for r in items if r.service_date < since]
        n = len(current)
        shown = n >= cfg["k_anonymity"]
        sc = bayesian_score([(r.service_date, r.rating, r.weight) for r in current], today, prior_mean=mu or 3.5,
                            half_life_days=cfg["half_life_days"], pseudo_count=cfg["pseudo_count"])
        prev_mean = sum(r.rating for r in previous) / len(previous) if len(previous) >= cfg["k_anonymity"] else None
        aspects: dict[str, int] = defaultdict(int)
        portion: dict[str, int] = defaultdict(int)
        for r in current:
            for a, v in (r.aspects or {}).items():
                if v < 0:
                    aspects[a] += 1
            if r.portion:
                portion[r.portion] += 1
        sentiments = [r.sentiment for r in current if r.sentiment is not None]
        prep, unserved, sold, logs_n = outcomes.get(dish_id, (None, None, 0, 0))
        waste_rate = (unserved / prep) if prep else None
        sellout_rate = (sold / logs_n) if logs_n else None
        out.append(DishScore(
            dish_id=dish_id, dish=d.name, course=d.course, n=n, shown=shown,
            score=sc.score if shown else None, raw_mean=sc.raw_mean if shown else None,
            trend=round(sc.raw_mean - prev_mean, 2) if shown and prev_mean is not None and sc.raw_mean is not None else None,
            liked=sum(1 for r in current if r.rating >= 4) if shown else 0,
            okay=sum(1 for r in current if r.rating == 3) if shown else 0,
            disliked=sum(1 for r in current if r.rating <= 2) if shown else 0,
            aspects=dict(aspects) if shown else {}, portion=dict(portion) if shown else {},
            sentiment=round(sum(sentiments) / len(sentiments), 2) if shown and sentiments else None,
            waste_rate=round(waste_rate, 3) if waste_rate is not None else None,
            sellout_rate=round(sellout_rate, 3) if sellout_rate is not None else None,
            diagnosis=diagnose(sc.score, mu or 3.5, waste_rate, sellout_rate) if shown and sc.effective_n >= cfg["min_effective_n"] else None,
        ))
    out.sort(key=lambda x: (not x.shown, -(x.score or 0)))
    return Summary(canteen_id=canteen_id, days=days, responses=len(mu_rows),
                   response_rate=round(len(mu_rows) / served_ff, 4) if served_ff else None,
                   canteen_mean=round(mu, 2) if mu else None, k_anonymity=cfg["k_anonymity"], dishes=out)


class Comment(BaseModel):
    id: uuid.UUID
    date: date
    meal: str
    dish: str
    rating: int
    text: str
    language: str | None
    sentiment: float | None
    aspects: dict
    down_weighted: bool


@router.get("/comments", response_model=list[Comment])
def comments(canteen_id: uuid.UUID, dish_id: uuid.UUID | None = None, limit: int = Query(40, ge=1, le=200),
             sentiment: str | None = Query(None, pattern="^(positive|negative)$"),
             ctx: Ctx = Depends(need("feedback:read"))) -> list[Comment]:
    ctx.check_canteen(canteen_id)
    q = (select(Feedback, Dish.name).join(Dish, Dish.id == Feedback.dish_id)
         .where(Feedback.canteen_id == canteen_id, Feedback.text.is_not(None))
         .order_by(Feedback.service_date.desc(), Feedback.created_at.desc()).limit(limit))
    if dish_id:
        q = q.where(Feedback.dish_id == dish_id)
    if sentiment == "positive":
        q = q.where(Feedback.sentiment > 0.1)
    elif sentiment == "negative":
        q = q.where(Feedback.sentiment < -0.1)
    return [Comment(id=f.id, date=f.service_date, meal=f.meal, dish=name, rating=f.rating, text=f.text,
                    language=f.language, sentiment=f.sentiment, aspects=f.text_aspects or {}, down_weighted=f.weight < 0.8)
            for f, name in ctx.db.execute(q)]


class QrOut(BaseModel):
    token: str
    url: str
    expires_at: datetime
    canteen: str
    meal: str
    date: date


@router.get("/qr", response_model=QrOut)
def qr(canteen_id: uuid.UUID, day: date, meal: str, ctx: Ctx = Depends(need("feedback:read"))) -> QrOut:
    ctx.check_canteen(canteen_id)
    canteen = ctx.db.get(Canteen, canteen_id)
    if canteen is None:
        raise NotFound("Canteen not found.")
    if meal not in {m["key"] for m in canteen.meals}:
        raise Unprocessable("That meal is not served here.")
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    token, expires = session_token(tenant.id, canteen, day, meal, tenant.timezone)
    return QrOut(token=token, url=f"{get_settings().public_base_url.rstrip('/')}/s/{token}", expires_at=expires,
                 canteen=canteen.name, meal=meal, date=day)


class RsvpDay(BaseModel):
    date: date
    meal: str
    eating: int
    skipping: int
    footfall: int | None


@router.get("/rsvp", response_model=list[RsvpDay])
def rsvp_stats(canteen_id: uuid.UUID, days: int = Query(14, ge=1, le=60), ctx: Ctx = Depends(need("feedback:read"))) -> list[RsvpDay]:
    ctx.check_canteen(canteen_id)
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    today = local_today(tenant.timezone)
    rows = ctx.db.execute(
        select(Rsvp.service_date, Rsvp.meal, func.count().filter(Rsvp.status == "eating"),
               func.count().filter(Rsvp.status == "skipping"))
        .where(Rsvp.canteen_id == canteen_id, Rsvp.service_date >= today - timedelta(days=days),
               Rsvp.service_date <= today + timedelta(days=2))
        .group_by(Rsvp.service_date, Rsvp.meal).order_by(Rsvp.service_date)
    ).all()
    ff = {(d, m): f for d, m, f in ctx.db.execute(
        select(ServiceLog.service_date, ServiceLog.meal, func.max(ServiceLog.footfall))
        .where(ServiceLog.canteen_id == canteen_id, ServiceLog.service_date >= today - timedelta(days=days))
        .group_by(ServiceLog.service_date, ServiceLog.meal)).all()}
    return [RsvpDay(date=d, meal=m, eating=e, skipping=s, footfall=ff.get((d, m))) for d, m, e, s in rows]


class VoteTally(BaseModel):
    week_start: date
    dish_id: uuid.UUID
    dish: str
    votes: int


@router.get("/votes", response_model=list[VoteTally])
def votes(canteen_id: uuid.UUID, weeks: int = Query(4, ge=1, le=26), ctx: Ctx = Depends(need("feedback:read"))) -> list[VoteTally]:
    ctx.check_canteen(canteen_id)
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    since = local_today(tenant.timezone) - timedelta(weeks=weeks)
    rows = ctx.db.execute(
        select(Vote.week_start, Vote.dish_id, Dish.name, func.count())
        .join(Dish, Dish.id == Vote.dish_id)
        .where(Vote.canteen_id == canteen_id, Vote.week_start >= since)
        .group_by(Vote.week_start, Vote.dish_id, Dish.name).order_by(Vote.week_start.desc(), func.count().desc())
    ).all()
    return [VoteTally(week_start=w, dish_id=d, dish=n, votes=c) for w, d, n, c in rows]


# --------------------------------------------------------------------------- menu optimiser


class Suggestion(BaseModel):
    date: date
    meal: str
    course: str
    current_dish: str
    current_dish_id: uuid.UUID
    suggested_dish: str
    suggested_dish_id: uuid.UUID
    reason: str
    score_gain: float


@router.get("/menu-suggestions", response_model=list[Suggestion])
def menu_suggestions(canteen_id: uuid.UUID, ctx: Ctx = Depends(need("feedback:read"))) -> list[Suggestion]:
    """Weekly menu optimiser (heuristic): swap low-rated, high-waste dishes for well-liked dishes of the
    same course and diet, without repeating a dish within three days. A manager approves each swap."""
    ctx.check_canteen(canteen_id)
    s = summary(canteen_id, 28, ctx)
    scores = {d.dish_id: d for d in s.dishes if d.shown and d.score is not None}
    if not scores:
        return []
    tenant = ctx.db.get(Tenant, ctx.tenant_id)
    today = local_today(tenant.timezone)
    week = ctx.db.execute(
        select(MenuEntry.service_date, MenuEntry.meal, Dish)
        .join(Dish, Dish.id == MenuEntry.dish_id)
        .where(MenuEntry.canteen_id == canteen_id, MenuEntry.service_date > today,
               MenuEntry.service_date <= today + timedelta(days=7))
        .order_by(MenuEntry.service_date)
    ).all()
    served_on: dict[uuid.UUID, list[date]] = defaultdict(list)
    for d, _, dish in week:
        served_on[dish.id].append(d)
    dishes = {d.id: d for d in ctx.db.execute(select(Dish).where(Dish.is_active.is_(True))).scalars()}
    out = []
    used: set[uuid.UUID] = set()
    for day, meal, dish in week:
        cur = scores.get(dish.id)
        if cur is None or cur.diagnosis not in ("replace_or_reduce", "watch"):
            continue
        best = None
        for cand_id, cand in scores.items():
            cd = dishes.get(cand_id)
            if cd is None or cd.id == dish.id or cd.course != dish.course or cd.diet != dish.diet or cand_id in used:
                continue
            if any(abs((other - day).days) <= 3 for other in served_on.get(cand_id, [])):
                continue
            gain = (cand.score or 0) - (cur.score or 0)
            if gain > 0.3 and (best is None or gain > best[1]):
                best = (cd, gain, cand)
        if best:
            cd, gain, cand = best
            used.add(cd.id)
            reason = (f"{dish.name} is rated {cur.score:.1f}/5 and {cur.waste_rate or 0:.0%} of it goes unserved; "
                      f"{cd.name} is rated {cand.score:.1f}/5.")
            out.append(Suggestion(date=day, meal=meal, course=dish.course, current_dish=dish.name, current_dish_id=dish.id,
                                  suggested_dish=cd.name,
                                  suggested_dish_id=cd.id, reason=reason, score_gain=round(gain, 2)))
    return out


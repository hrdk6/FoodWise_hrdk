"""Organisation settings, readiness, the audit trail and the message log."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from foodwise.api.common import Page
from foodwise.core import audit
from foodwise.core.deps import Ctx, need
from foodwise.core.errors import Conflict
from foodwise.core.timeutil import local_today
from foodwise.db.models import AuditLog, Canteen, ModelVersion, OutboxMessage, ServiceLog, Tenant
from foodwise.domain.newsvendor import critical_ratio
from foodwise.domain.readiness import TIER_GIVES, readiness
from foodwise.domain.settings import DEFAULT_SETTINGS, deep_merge, effective_settings

router = APIRouter(tags=["tenant"])


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    timezone: str
    currency: str
    service_mode: str
    data_sharing: str
    feedback_mode: str
    is_demo: bool
    settings: dict[str, Any]
    defaults: dict[str, Any]


class TenantPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    data_sharing: Literal["none", "anonymized_pool"] | None = None
    feedback_mode: Literal["anonymous", "verified"] | None = None
    service_mode: Literal["fixed_thali", "choice", "counter"] | None = None
    settings: dict[str, Any] | None = None


ALLOWED_SETTING_KEYS = set(DEFAULT_SETTINGS)


def _tenant_out(t: Tenant) -> TenantOut:
    return TenantOut(id=t.id, name=t.name, slug=t.slug, timezone=t.timezone, currency=t.currency,
                     service_mode=t.service_mode, data_sharing=t.data_sharing, feedback_mode=t.feedback_mode,
                     is_demo=t.is_demo, settings=effective_settings(t.settings), defaults=DEFAULT_SETTINGS)


@router.get("/tenant", response_model=TenantOut)
def get_tenant(ctx: Ctx = Depends(need("tenant:read"))) -> TenantOut:
    return _tenant_out(ctx.db.get(Tenant, ctx.tenant_id))


@router.patch("/tenant", response_model=TenantOut)
def patch_tenant(body: TenantPatch, ctx: Ctx = Depends(need("tenant:write"))) -> TenantOut:
    t = ctx.db.get(Tenant, ctx.tenant_id)
    changes = body.model_dump(exclude_none=True)
    if "settings" in changes:
        unknown = set(changes["settings"]) - ALLOWED_SETTING_KEYS
        if unknown:
            raise Conflict(f"Unknown settings: {', '.join(sorted(unknown))}")
        merged = deep_merge(t.settings or {}, changes.pop("settings"))
        safety_changed = "safety" in (body.settings or {}) and "windows_h" in body.settings["safety"]
        if safety_changed:
            merged["safety"]["reviewed"] = False
            merged["safety"]["reviewed_by"] = None
        if (body.settings or {}).get("safety", {}).get("reviewed") is True:
            merged["safety"]["reviewed"] = True
            merged["safety"]["reviewed_by"] = ctx.principal.name
        t.settings = merged
    for k, v in changes.items():
        setattr(t, k, v)
    audit.record(ctx.db, ctx.principal, "tenant.updated", target_type="tenant", target_id=t.id,
                 meta={"fields": sorted(body.model_dump(exclude_none=True))})
    ctx.db.commit()
    return _tenant_out(t)


class CanteenReadiness(BaseModel):
    canteen_id: uuid.UUID
    canteen: str
    tier: int
    tier_name: str
    gives: str
    days: int
    next_tier_days: int | None
    progress: float
    message: str
    quality_score: float | None
    last_log: date | None
    critical_ratio: float
    champion: str | None


@router.get("/tenant/readiness", response_model=list[CanteenReadiness])
def get_readiness(ctx: Ctx = Depends(need("tenant:read"))) -> list[CanteenReadiness]:
    t = ctx.db.get(Tenant, ctx.tenant_id)
    settings = effective_settings(t.settings)
    out = []
    for c in ctx.db.execute(select(Canteen).where(Canteen.is_active.is_(True)).order_by(Canteen.name)).scalars():
        if ctx.principal.canteen_ids and c.id not in ctx.principal.canteen_ids:
            continue
        days, last = ctx.db.execute(
            select(func.count(func.distinct(ServiceLog.service_date)), func.max(ServiceLog.service_date))
            .where(ServiceLog.canteen_id == c.id)
        ).one()
        flagged, total = ctx.db.execute(
            select(func.count().filter(func.cardinality(ServiceLog.quality_flags) > 0), func.count())
            .where(ServiceLog.canteen_id == c.id, ServiceLog.service_date >= local_today(t.timezone) - timedelta(days=60))
        ).one()
        r = readiness(int(days or 0), settings["tiers"])
        champ = ctx.db.execute(
            select(ModelVersion.algorithm).where(ModelVersion.canteen_id == c.id, ModelVersion.stage.in_(["champion", "degraded"]))
        ).scalars().first()
        costs = {**settings["costs"], **(c.costs or {})}
        out.append(CanteenReadiness(
            canteen_id=c.id, canteen=c.name, tier=r.tier, tier_name=r.name, gives=TIER_GIVES[r.tier], days=r.days,
            next_tier_days=r.next_tier_days, progress=r.progress, message=r.message,
            quality_score=round(100 * (1 - (flagged or 0) / total), 1) if total else None, last_log=last,
            critical_ratio=round(critical_ratio(costs["shortage_to_waste_ratio"]), 3), champion=champ,
        ))
    return out


# --------------------------------------------------------------------------- audit and messages


class AuditRow(BaseModel):
    id: uuid.UUID
    at: datetime
    actor: str
    action: str
    target_type: str | None
    target_id: str | None
    meta: dict
    ip: str | None


@router.get("/audit", response_model=Page[AuditRow])
def list_audit(
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    action: str | None = None,
    ctx: Ctx = Depends(need("audit:read")),
) -> Page[AuditRow]:
    q = select(AuditLog).where(AuditLog.tenant_id == ctx.tenant_id).order_by(AuditLog.at.desc(), AuditLog.id)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    if cursor:
        q = q.where(AuditLog.at < datetime.fromisoformat(cursor))
    rows = ctx.db.execute(q.limit(limit + 1)).scalars().all()
    items = [AuditRow(id=r.id, at=r.at, actor=r.actor_label, action=r.action, target_type=r.target_type,
                      target_id=r.target_id, meta=r.meta, ip=r.ip) for r in rows[:limit]]
    return Page[AuditRow](items=items, next_cursor=rows[limit - 1].at.isoformat() if len(rows) > limit else None)


class MessageRow(BaseModel):
    id: uuid.UUID
    created_at: datetime
    channel: str
    to_address: str
    recipient: str | None
    subject: str | None
    body: str
    link: str | None
    status: str
    related_type: str | None
    related_id: str | None


@router.get("/outbox", response_model=list[MessageRow])
def list_outbox(limit: int = Query(60, ge=1, le=300), related_id: str | None = None,
                ctx: Ctx = Depends(need("outbox:read"))) -> list[MessageRow]:
    q = select(OutboxMessage).order_by(OutboxMessage.created_at.desc()).limit(limit)
    if related_id:
        q = q.where(OutboxMessage.related_id == related_id)
    return [MessageRow(id=m.id, created_at=m.created_at, channel=m.channel, to_address=m.to_address,
                       recipient=m.recipient_label, subject=m.subject, body=m.body, link=m.link, status=m.status,
                       related_type=m.related_type, related_id=m.related_id)
            for m in ctx.db.execute(q).scalars()]

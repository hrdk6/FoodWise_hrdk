"""Webhook subscriptions for mess-software integration."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from foodwise.api.common import Ok
from foodwise.core import audit
from foodwise.core.deps import Ctx, need
from foodwise.core.errors import NotFound, Unprocessable
from foodwise.db.models import Webhook, WebhookDelivery
from foodwise.services.webhooks import EVENTS, emit

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookOut(BaseModel):
    id: uuid.UUID
    url: str
    events: list[str]
    is_active: bool
    created_at: datetime
    recent: list[dict]


class WebhookIn(BaseModel):
    url: str = Field(max_length=500)
    events: list[str] = []

    @field_validator("url")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("Webhook URLs must use https.")
        return v

    @field_validator("events")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        unknown = set(v) - set(EVENTS)
        if unknown:
            raise ValueError(f"Unknown events: {', '.join(sorted(unknown))}")
        return v


class CreatedWebhook(WebhookOut):
    secret: str


def _out(db, h: Webhook) -> dict:
    recent = db.execute(select(WebhookDelivery).where(WebhookDelivery.webhook_id == h.id)
                        .order_by(WebhookDelivery.created_at.desc()).limit(5)).scalars()
    return dict(id=h.id, url=h.url, events=list(h.events or []), is_active=h.is_active, created_at=h.created_at,
                recent=[{"event": d.event, "status": d.status, "code": d.response_code, "at": d.created_at.isoformat()}
                        for d in recent])


@router.get("", response_model=list[WebhookOut])
def list_hooks(ctx: Ctx = Depends(need("webhooks:manage"))) -> list[WebhookOut]:
    return [WebhookOut(**_out(ctx.db, h)) for h in ctx.db.execute(select(Webhook).order_by(Webhook.created_at)).scalars()]


@router.post("", response_model=CreatedWebhook, status_code=201)
def create(body: WebhookIn, ctx: Ctx = Depends(need("webhooks:manage"))) -> CreatedWebhook:
    h = Webhook(tenant_id=ctx.tenant_id, url=body.url, events=body.events, secret=secrets.token_hex(24))
    ctx.db.add(h)
    ctx.db.flush()
    audit.record(ctx.db, ctx.principal, "webhook.created", target_type="webhook", target_id=h.id, meta={"url": body.url})
    ctx.db.commit()
    return CreatedWebhook(**_out(ctx.db, h), secret=h.secret)


@router.delete("/{hook_id}", response_model=Ok)
def delete(hook_id: uuid.UUID, ctx: Ctx = Depends(need("webhooks:manage"))) -> Ok:
    h = ctx.db.get(Webhook, hook_id)
    if h is None:
        raise NotFound("Webhook not found.")
    ctx.db.delete(h)
    audit.record(ctx.db, ctx.principal, "webhook.deleted", target_type="webhook", target_id=hook_id)
    ctx.db.commit()
    return Ok()


@router.post("/{hook_id}/test", response_model=Ok)
def test(hook_id: uuid.UUID, ctx: Ctx = Depends(need("webhooks:manage"))) -> Ok:
    h = ctx.db.get(Webhook, hook_id)
    if h is None:
        raise NotFound("Webhook not found.")
    if not h.is_active:
        raise Unprocessable("Turn the webhook on before testing it.")
    emit(ctx.db, ctx.tenant_id, h.events[0] if h.events else "forecast.generated", {"test": True})
    ctx.db.commit()
    return Ok(message="Test delivery queued.")

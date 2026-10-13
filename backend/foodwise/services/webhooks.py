"""Outgoing webhooks for mess-software integration, signed with HMAC-SHA256.

Receivers verify `X-FoodWise-Signature: sha256=<hex>` over the raw body using their webhook secret.
Deliveries run as jobs, so retries with backoff and the dead-letter queue come for free.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from foodwise.core import jobs, security
from foodwise.db.models import Webhook, WebhookDelivery

EVENTS = ("forecast.generated", "forecast.approved", "incident.created", "incident.claimed", "incident.closed",
          "model.promoted")


def emit(db: Session, tenant_id: uuid.UUID, event: str, data: dict) -> int:
    hooks = db.execute(select(Webhook).where(Webhook.tenant_id == tenant_id, Webhook.is_active.is_(True))).scalars().all()
    n = 0
    for hook in hooks:
        if hook.events and event not in hook.events:
            continue
        payload = {"id": str(uuid.uuid4()), "event": event, "created_at": datetime.now(UTC).isoformat(), "data": data}
        delivery = WebhookDelivery(tenant_id=tenant_id, webhook_id=hook.id, event=event, payload=payload)
        db.add(delivery)
        db.flush()
        jobs.enqueue(db, "deliver_webhook", {"delivery_id": str(delivery.id)}, tenant_id=tenant_id, max_attempts=6)
        n += 1
    return n


def deliver(db: Session, delivery: WebhookDelivery) -> int:
    hook = db.get(Webhook, delivery.webhook_id)
    body = json.dumps(delivery.payload, separators=(",", ":"), default=str).encode()
    delivery.attempts += 1
    try:
        resp = httpx.post(
            hook.url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "FoodWise-Webhooks/1",
                "X-FoodWise-Event": delivery.event,
                "X-FoodWise-Delivery": str(delivery.id),
                "X-FoodWise-Signature": security.hmac_signature(hook.secret, body),
            },
            timeout=8.0,
        )
        delivery.response_code = resp.status_code
        delivery.status = "delivered" if resp.is_success else "failed"
    except httpx.HTTPError as exc:
        delivery.status = "failed"
        delivery.response_code = None
        raise RuntimeError(f"webhook delivery failed: {exc}") from exc
    if delivery.status != "delivered":
        raise RuntimeError(f"webhook receiver answered {delivery.response_code}")
    return delivery.response_code or 0

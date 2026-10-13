"""Append-only audit trail (the table rejects UPDATE and DELETE at the database level)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from foodwise.core.security import Principal
from foodwise.db.models import AuditLog


def record(
    db: Session,
    actor: Principal | None,
    action: str,
    *,
    tenant_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: Any = None,
    meta: dict | None = None,
    ip: str | None = None,
    actor_label: str | None = None,
) -> None:
    db.add(
        AuditLog(
            tenant_id=tenant_id or (actor.tenant_id if actor else None),
            actor_id=actor.user_id if actor else None,
            actor_label=actor_label or (actor.label if actor else "system"),
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            meta=meta or {},
            ip=ip,
        )
    )

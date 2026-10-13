"""Request context: who is calling, what they may do, and a database session scoped to their tenant.

`tenant_id` always comes from the verified access token, never from the URL or body.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from foodwise.core.errors import Forbidden, NotFound, Unauthorized
from foodwise.core.logging import tenant_id_var
from foodwise.core.rbac import has_permission
from foodwise.core.security import Principal, decode_access_token
from foodwise.db.base import session_factory
from foodwise.db.models import Canteen

_bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def current_principal(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    if creds is None:
        raise Unauthorized("Sign in to continue.")
    try:
        principal = decode_access_token(creds.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Your session expired. Sign in again.", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("Your session is not valid. Sign in again.") from exc
    if principal.tenant_id:
        tenant_id_var.set(str(principal.tenant_id))
    return principal


@dataclass
class Ctx:
    principal: Principal
    db: Session
    ip: str

    @property
    def tenant_id(self) -> uuid.UUID:
        if self.principal.tenant_id is None:
            raise Forbidden("This action needs an organisation account.")
        return self.principal.tenant_id

    def can(self, permission: str) -> bool:
        return has_permission(self.principal.role, permission)

    def check_canteen(self, canteen_id: uuid.UUID) -> None:
        """404 (not 403, which would confirm it exists) unless the canteen belongs to the caller's
        college and, for staff assigned to specific canteens, is one of theirs."""
        allowed = self.principal.canteen_ids
        if allowed and canteen_id not in allowed:
            raise NotFound("Canteen not found.")
        if self.db.get(Canteen, canteen_id) is None:  # row-level security hides other tenants' canteens
            raise NotFound("Canteen not found.")


def need(permission: str) -> Callable[..., Iterator[Ctx]]:
    def _dep(request: Request, principal: Principal = Depends(current_principal)) -> Iterator[Ctx]:
        if not has_permission(principal.role, permission):
            raise Forbidden("This account cannot do that.", details={"permission": permission})
        with session_factory()() as db:
            # Admins have no tenant, so RLS shows them no tenant rows at all. Operations
            # endpoints that legitimately read across tenants open a system session.
            db.info["tenant_id"] = str(principal.tenant_id) if principal.tenant_id else None
            yield Ctx(principal=principal, db=db, ip=client_ip(request))

    return _dep

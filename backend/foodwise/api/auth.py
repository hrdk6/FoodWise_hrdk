"""Sign-in with short-lived access tokens and rotating refresh tokens (httpOnly cookie).

There are two sign-in pages: one for organisations (the shared login a college or canteen
operator uses) and one for FoodWise admins. Each page accepts only its own kind of account.

A refresh token can be used once. Presenting an already-rotated token means it was stolen or
replayed, so the whole token family is revoked and the user must sign in again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from foodwise.config import get_settings
from foodwise.core import audit, security
from foodwise.core.deps import client_ip, current_principal
from foodwise.core.errors import NotFound, Unauthorized, Unprocessable
from foodwise.core.ratelimit import limiter
from foodwise.core.rbac import PORTAL, permissions_for
from foodwise.db.base import system_session, tenant_session
from foodwise.db.models import Canteen, RefreshToken, Tenant, User

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE = "fw_refresh"
CSRF_HEADER = "x-foodwise-csrf"


Portal = Literal["organization", "admin"]


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)
    portal: Portal = "organization"


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    tenant_id: uuid.UUID | None
    language: str


class MealBrief(BaseModel):
    key: str
    label: str
    start: str
    end: str


class CanteenBrief(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    meals: list[MealBrief]


class TenantBrief(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    timezone: str
    currency: str
    is_demo: bool


class MeOut(BaseModel):
    user: UserOut
    permissions: list[str]
    tenant: TenantBrief | None
    canteens: list[CanteenBrief]


class TokenOut(BaseModel):
    access_token: str
    expires_in: int
    me: MeOut


class DemoAccount(BaseModel):
    email: str
    name: str
    role: str
    portal: Portal
    organisation: str | None
    detail: str


def _principal(u: User) -> security.Principal:
    return security.Principal(
        user_id=u.id, tenant_id=u.tenant_id, role=u.role, name=u.name, email=u.email,
        canteen_ids=tuple(u.canteen_ids or ()),
    )


def build_me(u: User) -> MeOut:
    tenant = canteens = None
    if u.tenant_id:
        with tenant_session(u.tenant_id) as db:
            t = db.get(Tenant, u.tenant_id)
            tenant = TenantBrief(id=t.id, name=t.name, slug=t.slug, timezone=t.timezone, currency=t.currency, is_demo=t.is_demo)
            q = select(Canteen).where(Canteen.is_active.is_(True)).order_by(Canteen.name)
            if u.canteen_ids:
                q = q.where(Canteen.id.in_(u.canteen_ids))
            canteens = [CanteenBrief(id=c.id, name=c.name, kind=c.kind, meals=c.meals) for c in db.execute(q).scalars()]
    return MeOut(
        user=UserOut(id=u.id, email=u.email, name=u.name, role=u.role, tenant_id=u.tenant_id, language=u.language),
        permissions=permissions_for(u.role), tenant=tenant, canteens=canteens or [],
    )


def _issue(response: Response, db, u: User, family: uuid.UUID | None,
           user_agent: str | None) -> tuple[TokenOut, RefreshToken]:
    settings = get_settings()
    raw = security.new_opaque_token()
    rt = RefreshToken(user_id=u.id, family_id=family or uuid.uuid4(), token_hash=security.hash_token(raw),
                      expires_at=security.refresh_expiry(), user_agent=(user_agent or "")[:300])
    db.add(rt)
    db.flush()
    response.set_cookie(
        COOKIE, raw, httponly=True, secure=settings.env == "production", samesite="lax",
        max_age=settings.refresh_token_days * 86400, path="/api/v1/auth",
    )
    token, ttl = security.create_access_token(_principal(u))
    return TokenOut(access_token=token, expires_in=ttl, me=build_me(u)), rt


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, response: Response) -> TokenOut:
    settings = get_settings()
    ip = client_ip(request)
    limiter.hit(f"login-ip:{ip}", limit=20, window_s=60)
    email = body.email.strip().lower()
    limiter.hit(f"login-email:{email}", limit=10, window_s=300)
    with system_session() as db:
        u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        now = datetime.now(UTC)
        if u is not None and u.locked_until and u.locked_until > now:
            raise Unauthorized("Too many failed attempts. Try again in a few minutes.", code="locked")
        if u is None or not u.is_active or not security.verify_password(u.password_hash, body.password):
            if u is not None:
                u.failed_logins += 1
                if u.failed_logins >= settings.login_max_failures:
                    u.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
                    u.failed_logins = 0
                audit.record(db, None, "auth.login_failed", tenant_id=u.tenant_id, target_type="user", target_id=u.id,
                             ip=ip, actor_label=email)
                db.commit()
            raise Unauthorized("That email and password do not match.", code="bad_credentials")
        u.failed_logins = 0
        if PORTAL.get(u.role) != body.portal:
            db.commit()
            other = "the admin sign-in" if PORTAL.get(u.role) == "admin" else "the organisation sign-in"
            raise Unauthorized(f"This account signs in on {other}.", code="wrong_portal")
        u.last_login_at = now
        out, _ = _issue(response, db, u, None, request.headers.get("user-agent"))
        audit.record(db, _principal(u), "auth.login", target_type="user", target_id=u.id, ip=ip)
        db.commit()
        return out


def _require_csrf(request: Request) -> None:
    if request.headers.get(CSRF_HEADER) != "1":
        raise Unauthorized("Missing request header.", code="csrf")


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response) -> TokenOut:
    _require_csrf(request)
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise Unauthorized("Sign in to continue.", code="no_session")
    with system_session() as db:
        rt = db.execute(select(RefreshToken).where(RefreshToken.token_hash == security.hash_token(raw))).scalar_one_or_none()
        now = datetime.now(UTC)
        if rt is None or rt.expires_at < now:
            raise Unauthorized("Your session expired. Sign in again.", code="no_session")
        if rt.revoked_at is not None:
            # Reuse of a rotated token: revoke the whole family.
            db.execute(update(RefreshToken).where(RefreshToken.family_id == rt.family_id, RefreshToken.revoked_at.is_(None))
                       .values(revoked_at=now))
            u = db.get(User, rt.user_id)
            audit.record(db, None, "auth.refresh_reuse_detected", tenant_id=u.tenant_id if u else None,
                         target_type="user", target_id=rt.user_id, ip=client_ip(request), actor_label="system")
            db.commit()
            response.delete_cookie(COOKIE, path="/api/v1/auth")
            raise Unauthorized("This session was used elsewhere and has been closed. Sign in again.", code="token_reuse")
        u = db.get(User, rt.user_id)
        if u is None or not u.is_active:
            raise Unauthorized("Sign in to continue.", code="no_session")
        out, new = _issue(response, db, u, rt.family_id, request.headers.get("user-agent"))
        rt.revoked_at = now
        rt.replaced_by = new.id
        db.commit()
        return out


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> Response:
    _require_csrf(request)
    raw = request.cookies.get(COOKIE)
    if raw:
        with system_session() as db:
            rt = db.execute(select(RefreshToken).where(RefreshToken.token_hash == security.hash_token(raw))).scalar_one_or_none()
            if rt is not None:
                db.execute(update(RefreshToken).where(RefreshToken.family_id == rt.family_id, RefreshToken.revoked_at.is_(None))
                           .values(revoked_at=datetime.now(UTC)))
                db.commit()
    response.delete_cookie(COOKIE, path="/api/v1/auth")
    response.status_code = 204
    return response


@router.get("/me", response_model=MeOut)
def me(principal: security.Principal = Depends(current_principal)) -> MeOut:
    with system_session() as db:
        u = db.get(User, principal.user_id)
        if u is None or not u.is_active:
            raise Unauthorized("Sign in to continue.")
        db.expunge(u)
    return build_me(u)


class PasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


@router.post("/password", status_code=204)
def change_password(body: PasswordIn, request: Request, response: Response,
                    principal: security.Principal = Depends(current_principal)) -> Response:
    """Change this account's password. Other devices are signed out; this one stays signed in."""
    _require_csrf(request)
    if body.new_password == body.current_password:
        raise Unprocessable("Choose a password different from the current one.")
    with system_session() as db:
        u = db.get(User, principal.user_id)
        if u is None or not security.verify_password(u.password_hash, body.current_password):
            raise Unauthorized("The current password is not right.", code="bad_credentials")
        u.password_hash = security.hash_password(body.new_password)
        raw = request.cookies.get(COOKIE)
        keep = None
        if raw:
            rt = db.execute(select(RefreshToken).where(RefreshToken.token_hash == security.hash_token(raw))).scalar_one_or_none()
            keep = rt.family_id if rt else None
        q = update(RefreshToken).where(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None))
        if keep is not None:
            q = q.where(RefreshToken.family_id != keep)
        db.execute(q.values(revoked_at=datetime.now(UTC)))
        audit.record(db, principal, "auth.password_changed", target_type="user", target_id=u.id, ip=client_ip(request))
        db.commit()
    response.status_code = 204
    return response


# --------------------------------------------------------------------------- development only


def _dev_only() -> None:
    if not get_settings().is_dev:
        raise NotFound("Not found.")


@router.get("/demo-accounts", response_model=list[DemoAccount], dependencies=[Depends(_dev_only)])
def demo_accounts() -> list[DemoAccount]:
    with system_session() as db:
        rows = db.execute(
            select(User, Tenant.name).outerjoin(Tenant, Tenant.id == User.tenant_id).where(User.is_seed.is_(True))
            .order_by(Tenant.created_at.nullslast(), User.role)
        ).all()
        return [DemoAccount(email=u.email, name=u.name, role=u.role, portal=PORTAL.get(u.role, "organization"),
                            organisation=t, detail=_demo_detail(u, t)) for u, t in rows]


def _demo_detail(u: User, tenant_name: str | None) -> str:
    if u.role == "admin":
        return "Model health, API speed, jobs, database and sign-ins across every organisation"
    if tenant_name and "Riverside" in tenant_name:
        return "Signed up ten days ago: rule-based plans while history builds up"
    return "Eight months of history; kitchens cooked to FoodWise plans for the last three weeks"


class DemoLoginIn(BaseModel):
    email: str
    portal: Portal = "organization"


@router.post("/demo-login", response_model=TokenOut, dependencies=[Depends(_dev_only)])
def demo_login(body: DemoLoginIn, request: Request, response: Response) -> TokenOut:
    """Development convenience: sign in as a seeded demo account without typing its password."""
    with system_session() as db:
        u = db.execute(select(User).where(User.email == body.email, User.is_seed.is_(True))).scalar_one_or_none()
        if u is None:
            raise NotFound("No demo account with that email.")
        if PORTAL.get(u.role) != body.portal:
            raise Unauthorized("This account signs in on the other sign-in page.", code="wrong_portal")
        u.last_login_at = datetime.now(UTC)
        out, _ = _issue(response, db, u, None, request.headers.get("user-agent"))
        audit.record(db, _principal(u), "auth.demo_login", target_type="user", target_id=u.id, ip=client_ip(request))
        db.commit()
        return out

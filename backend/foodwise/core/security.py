"""Passwords (argon2id), access tokens (JWT), refresh tokens, and signed public links."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from foodwise.config import get_settings

_hasher = PasswordHasher()  # argon2id with library defaults (RFC 9106 low-memory profile)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# --------------------------------------------------------------------------- access tokens


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None
    role: str
    name: str
    email: str
    canteen_ids: tuple[uuid.UUID, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.name} <{self.email}>"


def create_access_token(p: Principal) -> tuple[str, int]:
    settings = get_settings()
    ttl = settings.access_token_minutes * 60
    now = int(time.time())
    claims = {
        "sub": str(p.user_id),
        "tid": str(p.tenant_id) if p.tenant_id else None,
        "role": p.role,
        "name": p.name,
        "email": p.email,
        "cids": [str(c) for c in p.canteen_ids],
        "iat": now,
        "exp": now + ttl,
        "typ": "access",
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256"), ttl


def decode_access_token(token: str) -> Principal:
    claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    if claims.get("typ") != "access":
        raise jwt.InvalidTokenError("not an access token")
    return Principal(
        user_id=uuid.UUID(claims["sub"]),
        tenant_id=uuid.UUID(claims["tid"]) if claims.get("tid") else None,
        role=claims["role"],
        name=claims.get("name", ""),
        email=claims.get("email", ""),
        canteen_ids=tuple(uuid.UUID(c) for c in claims.get("cids", [])),
    )


# --------------------------------------------------------------------------- refresh tokens


def new_opaque_token() -> str:
    return secrets.token_urlsafe(40)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def refresh_expiry() -> datetime:
    return datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days)


# --------------------------------------------------------------------------- signed links


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class LinkError(ValueError):
    pass


def sign_link(purpose: str, payload: dict, ttl_seconds: int) -> str:
    """Compact signed token: base64(json).base64(hmac). The purpose is bound into the MAC
    so a QR feedback token can never be replayed for any other purpose."""
    body = dict(payload, _p=purpose, _exp=int(time.time()) + ttl_seconds)
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    mac = hmac.new(get_settings().link_secret.encode(), purpose.encode() + b"." + raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(mac[:24])}"


def verify_link(purpose: str, token: str) -> dict:
    try:
        raw_b64, mac_b64 = token.split(".", 1)
        raw = _unb64(raw_b64)
        mac = _unb64(mac_b64)
    except ValueError as exc:
        raise LinkError("malformed link") from exc
    expected = hmac.new(get_settings().link_secret.encode(), purpose.encode() + b"." + raw, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected[:24]):
        raise LinkError("invalid link signature")
    body = json.loads(raw)
    if body.get("_p") != purpose:
        raise LinkError("wrong link purpose")
    if int(body.get("_exp", 0)) < time.time():
        raise LinkError("link expired")
    return body


def hash_device(tenant_id: uuid.UUID | str, device_id: str) -> str:
    """Per-tenant salted device hash, so device IDs cannot be correlated across colleges."""
    key = (get_settings().link_secret + str(tenant_id)).encode()
    return hmac.new(key, device_id.encode(), hashlib.sha256).hexdigest()[:48]


def hmac_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


"""Two kinds of account, checked in one place (`deps.need`).

* organization: the one shared login for a college or canteen operator. Managers, kitchen staff
  and anyone else who plans food use it; it can do everything inside its own organisation.
* admin: a FoodWise engineer. Sees how the platform and every organisation's models are
  performing (metadata and aggregates), never an organisation's records.
"""

from __future__ import annotations

ROLES = ("organization", "admin")

ORGANIZATION = {
    "tenant:read",
    "tenant:write",
    "forecast:read",
    "forecast:decide",
    "forecast:generate",
    "logs:read",
    "logs:write",
    "menu:write",
    "calendar:write",
    "imports:write",
    "models:read",
    "models:train",
    "models:promote",
    "feedback:read",
    "incident:read",
    "incident:create",
    "incident:manage",
    "places:read",
    "impact:read",
    "reports:export",
    "audit:read",
    "webhooks:manage",
    "outbox:read",
}

ADMIN = {"ops:read", "ops:manage"}

PERMISSIONS: dict[str, set[str]] = {"organization": ORGANIZATION, "admin": ADMIN}

# Which sign-in page each role uses. An organisation account cannot sign in on the admin page,
# and the other way round, so the two consoles stay separate.
PORTAL = {"organization": "organization", "admin": "admin"}


def has_permission(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, set())


def permissions_for(role: str) -> list[str]:
    return sorted(PERMISSIONS.get(role, set()))

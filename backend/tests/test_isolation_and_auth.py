"""Tenant isolation (at the API and at the database), the two kinds of account, and the sign-in lifecycle."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from tests.conftest import PASSWORD, login, make_admin, make_tenant

pytestmark = pytest.mark.db


@pytest.fixture
def two_tenants(client):
    a = make_tenant("alpha", history_days=3)
    b = make_tenant("beta", history_days=3)
    return a, b


def test_database_rls_hides_other_tenants_even_without_a_where_clause(two_tenants):
    from foodwise.db.base import session_factory, tenant_session
    from foodwise.db.models import Canteen, ServiceLog

    a, b = two_tenants
    with tenant_session(a["tenant_id"]) as db:
        canteens = db.execute(select(Canteen)).scalars().all()
        assert {c.id for c in canteens} == {a["canteen_id"]}
        assert db.execute(select(ServiceLog).where(ServiceLog.canteen_id == b["canteen_id"])).first() is None
        # Tenant context survives commits (it is re-applied at the start of every transaction).
        db.commit()
        assert {c.id for c in db.execute(select(Canteen)).scalars()} == {a["canteen_id"]}
    with session_factory()() as db:  # no tenant at all: fails closed
        assert db.execute(text("SELECT count(*) FROM canteens")).scalar() == 0


def test_database_rls_blocks_cross_tenant_writes(two_tenants):
    from sqlalchemy.exc import ProgrammingError

    from foodwise.db.base import tenant_session
    from foodwise.db.models import CalendarDay

    a, b = two_tenants
    with tenant_session(a["tenant_id"]) as db:
        db.add(CalendarDay(tenant_id=b["tenant_id"], day=__import__("datetime").date.today(), kind="event", label="x"))
        with pytest.raises(ProgrammingError):
            db.commit()


def test_audit_log_is_append_only(two_tenants):
    from sqlalchemy.exc import DBAPIError

    from foodwise.db.base import system_session

    with system_session() as db:
        db.execute(text("INSERT INTO audit_logs (id, actor_label, action, meta) VALUES (gen_random_uuid(), 'x', 'y', '{}')"))
        db.commit()
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text("UPDATE audit_logs SET action = 'tampered'"))
            db.commit()


CROSS_TENANT_GETS = [
    "/api/v1/forecasts/plan?canteen_id={c}&day=2026-10-13",
    "/api/v1/forecasts/accuracy?canteen_id={c}",
    "/api/v1/feedback/rsvp?canteen_id={c}",
    "/api/v1/feedback/comments?canteen_id={c}",
    "/api/v1/logs?canteen_id={c}&start=2026-10-01&end=2026-10-02",
    "/api/v1/live?canteen_id={c}",
    "/api/v1/weather?canteen_id={c}&start=2026-10-01&end=2026-10-02",
    "/api/v1/logs/sheet?canteen_id={c}",
    "/api/v1/models?canteen_id={c}",
    "/api/v1/feedback/summary?canteen_id={c}",
    "/api/v1/menus?canteen_id={c}&start=2026-10-01&end=2026-10-02",
]


@pytest.mark.parametrize("path", CROSS_TENANT_GETS)
def test_api_returns_404_for_another_tenants_canteen(client, two_tenants, path):
    a, b = two_tenants
    headers = login(client, a["emails"]["organization"])
    r = client.get(path.format(c=b["canteen_id"]), headers=headers)
    assert r.status_code == 404, r.text
    assert client.get(path.format(c=a["canteen_id"]), headers=headers).status_code == 200


def test_api_cannot_touch_another_tenants_records(client, two_tenants):
    from foodwise.db.base import system_session
    from foodwise.db.models import Dish

    a, b = two_tenants
    headers = login(client, a["emails"]["organization"])
    with system_session() as db:
        dish_b = db.execute(select(Dish.id).where(Dish.tenant_id == b["tenant_id"])).scalars().first()
    assert client.patch(f"/api/v1/dishes/{dish_b}", json={"name": "hijacked"}, headers=headers).status_code == 404
    assert client.patch(f"/api/v1/canteens/{b['canteen_id']}", json={"name": "x"}, headers=headers).status_code == 404
    names = [d["name"] for d in client.get("/api/v1/dishes", headers=headers).json()]
    assert "hijacked" not in names and len(names) == 4


def test_places_search_is_scoped_to_the_callers_canteens(client, two_tenants, fake_osm):
    a, b = two_tenants
    headers = login(client, a["emails"]["organization"])
    body = {"canteen_id": str(b["canteen_id"]), "lat": 18.52, "lon": 73.85}
    assert client.post("/api/v1/places/nearby", json=body, headers=headers).status_code == 404
    body["canteen_id"] = str(a["canteen_id"])
    assert client.post("/api/v1/places/nearby", json=body, headers=headers).status_code == 200


def test_organisation_account_can_do_everything_inside_its_organisation(client, two_tenants):
    a, _ = two_tenants
    headers = login(client, a["emails"]["organization"])
    assert client.patch("/api/v1/tenant", json={"name": "Alpha College of Engineering"}, headers=headers).status_code == 200
    assert client.get("/api/v1/audit", headers=headers).status_code == 200
    assert client.get("/api/v1/logs/sheet", params={"canteen_id": str(a["canteen_id"])}, headers=headers).status_code == 200
    # ...but none of the admin console.
    assert client.get("/api/v1/ops/overview", headers=headers).status_code == 403


def test_admin_sees_the_console_but_no_organisation_records(client, two_tenants):
    a, _ = two_tenants
    headers = login(client, make_admin(), portal="admin")
    overview = client.get("/api/v1/ops/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    assert overview.json()["organisations"] == 2
    models = client.get("/api/v1/ops/models", headers=headers).json()
    assert {r["canteen"] for r in models["rows"]} == {"Alpha Mess", "Beta Mess"}
    for path in ("/api/v1/ops/api", "/api/v1/ops/jobs", "/api/v1/ops/database", "/api/v1/ops/organisations",
                 "/api/v1/ops/security", "/api/v1/ops/web-vitals"):
        assert client.get(path, headers=headers).status_code == 200, path
    traffic = client.get("/api/v1/ops/api", headers=headers).json()
    assert any(r["route"] == "GET /api/v1/ops/overview" for r in traffic["routes"])  # full patterns, not ids
    assert traffic["summary"]["requests"] >= 2
    # The admin account has no tenant: organisation endpoints are refused, never empty-but-200.
    assert client.get("/api/v1/dishes", headers=headers).status_code == 403
    assert client.get("/api/v1/logs/sheet", params={"canteen_id": str(a["canteen_id"])}, headers=headers).status_code == 403


def test_each_account_signs_in_only_on_its_own_page(client, two_tenants):
    a, _ = two_tenants
    admin = make_admin()
    r = client.post("/api/v1/auth/login", json={"email": a["emails"]["organization"], "password": PASSWORD, "portal": "admin"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "wrong_portal"
    r = client.post("/api/v1/auth/login", json={"email": admin, "password": PASSWORD})
    assert r.status_code == 401 and r.json()["error"]["code"] == "wrong_portal"
    assert client.post("/api/v1/auth/login", json={"email": admin, "password": PASSWORD, "portal": "admin"}).status_code == 200


def test_changing_the_shared_password_signs_out_other_devices(client, two_tenants):
    from fastapi.testclient import TestClient

    a, _ = two_tenants
    email = a["emails"]["organization"]
    other = TestClient(client.app)
    assert other.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    headers = login(client, email)
    csrf = {**headers, "X-FoodWise-CSRF": "1"}
    r = client.post("/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": "a new long password"},
                    headers=csrf)
    assert r.status_code == 204, r.text
    assert other.post("/api/v1/auth/refresh", headers={"X-FoodWise-CSRF": "1"}).status_code == 401
    assert client.post("/api/v1/auth/refresh", headers={"X-FoodWise-CSRF": "1"}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "a new long password"}).status_code == 200


def test_login_refresh_rotation_and_reuse_detection(client, two_tenants):
    a, _ = two_tenants
    r = client.post("/api/v1/auth/login", json={"email": a["emails"]["organization"], "password": PASSWORD})
    assert r.status_code == 200
    first_cookie = client.cookies.get("fw_refresh")
    csrf = {"X-FoodWise-CSRF": "1"}
    r2 = client.post("/api/v1/auth/refresh", headers=csrf)
    assert r2.status_code == 200
    assert client.cookies.get("fw_refresh") != first_cookie
    # Replaying the rotated token revokes the whole family.
    client.cookies.set("fw_refresh", first_cookie, path="/api/v1/auth")
    r3 = client.post("/api/v1/auth/refresh", headers=csrf)
    assert r3.status_code == 401 and r3.json()["error"]["code"] == "token_reuse"
    r4 = client.post("/api/v1/auth/refresh", headers=csrf)
    assert r4.status_code == 401


def test_refresh_requires_the_csrf_header(client, two_tenants):
    a, _ = two_tenants
    client.post("/api/v1/auth/login", json={"email": a["emails"]["organization"], "password": PASSWORD})
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_lockout_after_repeated_failures(client, two_tenants):
    a, _ = two_tenants
    email = a["emails"]["organization"]
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"}).status_code == 401
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 401 and r.json()["error"]["code"] == "locked"


def test_demo_login_is_not_available_outside_development(client, two_tenants):
    assert client.get("/api/v1/auth/demo-accounts").status_code == 404


def test_expired_or_garbage_tokens_are_rejected(client, two_tenants):
    assert client.get("/api/v1/tenant", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.get("/api/v1/tenant").status_code == 401


def test_errors_use_one_envelope(client, two_tenants):
    a, _ = two_tenants
    headers = login(client, a["emails"]["organization"])
    r = client.get(f"/api/v1/models/{uuid.uuid4()}", headers=headers)
    assert r.status_code == 404
    assert set(r.json()["error"]) == {"code", "message", "details"}
    r = client.post("/api/v1/dishes", json={"name": ""}, headers=headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid"

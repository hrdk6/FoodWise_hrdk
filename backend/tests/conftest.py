"""Test fixtures.

Pure tests (domain rules, ML) need nothing. Tests marked `db` run against the `foodwise_test`
database created by infra/postgres/init.sql (`docker compose up -d db`), migrated once per run
and truncated between tests.
"""

from __future__ import annotations

import os
from datetime import date, time, timedelta

import pytest

os.environ.setdefault(
    "FOODWISE_DATABASE_URL",
    os.environ.get("FOODWISE_TEST_DATABASE_URL", "postgresql+psycopg://foodwise:foodwise@localhost:5433/foodwise_test"),
)
os.environ["FOODWISE_ENV"] = "test"
os.environ["FOODWISE_WEATHER_PROVIDER"] = "none"
os.environ["FOODWISE_IMAGES_PROVIDER"] = "none"
os.environ["FOODWISE_STORAGE_DIR"] = os.path.join(os.path.dirname(__file__), ".storage")

from foodwise.config import get_settings  # noqa: E402

get_settings.cache_clear()

TABLES = (
    "tenants, refresh_tokens, users, jobs, outbox_messages, audit_logs, campuses, canteens, dishes, "
    "menu_entries, calendar_days, weather_days, service_logs, live_sessions, import_jobs, model_versions, forecasts, "
    "feedback, votes, rsvps, surplus_incidents, incident_events, donations, webhooks, "
    "webhook_deliveries, place_cache, service_checks, worker_heartbeats, web_vitals"
)


def _db_available() -> bool:
    import psycopg

    url = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
    try:
        with psycopg.connect(url, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


@pytest.fixture(scope="session")
def migrated():
    if not _db_available():
        pytest.skip("PostgreSQL test database not available (run: docker compose up -d db)")
    from foodwise.cli import _migrate

    _migrate(get_settings().database_url)
    yield


@pytest.fixture
def db_clean(migrated):
    from sqlalchemy import text

    from foodwise.core.ratelimit import limiter
    from foodwise.db.base import system_session

    limiter.reset()
    with system_session() as db:
        db.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        db.commit()
    yield


@pytest.fixture
def client(db_clean):
    from fastapi.testclient import TestClient

    from foodwise.main import create_app

    with TestClient(create_app()) as c:
        yield c


PASSWORD = "correct horse battery staple"


def make_tenant(slug: str, *, lat: float = 18.52, lon: float = 73.85, history_days: int = 0) -> dict:
    """A small tenant: one canteen with lunch and dinner, four dishes, optional synthetic history."""
    import numpy as np

    from foodwise.core.security import hash_password
    from foodwise.db.base import system_session
    from foodwise.db.models import Campus, Canteen, Dish, MenuEntry, ServiceLog, Tenant, User

    with system_session() as db:
        t = Tenant(name=f"{slug.title()} College", slug=slug, settings={})
        db.add(t)
        db.flush()
        campus = Campus(tenant_id=t.id, name="Main", lat=lat, lon=lon)
        db.add(campus)
        db.flush()
        c = Canteen(tenant_id=t.id, campus_id=campus.id, name=f"{slug.title()} Mess", lat=lat, lon=lon,
                    cutoff_time=time(21, 30), expected_diners=400, pickup_point="Back gate",
                    meals=[{"key": "lunch", "label": "Lunch", "start": "12:30", "end": "14:30"},
                           {"key": "dinner", "label": "Dinner", "start": "19:30", "end": "21:30"}])
        db.add(c)
        db.flush()
        dishes = []
        for name, unit, course, cat, diet in [("Steamed rice", "kg", "rice", "cooked_rice", "veg"),
                                              ("Dal tadka", "litres", "dal", "dal", "veg"),
                                              ("Paneer curry", "plates", "curry", "curry_gravy", "veg"),
                                              ("Chapati", "pieces", "bread", "bread", "veg")]:
            d = Dish(tenant_id=t.id, name=name, unit=unit, course=course, food_category=cat, diet=diet,
                     portion_kg=1.0 if unit in ("kg", "litres") else 0.2 if unit == "plates" else 0.04, cost_per_unit=30)
            db.add(d)
            dishes.append(d)
        db.flush()
        users = {"organization": User(tenant_id=t.id, email=f"team@{slug}.test", name=f"{slug.title()} team",
                                      role="organization", password_hash=hash_password(PASSWORD))}
        db.add(users["organization"])
        rng = np.random.default_rng(len(slug))
        today = date.today()
        for offset in range(history_days, -2, -1):
            day = today - timedelta(days=offset)
            for meal in ("lunch", "dinner"):
                ff = int(300 * (0.75 if day.weekday() >= 5 else 1.0) * rng.uniform(0.95, 1.05))
                for d in dishes:
                    db.add(MenuEntry(tenant_id=t.id, canteen_id=c.id, service_date=day, meal=meal, dish_id=d.id))
                    if offset > 0:
                        take = {"kg": 0.17, "litres": 0.15, "plates": 0.8, "pieces": 2.5}[d.unit]
                        served = round(ff * take * rng.uniform(0.95, 1.05), 1)
                        prepared = round(served * 1.15, 1)
                        db.add(ServiceLog(tenant_id=t.id, canteen_id=c.id, service_date=day, meal=meal, dish_id=d.id,
                                          unit=d.unit, prepared_qty=prepared, served_qty=served,
                                          unserved_qty=round(prepared - served, 1), footfall=ff, source="import"))
        db.commit()
        return {"tenant_id": t.id, "canteen_id": c.id, "dish_ids": [d.id for d in dishes],
                "emails": {r: u.email for r, u in users.items()}}


def make_admin(email: str = "admin@foodwise.test") -> str:
    from foodwise.core.security import hash_password
    from foodwise.db.base import system_session
    from foodwise.db.models import User

    with system_session() as db:
        db.add(User(tenant_id=None, email=email, name="FoodWise engineering", role="admin",
                    password_hash=hash_password(PASSWORD)))
        db.commit()
    return email


def osm_elements(lat: float = 18.52, lon: float = 73.85) -> list[dict]:
    """What Overpass returns around the test canteen: four places that feed people (one far away,
    one without a phone), a gaushala, and two things that must be ignored."""
    return [
        {"type": "node", "id": 1, "lat": lat + 0.004, "lon": lon + 0.003,
         "tags": {"amenity": "social_facility", "social_facility": "soup_kitchen", "name": "Near Kitchen",
                  "phone": "+91 90000 00001", "opening_hours": "24/7"}},
        {"type": "way", "id": 2, "center": {"lat": lat + 0.008, "lon": lon + 0.006},
         "tags": {"amenity": "social_facility", "social_facility": "shelter", "social_facility:for": "homeless",
                  "name": "Shelter Two", "phone": "+91 90000 00002"}},
        {"type": "node", "id": 3, "lat": lat + 0.012, "lon": lon + 0.009,
         "tags": {"amenity": "orphanage", "name": "Little Stars Home"}},
        {"type": "node", "id": 4, "lat": lat + 0.03, "lon": lon + 0.02,
         "tags": {"amenity": "place_of_worship", "religion": "sikh", "name": "Gurdwara Sahib", "phone": "+91 90000 00004"}},
        {"type": "node", "id": 5, "lat": lat + 0.01, "lon": lon - 0.01,
         "tags": {"amenity": "animal_shelter", "name": "Gau Seva Goshala", "phone": "+91 90000 00005"}},
        {"type": "node", "id": 6, "lat": lat + 0.002, "lon": lon + 0.002, "tags": {"amenity": "restaurant", "name": "Cafe"}},
        {"type": "node", "id": 7, "lat": lat + 0.002, "lon": lon + 0.001, "tags": {"amenity": "social_facility"}},
    ]


@pytest.fixture
def fake_osm(monkeypatch):
    """Replace the Overpass call; records how often it was asked."""
    calls = []

    def fake(query: str) -> list[dict]:
        calls.append(query)
        return osm_elements()

    from foodwise.services import places

    monkeypatch.setattr(places, "overpass", fake)
    return calls


def login(client, email: str, password: str = PASSWORD, portal: str = "organization") -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password, "portal": portal})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}

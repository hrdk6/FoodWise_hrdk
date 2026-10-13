"""End-to-end flows against a real database: training and planning, surplus rescue to nearby places
(with concurrent donations), dish pictures, public feedback, the import pipeline, and the job queue."""

from __future__ import annotations

import io
import threading
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.conftest import login, make_admin, make_tenant

pytestmark = pytest.mark.db


def test_cold_start_tier0_then_train_and_plan(client):
    t = make_tenant("gamma", history_days=70)
    headers = login(client, t["emails"]["organization"])
    readiness = client.get("/api/v1/tenant/readiness", headers=headers).json()
    assert readiness[0]["tier"] == 2

    from foodwise.ml.training import train_canteen

    result = train_canteen(t["tenant_id"], t["canteen_id"], trigger="test")
    assert result["stage"] == "champion"
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    plan = client.post("/api/v1/forecasts/generate", json={"canteen_id": str(t["canteen_id"]), "date": tomorrow},
                       headers=headers)
    assert plan.status_code == 200, plan.text
    body = plan.json()
    dishes = [d for m in body["meals"] for d in m["dishes"]]
    assert len(dishes) == 8
    for d in dishes:
        assert d["p10"] <= d["p50"] <= d["p90"]
        assert d["recommended"] >= 0
        assert d["quantiles"] == sorted(d["quantiles"])

    one = dishes[0]
    r = client.patch(f"/api/v1/forecasts/{one['id']}", json={"action": "override", "qty": one["recommended"] + 5},
                     headers=headers)
    assert r.status_code == 422  # a reason is required
    r = client.patch(f"/api/v1/forecasts/{one['id']}",
                     json={"action": "override", "qty": one["recommended"] + 5, "reason": "Guest lecture"}, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "overridden"
    approved = client.post("/api/v1/forecasts/approve", json={"canteen_id": str(t["canteen_id"]), "date": tomorrow},
                           headers=headers).json()
    assert approved["counts"]["proposed"] == 0

    whatif = client.post("/api/v1/forecasts/whatif", json={"canteen_id": str(t["canteen_id"]), "date": tomorrow,
                                                           "headcount_pct": -0.3}, headers=headers)
    assert whatif.status_code == 200
    w = whatif.json()
    assert w["totals"]["scenario_kg"] < w["totals"]["base_kg"]


def test_tier0_tenant_gets_a_labelled_rule_based_plan(client):
    t = make_tenant("delta", history_days=5)
    headers = login(client, t["emails"]["organization"])
    from foodwise.ml.training import train_canteen

    assert train_canteen(t["tenant_id"], t["canteen_id"])["algorithm"] == "rule_prior"
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    body = client.post("/api/v1/forecasts/generate", json={"canteen_id": str(t["canteen_id"]), "date": tomorrow},
                       headers=headers).json()
    assert body["model"]["kind"] == "prior"


def _incident(client, t, headers, qty=150, cooked_hours_ago=1.0):
    cooked = (datetime.now(UTC) - timedelta(hours=cooked_hours_ago)).isoformat()
    return client.post("/api/v1/incidents", headers=headers, json={
        "canteen_id": str(t["canteen_id"]), "trigger": "manual",
        "items": [{"dish_id": str(t["dish_ids"][2]), "qty": qty, "storage": "hot_held", "cooked_at": cooked}],
    })


def _place(p: dict) -> dict:
    return {k: p[k] for k in ("ref", "name", "kind", "lat", "lon", "phone", "address", "distance_km")}


def test_surplus_rescue_to_nearby_places_end_to_end(client, fake_osm):
    t = make_tenant("epsilon", history_days=10)
    headers = login(client, t["emails"]["organization"])
    inc = _incident(client, t, headers).json()
    assert inc["status"] == "open" and inc["route"] == "people"
    assert "150 portions" in inc["share_text"] and "safe to eat until" in inc["share_text"]

    # Places come from the device's location; the canteen's location is the fallback.
    body = {"canteen_id": str(t["canteen_id"]), "lat": 18.521, "lon": 73.851, "incident_id": inc["id"]}
    near = client.post("/api/v1/places/nearby", json=body, headers=headers)
    assert near.status_code == 200, near.text
    near = near.json()
    assert near["origin"] == "device" and "OpenStreetMap" in near["attribution"]
    names = [p["name"] for p in near["places"]]
    assert names[0] == "Near Kitchen"
    assert {"Shelter Two", "Little Stars Home", "Gurdwara Sahib"} <= set(names)
    assert "Cafe" not in names and "Gau Seva Goshala" not in names  # not a food place / not for people
    stars = next(p for p in near["places"] if p["name"] == "Little Stars Home")
    assert stars["kind"] == "children_home" and any("No phone" in n for n in stars["notes"])
    gurdwara = next(p for p in near["places"] if p["name"] == "Gurdwara Sahib")
    assert gurdwara["kind"] == "community_kitchen" and "langar" in gurdwara["note"]
    # A second search in the same area is served from the cache.
    client.post("/api/v1/places/nearby", json={**body, "lat": 18.5212}, headers=headers)
    assert len(fake_osm) == 1

    # Two people record a donation at the same moment: the food cannot be given away twice.
    kitchen, shelter = near["places"][0], next(p for p in near["places"] if p["name"] == "Shelter Two")
    results = []

    def give():
        results.append(client.post(f"/api/v1/incidents/{inc['id']}/donations", headers=headers,
                                   json={"place": _place(kitchen), "portions": 100}).status_code)

    threads = [threading.Thread(target=give) for _ in range(2)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert sorted(results) == [201, 409]
    r = client.post(f"/api/v1/incidents/{inc['id']}/donations", headers=headers,
                    json={"place": _place(shelter), "portions": 50, "contact_method": "whatsapp"})
    assert r.status_code == 201 and r.json()["status"] == "arranged" and r.json()["remaining_portions"] == 0
    over = client.post(f"/api/v1/incidents/{inc['id']}/donations", headers=headers,
                       json={"place": {"name": "Somewhere Else", "kind": "ngo"}, "portions": 5})
    assert over.status_code == 409

    d_kitchen, d_shelter = r.json()["donations"]
    assert client.post(f"/api/v1/incidents/{inc['id']}/close", headers=headers, json={}).status_code == 409
    ok = client.post(f"/api/v1/incidents/{inc['id']}/donations/{d_kitchen['id']}/handed-over", headers=headers,
                     json={"weight_kg": 19.5})
    assert ok.status_code == 200 and ok.json()["status"] == "arranged"
    fell = client.post(f"/api/v1/incidents/{inc['id']}/donations/{d_shelter['id']}/fell-through", headers=headers,
                       json={"reason": "No vehicle free"})
    assert fell.status_code == 200 and fell.json()["remaining_portions"] == 50
    closed = client.post(f"/api/v1/incidents/{inc['id']}/close", headers=headers, json={"disposal": "compost"})
    assert closed.status_code == 200, closed.text
    impact = closed.json()["impact"]
    assert closed.json()["status"] == "closed" and impact["kg"] == 19.5 and impact["places"] == ["Near Kitchen"]
    assert impact["meals"] > 0 and impact["disposed_kg"] > 0

    # The place is remembered: it ranks with its history next time.
    again = client.post("/api/v1/places/nearby", json={"canteen_id": str(t["canteen_id"])}, headers=headers).json()
    assert again["origin"] == "canteen"
    top = next(p for p in again["places"] if p["name"] == "Near Kitchen")
    assert top["history"]["given"] == 1 and top["history"]["kg"] == 19.5
    shelter_again = next(p for p in again["places"] if p["name"] == "Shelter Two")
    assert shelter_again["history"]["fell_through"] == 1
    given = client.get("/api/v1/places/history", headers=headers).json()
    assert {g["name"] for g in given} == {"Near Kitchen", "Shelter Two"}


def test_small_surplus_is_logged_not_offered(client):
    t = make_tenant("zeta", history_days=10)
    headers = login(client, t["emails"]["organization"])
    r = _incident(client, t, headers, qty=5)
    assert r.status_code == 201 and r.json()["status"] == "rejected"


def test_unsafe_food_only_goes_to_animal_feed_or_compost(client, fake_osm):
    t = make_tenant("eta", history_days=10)
    headers = login(client, t["emails"]["organization"])
    inc = _incident(client, t, headers, qty=150, cooked_hours_ago=3.9).json()
    assert inc["route"] == "divert" and inc["status"] == "open"
    near = client.post("/api/v1/places/nearby", json={"canteen_id": str(t["canteen_id"]), "incident_id": inc["id"]},
                       headers=headers).json()
    assert [p["name"] for p in near["places"]] == ["Gau Seva Goshala"]
    people = {"ref": "osm:node/1", "name": "Near Kitchen", "kind": "community_kitchen"}
    r = client.post(f"/api/v1/incidents/{inc['id']}/donations", headers=headers, json={"place": people, "portions": 50})
    assert r.status_code == 422
    r = client.post(f"/api/v1/incidents/{inc['id']}/donations", headers=headers,
                    json={"place": _place(near["places"][0]), "portions": 150})
    assert r.status_code == 201


def test_safe_window_passing_switches_the_surplus_to_animal_feed(client):
    from foodwise.db.base import tenant_session
    from foodwise.db.models import SurplusIncident
    from foodwise.services import incidents

    t = make_tenant("theta", history_days=10)
    headers = login(client, t["emails"]["organization"])
    inc = _incident(client, t, headers).json()
    with tenant_session(t["tenant_id"]) as db:
        assert incidents.expire(db, uuid.UUID(inc["id"]), now=datetime.now(UTC) + timedelta(hours=6)) == "diverted"
        db.commit()
        row = db.get(SurplusIncident, uuid.UUID(inc["id"]))
        assert row.route == "divert" and row.status == "open"


def test_place_search_survives_openstreetmap_being_down(client, monkeypatch, fake_osm):
    from foodwise.services import places

    t = make_tenant("iota", history_days=3)
    headers = login(client, t["emails"]["organization"])
    body = {"canteen_id": str(t["canteen_id"])}
    assert client.post("/api/v1/places/nearby", json=body, headers=headers).json()["places"]

    def down(query):
        raise RuntimeError("overpass unavailable")

    monkeypatch.setattr(places, "overpass", down)
    wider = client.post("/api/v1/places/nearby", json={**body, "radius_km": 15}, headers=headers).json()
    assert wider["places"] == [] and "OpenStreetMap" in wider["error"]
    # The same area as before is still served from the cache when it goes stale.
    from foodwise.db.base import system_session
    from foodwise.db.models import PlaceCache

    with system_session() as db:
        for row in db.execute(select(PlaceCache)).scalars():
            row.fetched_at = datetime.now(UTC) - timedelta(days=10)
        db.commit()
    stale = client.post("/api/v1/places/nearby", json=body, headers=headers).json()
    assert stale["stale"] and stale["places"]


def test_session_links_cannot_be_forged(client):
    from foodwise.core import security

    assert client.get("/api/v1/public/session/not-a-real-token").status_code == 404
    forged = security.sign_link("offer", {"t": str(uuid.uuid4())}, 3600)  # wrong purpose
    assert client.get(f"/api/v1/public/session/{forged}").status_code == 404


def test_dish_picture_upload_is_cleaned_and_served_publicly(client):
    from PIL import Image

    t = make_tenant("kappa")
    headers = login(client, t["emails"]["organization"])
    img = Image.new("RGB", (900, 600), (200, 120, 40))
    exif = Image.Exif()
    exif[0x010E] = "secret kitchen notes"
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    dish = t["dish_ids"][2]
    r = client.put(f"/api/v1/dishes/{dish}/image/upload", headers=headers,
                   files={"file": ("paneer.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["image_status"] == "found" and out["image_source"] == "upload"
    served = client.get(out["image_url"])  # no sign-in needed
    assert served.status_code == 200 and served.headers["content-type"] == "image/jpeg"
    assert b"secret kitchen notes" not in served.content
    assert Image.open(io.BytesIO(served.content)).size == (480, 480)
    assert "immutable" in served.headers["cache-control"]
    bad = client.put(f"/api/v1/dishes/{dish}/image/upload", headers=headers,
                     files={"file": ("notes.txt", b"not a picture", "text/plain")})
    assert bad.status_code == 422
    gone = client.delete(f"/api/v1/dishes/{dish}/image", headers=headers).json()
    assert gone["image_url"] is None and gone["image_status"] == "removed"


def test_web_vitals_beacon_reaches_the_admin_console(client):
    samples = [{"metric": "LCP", "value": v, "rating": "good" if v < 2500 else "poor", "route": "/plan"}
               for v in (1200, 1800, 2100, 5200)]
    assert client.post("/api/v1/telemetry/vitals", json={"samples": samples}).status_code == 204
    assert client.post("/api/v1/telemetry/vitals", json={"samples": [{**samples[0], "route": "javascript:x"}]}).status_code == 422
    headers = login(client, make_admin(), portal="admin")
    vitals = client.get("/api/v1/ops/web-vitals", headers=headers).json()
    lcp = next(v for v in vitals["overall"] if v["metric"] == "LCP")
    assert lcp["samples"] == 4 and lcp["p75"] > 2100
    assert vitals["routes"][0]["route"] == "/plan"


def test_student_feedback_rsvp_and_vote(client):
    t = make_tenant("theta", history_days=2)
    headers = login(client, t["emails"]["organization"])
    today = date.today().isoformat()
    qr = client.get("/api/v1/feedback/qr", headers=headers,
                    params={"canteen_id": str(t["canteen_id"]), "day": today, "meal": "lunch"}).json()
    device = {"X-Device-ID": "device-0123456789"}
    session = client.get(f"/api/v1/public/session/{qr['token']}", headers=device)
    assert session.status_code == 200
    dish = session.json()["dishes"][0]["id"]
    body = {"token": qr["token"], "dish_id": dish, "rating": 1, "aspects": {"temperature": -1},
            "text": "dal was thanda, call 9876543210"}
    assert client.post("/api/v1/public/feedback", json=body, headers=device).status_code == 200
    body["rating"] = 5  # same device, same dish, same session: an update, not a second vote
    assert client.post("/api/v1/public/feedback", json=body, headers=device).status_code == 200

    from foodwise.db.base import system_session
    from foodwise.db.models import Feedback

    with system_session() as db:
        rows = db.execute(select(Feedback)).scalars().all()
    assert len(rows) == 1 and rows[0].rating == 5
    assert "9876543210" not in rows[0].text and rows[0].sentiment is not None

    other_dish = str(uuid.uuid4())
    assert client.post("/api/v1/public/feedback", headers=device,
                       json={**body, "dish_id": other_dish}).status_code == 422
    assert client.post("/api/v1/public/feedback", json=body).status_code == 422  # no device id


def test_import_pipeline_maps_validates_and_commits(client):
    t = make_tenant("iota", history_days=0)
    headers = login(client, t["emails"]["organization"])
    csv = (
        "Date,Mess,Meal,Item,Unit,Cooked,Served,Left over,Diners,Ran out\n"
        "2026-08-18,Iota Mess,Lunch,Steamed Rice,kg,40,36,4,300,false\n"
        "2026-08-18,Iota Mess,Lunch,Daal Tadka,litres,30,28,2,300,false\n"
        "2026-08-18,Iota Mess,Lunch,Gulab jamun,pieces,500,480,20,300,false\n"
        "2026-08-19,Iota Mess,Lunch,Steamed Rice,kg,40,45,0,300,true\n"
        "2026-08-19,Iota Mess,Brunch,Steamed Rice,kg,40,36,4,300,false\n"
    )
    up = client.post("/api/v1/imports", headers=headers, files={"file": ("history.csv", io.BytesIO(csv.encode()), "text/csv")})
    assert up.status_code == 201, up.text
    job = up.json()
    m = job["mapping"]
    assert m["date"] == "Date" and m["prepared_qty"] == "Cooked" and m["served_qty"] == "Served" and m["dish"] == "Item"
    v = client.post(f"/api/v1/imports/{job['id']}/validate", headers=headers, json={"mapping": m, "dish_matches": {}})
    assert v.status_code == 200, v.text
    report = v.json()["report"]
    codes = {i["code"] for i in report["issues"]}
    assert "prepared_lt_served" in codes and "unknown_meal" in codes
    statuses = {d["raw"]: d["status"] for d in report["dish_matches"]}
    assert statuses["Steamed Rice"] == "exact" and statuses["Daal Tadka"] in ("exact", "suggested")
    assert statuses["Gulab jamun"] == "new"
    c = client.post(f"/api/v1/imports/{job['id']}/commit", headers=headers, json={"create_new_dishes": True})
    assert c.status_code == 200 and c.json()["committed_rows"] == 3
    dishes = {d["name"] for d in client.get("/api/v1/dishes", headers=headers).json()}
    assert "Gulab jamun" in dishes


def test_job_queue_dedupes_retries_and_dead_letters(client):
    from foodwise.core import jobs
    from foodwise.db.base import system_session
    from foodwise.db.models import Job

    calls = {"n": 0}

    @jobs.handler("test_flaky")
    def flaky(job):
        calls["n"] += 1
        raise RuntimeError("boom")

    with system_session() as db:
        a = jobs.enqueue(db, "test_flaky", {}, dedupe_key="k1", max_attempts=2)
        b = jobs.enqueue(db, "test_flaky", {}, dedupe_key="k1", max_attempts=2)
        db.commit()
    assert a == b
    jobs.drain()
    with system_session() as db:
        j = db.get(Job, a)
        assert j.status == "queued" and j.attempts == 1  # rescheduled with backoff
        j.run_at = datetime.now(UTC)
        db.commit()
    jobs.drain()
    with system_session() as db:
        j = db.get(Job, a)
        assert j.status == "dead" and "boom" in j.last_error
    assert calls["n"] == 2

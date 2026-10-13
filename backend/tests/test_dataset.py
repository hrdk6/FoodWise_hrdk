"""The dataset browser: every table is readable, filters work, CSV matches, and it stays inside the college."""

from __future__ import annotations

import pytest

from tests.conftest import login, make_tenant

pytestmark = pytest.mark.db


@pytest.fixture
def world(client):
    return make_tenant("alpha", history_days=30), make_tenant("beta", history_days=3)


def test_summary_lists_every_table_with_counts(client, world):
    a, _ = world
    headers = login(client, a["emails"]["organization"])
    r = client.get("/api/v1/dataset/summary", params={"canteen_id": str(a["canteen_id"])}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    keys = {t["key"]: t for t in body["tables"]}
    assert set(keys) == {"logs", "calendar", "weather", "rsvp", "feedback", "features"}
    assert keys["logs"]["rows"] > 0 and keys["features"]["rows"] > 0
    assert body["feature_count"] > 20 and body["days"] >= 30


def test_rows_page_filter_and_export(client, world):
    a, _ = world
    headers = login(client, a["emails"]["organization"])
    params = {"canteen_id": str(a["canteen_id"]), "table": "logs", "page_size": 10}
    first = client.get("/api/v1/dataset/rows", params=params, headers=headers).json()
    assert len(first["rows"]) == 10 and first["total"] > 10
    assert [c["key"] for c in first["columns"]][:3] == ["date", "meal", "dish"]
    page2 = client.get("/api/v1/dataset/rows", params={**params, "page": 2}, headers=headers).json()
    assert page2["rows"] != first["rows"]
    meal = first["rows"][0][1]
    only = client.get("/api/v1/dataset/rows", params={**params, "meal": meal, "page_size": 200}, headers=headers).json()
    assert only["rows"] and all(r[1] == meal for r in only["rows"])
    feats = client.get("/api/v1/dataset/rows", params={**params, "table": "features"}, headers=headers).json()
    keys = [c["key"] for c in feats["columns"]]
    assert "demand" in keys and "rain_prob" in keys and "dish_lag7" in keys
    csv = client.get("/api/v1/dataset/export", params={"canteen_id": str(a["canteen_id"]), "table": "logs"}, headers=headers)
    assert csv.status_code == 200 and csv.text.splitlines()[0].startswith("date,meal,dish")
    assert len(csv.text.splitlines()) == first["total"] + 1


def test_dataset_is_scoped_to_the_callers_college(client, world):
    a, b = world
    headers = login(client, a["emails"]["organization"])
    assert client.get("/api/v1/dataset/summary", params={"canteen_id": str(b["canteen_id"])}, headers=headers).status_code == 404
    assert client.get("/api/v1/dataset/rows", params={"canteen_id": str(b["canteen_id"]), "table": "logs"}, headers=headers).status_code == 404

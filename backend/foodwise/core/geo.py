"""Distances and travel times.

The default ETA is an estimate (straight-line distance x road factor at urban speed plus a
fixed loading overhead), which needs no third-party service. Setting
FOODWISE_ROUTING_PROVIDER=osrm re-ranks the shortlist with real driving times and falls back
to the estimate if the router is slow or unavailable.
"""

from __future__ import annotations

import logging
import math

import httpx

from foodwise.config import get_settings

log = logging.getLogger("foodwise.geo")

ROAD_FACTOR = 1.35
URBAN_SPEED_KMH = 22.0
OVERHEAD_MIN = 6.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def estimate_eta_min(distance_km: float) -> float:
    return OVERHEAD_MIN + distance_km * ROAD_FACTOR / URBAN_SPEED_KMH * 60.0


def travel_minutes(origin: tuple[float, float], dests: list[tuple[float, float]]) -> list[float]:
    estimates = [estimate_eta_min(haversine_km(origin[0], origin[1], d[0], d[1])) for d in dests]
    settings = get_settings()
    if settings.routing_provider != "osrm" or not dests:
        return estimates
    coords = ";".join(f"{lon},{lat}" for lat, lon in [origin, *dests])
    try:
        resp = httpx.get(
            f"{settings.osrm_url}/table/v1/driving/{coords}",
            params={"sources": "0", "annotations": "duration"},
            timeout=3.0,
        )
        resp.raise_for_status()
        durations = resp.json()["durations"][0][1:]
        return [OVERHEAD_MIN + d / 60.0 if d is not None else e for d, e in zip(durations, estimates, strict=True)]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        log.warning("routing provider unavailable, using estimates: %s", exc)
        return estimates

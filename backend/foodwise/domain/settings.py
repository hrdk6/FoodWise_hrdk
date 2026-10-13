"""Tenant configuration defaults. Stored settings are merged over these, key by key."""

from __future__ import annotations

import copy
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "costs": {
        # Cost of running one unit short relative to the cost of wasting one unit.
        # 5 gives a critical ratio of 5/6, so the plan cooks at about the 83rd percentile.
        # A prepaid hostel mess that must not run out might use 9 (the 90th percentile).
        "shortage_to_waste_ratio": 5.0,
        "disposal_cost_per_kg": 4.0,
    },
    "thresholds": {
        # A surplus is "large" at or above the larger of these and the canteen's historical P95 waste.
        "surplus_min_portions": 20,
        "surplus_min_kg": 5.0,
        "use_p95_waste": True,
    },
    "tiers": {"tier1_days": 14, "tier2_days": 60, "tier3_days": 180},
    "safety": {
        "reviewed": False,
        "reviewed_by": None,
        "handling_buffer_min": 20,
        "min_useful_window_min": 45,
        # Hours from cooking until the food is no longer suitable for donation.
        # Defaults are conservative placeholders. A food-safety officer must review them against
        # local rules (in India, the FSSAI surplus food recovery and distribution regulations).
        "windows_h": {
            "cooked_rice": {"hot_held": 4, "chilled": 24, "ambient": 2},
            "curry_gravy": {"hot_held": 4, "chilled": 24, "ambient": 2},
            "dal": {"hot_held": 4, "chilled": 24, "ambient": 2},
            "bread": {"hot_held": 6, "chilled": 24, "ambient": 6},
            "fried_snack": {"hot_held": 4, "chilled": 12, "ambient": 4},
            "dessert_dairy": {"hot_held": 2, "chilled": 12, "ambient": 2},
            "salad_raw": {"hot_held": 0, "chilled": 6, "ambient": 2},
            "beverage": {"hot_held": 3, "chilled": 12, "ambient": 2},
        },
    },
    "places": {
        # Nearby places that feed people come from OpenStreetMap around your device (or the
        # canteen). The search starts at this radius; staff can widen it up to the maximum.
        "radius_km": 10,
        "max_radius_km": 25,
        # Do not suggest giving away fewer portions than this to one place.
        "min_useful_portions": 10,
    },
    "impact": {
        # Configurable. Replace with factors from the college's sustainability office.
        "co2e_kg_per_kg_food": 2.5,
        "co2e_source": "Placeholder average for cooked food; set your own factor.",
        "kg_per_meal": 0.4,
    },
    "feedback": {
        "half_life_days": 21,
        "pseudo_count": 5,
        "k_anonymity": 5,
        "text_retention_days": 90,
        "min_effective_n": 8,
    },
    "models": {
        "shadow_days": 7,
        "min_skill_vs_baseline": 0.03,
        "coverage_tolerance_pp": 5.0,
        "max_abs_bias": 0.05,
        "retrain_every_days": 7,
    },
    "rsvp": {"cutoff": "21:00"},
    "digest": {"email": True, "whatsapp": False},
}


def deep_merge(base: dict, override: dict | None) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def effective_settings(stored: dict | None) -> dict:
    return deep_merge(DEFAULT_SETTINGS, stored)

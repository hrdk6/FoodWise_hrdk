"""Food-safety gate for surplus donation.

Each item gets a safe-until time from its food category and storage state. Food past its window,
or with too little time left to be useful, is routed to compost, biogas or animal feed instead of
people. The windows are tenant configuration; this module makes no safety claim of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

STORAGE_STATES = ("hot_held", "chilled", "ambient")


@dataclass
class ItemSafety:
    index: int
    safe_until: datetime | None
    minutes_left: float
    route: str  # people | divert
    reason: str | None = None


@dataclass
class SafetyAssessment:
    route: str  # people | divert
    safe_until: datetime | None
    minutes_left: float
    items: list[ItemSafety] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def item_safe_until(category: str, storage: str, cooked_at: datetime, windows_h: dict) -> datetime | None:
    hours = windows_h.get(category, {}).get(storage)
    if hours is None:
        hours = windows_h.get("curry_gravy", {}).get(storage, 2)
    if hours <= 0:
        return None
    return cooked_at + timedelta(hours=float(hours))


def assess(items: list[dict], now: datetime, safety: dict) -> SafetyAssessment:
    """items: dicts with food_category, storage, cooked_at (aware datetime)."""
    windows = safety["windows_h"]
    min_useful = float(safety.get("min_useful_window_min", 45))
    out: list[ItemSafety] = []
    for i, item in enumerate(items):
        until = item_safe_until(item["food_category"], item["storage"], item["cooked_at"], windows)
        if until is None:
            out.append(ItemSafety(i, None, 0, "divert", f"{item['storage'].replace('_', ' ')} storage is not safe for this food"))
            continue
        left = (until - now).total_seconds() / 60
        if left <= 0:
            out.append(ItemSafety(i, until, 0, "divert", "past its safe window"))
        elif left < min_useful:
            out.append(ItemSafety(i, until, left, "divert", f"only {int(left)} minutes left, under the {int(min_useful)}-minute minimum"))
        else:
            out.append(ItemSafety(i, until, left, "people"))

    people = [s for s in out if s.route == "people"]
    if not people:
        reasons = sorted({s.reason for s in out if s.reason})
        return SafetyAssessment("divert", None, 0, out, reasons)
    earliest = min(people, key=lambda s: s.safe_until)  # type: ignore[arg-type,return-value]
    reasons = [f"item {s.index + 1}: {s.reason}" for s in out if s.route == "divert"]
    return SafetyAssessment("people", earliest.safe_until, earliest.minutes_left, out, reasons)

"""Readiness tiers. New colleges get a useful (clearly labelled) plan from day one, and the
system says exactly what unlocks next instead of refusing to work."""

from __future__ import annotations

from dataclasses import dataclass

TIER_NAMES = {
    0: "Learning",
    1: "Baseline",
    2: "Full model selection",
    3: "Mature",
}

TIER_GIVES = {
    0: "Rule-based plan from headcount and portion norms, with wide ranges.",
    1: "Baselines and regularised linear models.",
    2: "The full model zoo, with a champion chosen per canteen by backtest.",
    3: "Semester seasonality, ensembles, and feedback features that prove their worth.",
}


@dataclass
class Readiness:
    tier: int
    name: str
    days: int
    next_tier_days: int | None
    message: str
    progress: float  # 0..1 toward the next tier


def readiness(days: int, tiers: dict) -> Readiness:
    t1, t2, t3 = int(tiers["tier1_days"]), int(tiers["tier2_days"]), int(tiers["tier3_days"])
    bounds = [(0, t1), (t1, t2), (t2, t3)]
    if days >= t3:
        return Readiness(3, TIER_NAMES[3], days, None, f"{days} days of history. Every model feature is unlocked.", 1.0)
    tier = 0 if days < t1 else 1 if days < t2 else 2
    lo, hi = bounds[tier]
    unlock = TIER_NAMES[tier + 1].lower()
    message = f"{days} of {hi} days. {TIER_NAMES[tier + 1]} unlocks at {hi} days." if tier < 2 else (
        f"{days} of {hi} days. {unlock.capitalize()} models unlock at {hi} days."
    )
    progress = (days - lo) / max(1, hi - lo)
    return Readiness(tier, TIER_NAMES[tier], days, hi, message, round(min(1.0, max(0.0, progress)), 3))

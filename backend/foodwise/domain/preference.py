"""Dish preference scores, smoothed so a dish with three votes cannot swing the model.

    score(d) = ( sum w_i r_i + k mu ) / ( sum w_i + k )
    w_i      = 0.5 ** (age_i / half_life)        recent opinions count more
    mu       = canteen-wide mean rating           shrinkage target for sparse dishes
    k        = pseudo-count (default 5)
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date


@dataclass
class Score:
    score: float
    n: int
    effective_n: float
    raw_mean: float | None


def decay_weight(age_days: float, half_life_days: float) -> float:
    return 0.5 ** (max(0.0, age_days) / max(1e-9, half_life_days))


def bayesian_score(
    ratings: Iterable[tuple[date, float, float]],
    as_of: date,
    *,
    prior_mean: float,
    half_life_days: float = 21,
    pseudo_count: float = 5,
) -> Score:
    """ratings: (date, rating on 1..5, extra weight such as spam down-weighting)."""
    num = den = 0.0
    raw_sum = 0.0
    n = 0
    for day, rating, weight in ratings:
        if day > as_of:
            continue  # as-of safe: never use opinions from after the cutoff
        w = decay_weight((as_of - day).days, half_life_days) * weight
        num += w * rating
        den += w
        raw_sum += rating
        n += 1
    score = (num + pseudo_count * prior_mean) / (den + pseudo_count)
    return Score(round(score, 3), n, round(den, 2), round(raw_sum / n, 3) if n else None)


def diagnose(score: float | None, prior_mean: float, waste_rate: float | None, sellout_rate: float | None) -> str | None:
    """Turn preference plus outcomes into a suggestion the menu planner can act on."""
    if score is None:
        return None
    if score < prior_mean - 0.35 and (waste_rate or 0) > 0.12:
        return "replace_or_reduce"
    if score > prior_mean + 0.3 and (sellout_rate or 0) > 0.2:
        return "increase"
    if score < prior_mean - 0.35:
        return "watch"
    return None

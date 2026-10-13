"""Cost-optimal preparation quantity (the newsvendor result).

    q* = F^-1( Cu / (Cu + Co) )

Cu is the cost of running one unit short and Co the cost of one surplus unit. Forecasts carry a
grid of quantiles, so F^-1 is read off the grid by linear interpolation rather than guessed
from three points.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

QUANTILE_LEVELS: tuple[float, ...] = (0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95)


def critical_ratio(shortage_to_waste_ratio: float) -> float:
    """Cu / (Cu + Co) with Cu expressed as a multiple of Co."""
    m = max(0.0, float(shortage_to_waste_ratio))
    return m / (1.0 + m)


def quantile_at(levels: Sequence[float], values: Sequence[float], p: float) -> float:
    """Inverse CDF from a quantile grid. Clamps outside the grid instead of extrapolating."""
    if not levels:
        raise ValueError("empty quantile grid")
    vals = sorted(values)  # enforce monotonicity (quantile crossing can occur numerically)
    if p <= levels[0]:
        return vals[0]
    if p >= levels[-1]:
        return vals[-1]
    for i in range(1, len(levels)):
        if p <= levels[i]:
            lo, hi = levels[i - 1], levels[i]
            w = (p - lo) / (hi - lo)
            return vals[i - 1] + w * (vals[i] - vals[i - 1])
    return vals[-1]


def round_for_unit(qty: float, unit: str) -> float:
    """Kitchens prepare whole plates and pieces; kg and litres to the nearest half."""
    qty = max(0.0, qty)
    if unit in ("kg", "litres"):
        return math.ceil(qty * 2) / 2
    return float(math.ceil(qty - 1e-9))


def recommended_quantity(levels: Sequence[float], values: Sequence[float], cr: float, unit: str) -> float:
    return round_for_unit(quantile_at(levels, values, cr), unit)


def expected_waste_and_shortage(levels: Sequence[float], values: Sequence[float], q: float) -> tuple[float, float]:
    """Expected surplus E[(q-D)+] and shortage E[(D-q)+] under the piecewise-linear forecast CDF.

    Integrates over a fine grid of probabilities, which is exact enough for planning and
    keeps the what-if simulator consistent with the recommendation.
    """
    n = 200
    waste = short = 0.0
    lo, hi = levels[0], levels[-1]
    for i in range(n):
        p = lo + (hi - lo) * (i + 0.5) / n
        d = quantile_at(levels, values, p)
        waste += max(q - d, 0.0)
        short += max(d - q, 0.0)
    return waste / n, short / n

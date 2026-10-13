"""Conformal prediction intervals for any point model.

Residuals are taken on the log scale, r = log((y + 1) / (yhat + 1)), from walk-forward
out-of-fold predictions only (never in-sample), so the interval reflects real forecasting error.
The quantiles of r turn a point forecast into a full quantile grid:

    q_tau = (yhat + 1) * exp(r_tau) - 1

Residuals are grouped ("Mondrian" conformal): unusual days (exams, events, holidays, rain,
disruptions) get their own error distribution, and ordinary days are split by meal, because a
fest-day dinner and a routine Tuesday breakfast are not equally predictable. Small groups fall
back to the pooled distribution.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from foodwise.domain.newsvendor import QUANTILE_LEVELS

MIN_GROUP = 40
SPECIAL_FLAGS = ("is_exam", "is_holiday", "is_vacation", "is_fest", "event_size", "disruption", "is_rainy")


def calibration_groups(frame: pd.DataFrame) -> np.ndarray:
    special = np.zeros(len(frame), dtype=bool)
    for col in SPECIAL_FLAGS:
        if col in frame:
            special |= frame[col].to_numpy(dtype=float) > 0
    meals = frame["meal"].astype(str).to_numpy()
    return np.where(special, "special", meals)


def _levels_for(n: int) -> np.ndarray:
    """Finite-sample correction: widen the empirical levels slightly for small calibration sets."""
    lv = np.array(QUANTILE_LEVELS)
    if n <= 0:
        return lv
    return np.clip(0.5 + (lv - 0.5) * (n + 1) / n, 0.0, 1.0)


@dataclass
class Calibrator:
    levels: tuple[float, ...] = QUANTILE_LEVELS
    pooled: list[float] = field(default_factory=list)
    groups: dict[str, list[float]] = field(default_factory=dict)
    n: int = 0

    @classmethod
    def fit(cls, y, yhat, groups) -> Calibrator:
        y, yhat = np.asarray(y, float), np.asarray(yhat, float)
        r = np.log((y + 1.0) / (np.maximum(yhat, 0) + 1.0))
        ok = np.isfinite(r)
        r, groups = r[ok], np.asarray(groups)[ok]
        if len(r) < 10:
            # Not enough evidence yet: a deliberately wide, symmetric band (about +-35% at P10/P90).
            z = np.array([-1.645, -1.2816, -0.8416, -0.5244, -0.2533, 0, 0.2533, 0.5244, 0.8416, 1.2816, 1.645])
            return cls(pooled=(z * 0.27).tolist(), n=len(r))
        cal = cls(pooled=np.quantile(r, _levels_for(len(r))).tolist(), n=len(r))
        for g in np.unique(groups):
            sub = r[groups == g]
            if len(sub) >= MIN_GROUP:
                cal.groups[str(g)] = np.quantile(sub, _levels_for(len(sub))).tolist()
        return cal

    def offsets(self, groups) -> np.ndarray:
        return np.array([self.groups.get(str(g), self.pooled) for g in groups], dtype=float)

    def quantiles(self, yhat, groups) -> np.ndarray:
        """(n, len(levels)) quantile grid, monotone in the level and non-negative."""
        yhat = np.maximum(np.asarray(yhat, float), 0)
        q = (yhat[:, None] + 1.0) * np.exp(self.offsets(groups)) - 1.0
        return np.maximum.accumulate(np.maximum(q, 0.0), axis=1)

    def to_dict(self) -> dict:
        return {"levels": list(self.levels), "pooled": self.pooled, "groups": self.groups, "n": self.n}

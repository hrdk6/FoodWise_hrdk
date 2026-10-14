"""The model zoo. One adapter per algorithm, all with the same contract:

    fit(train_frame, weights)      train on built feature rows with known demand
    observe(full_frame)            optional: see the as-of history (stateful time-series models)
    predict(frame) -> np.ndarray   point forecast (P50) of demand for each row

Learned models predict demand relative to the dish's own recent level (`dish_mean4`, an as-of
feature). That makes them scale-free across dishes measured in plates, kg and pieces, and lets
them learn multiplicative effects such as "rain -6%" directly. Intervals are added afterwards by
the conformal calibrator, identically for every model, so models compete on equal terms.
"""

from __future__ import annotations

import math
import time
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.preprocessing import StandardScaler

from foodwise.ml.features import FeatureSpec

try:  # LightGBM is optional; the zoo simply has one model fewer without it.
    import lightgbm as lgb

    HAS_LIGHTGBM = True
except Exception:  # noqa: BLE001
    HAS_LIGHTGBM = False

warnings.filterwarnings("ignore", message="X does not have valid feature names")
warnings.filterwarnings("ignore", category=UserWarning, module="lightgbm")

MEAL_RATE_PRIOR = {"breakfast": 0.62, "lunch": 0.78, "snacks": 0.45, "dinner": 0.84}
FAMILIES = {
    "seasonal_naive": "baseline",
    "weighted_mean": "baseline",
    "rule_prior": "baseline",
    "ridge": "linear",
    "elastic_net": "linear",
    "extra_trees": "trees",
    "hist_gbm": "boosting",
    "lightgbm": "boosting",
    "two_stage": "two_stage",
    "holt_winters": "classical",
    "ensemble": "ensemble",
}
LABELS = {
    "seasonal_naive": "Seasonal naive (same weekday last week)",
    "weighted_mean": "Weighted recent mean",
    "rule_prior": "Rule-based plan (headcount x norms)",
    "ridge": "Ridge regression",
    "elastic_net": "Elastic net",
    "extra_trees": "Extra trees",
    "hist_gbm": "Histogram gradient boosting",
    "lightgbm": "LightGBM",
    "two_stage": "Two-stage (footfall x dish share)",
    "holt_winters": "Holt-Winters footfall x share",
    "ensemble": "Ensemble blend",
}


# --------------------------------------------------------------------------- design matrix


def _ratio(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a / b.where(b > 0)).fillna(1.0).clip(0, 5)


def design_matrix(frame: pd.DataFrame, spec: FeatureSpec, levels: dict[str, list[str]]) -> pd.DataFrame:
    cols = [c for c in spec.columns if c not in spec.categorical]
    x = frame[cols].astype(float).copy()
    x["lag7_ratio"] = _ratio(frame["dish_lag7"], frame["dish_mean4"])
    x["last_ratio"] = _ratio(frame["dish_last"], frame["dish_mean4"])
    x["ewm_ratio"] = _ratio(frame["dish_ewm"], frame["dish_mean4"])
    x["ff_trend"] = _ratio(frame["footfall_mean7"], frame["footfall_mean28"])
    x["ff_lag1_ratio"] = _ratio(frame["footfall_lag1"], frame["footfall_mean7"])
    x["ff_lag7_ratio"] = _ratio(frame["footfall_lag7"], frame["footfall_mean7"])
    x["dow_vs_mean4"] = _ratio(frame["dish_dow_mean"], frame["dish_mean4"])
    x["lag7_vs_dow"] = _ratio(frame["dish_lag7"], frame["dish_dow_mean"])
    x["ff_dow_ratio"] = _ratio(frame["footfall_dow_mean"], frame["footfall_mean28"])
    x["rsvp_vs_ff"] = np.where(
        frame["rsvp_known"] > 0,
        _ratio(frame["rsvp_eating_rate"] * frame["expected_diners"], frame["footfall_mean28"]),
        0.0,
    )
    for cat in spec.categorical:
        values = frame[cat].astype(str)
        for level in levels.get(cat, []):
            x[f"{cat}={level}"] = (values == level).astype(float)
    # The same day can push meals in opposite directions (rain empties the lunch hall but fills the
    # evening chai queue), so context effects are allowed to differ by meal.
    meals = frame["meal"].astype(str)
    for level in levels.get("meal", []):
        is_meal = (meals == level).to_numpy(dtype=float)
        for col in ("rain_prob", "is_exam", "is_weekend", "event_size", "is_holiday"):
            x[f"{col}|{level}"] = frame[col].to_numpy(dtype=float) * is_meal
    return x.fillna(0.0)


def target(frame: pd.DataFrame) -> np.ndarray:
    """Training target: observed demand, with sold-out servings replaced by their imputed demand."""
    col = "target" if "target" in frame else "demand"
    return frame[col].to_numpy(dtype=float)


def base_level(frame: pd.DataFrame) -> np.ndarray:
    """The dish's own recent level: its same-weekday average once seen twice, else its last four servings."""
    dow = frame["dish_dow_mean"].to_numpy(dtype=float)
    recent = frame["dish_mean4"].to_numpy(dtype=float)
    level = np.where(frame["dish_dow_n"].to_numpy(dtype=float) >= 2, dow, recent)
    return np.maximum(np.nan_to_num(level, nan=1.0), 0.5)


# --------------------------------------------------------------------------- adapters


@dataclass
class ZooModel:
    name: str
    spec: FeatureSpec = field(default_factory=FeatureSpec)
    levels: dict[str, list[str]] = field(default_factory=dict)
    fit_seconds: float = 0.0

    @property
    def family(self) -> str:
        return FAMILIES[self.name]

    def _learn_levels(self, frame: pd.DataFrame) -> None:
        self.levels = {c: sorted(frame[c].astype(str).unique().tolist()) for c in self.spec.categorical}

    def fit(self, frame: pd.DataFrame, weights: np.ndarray) -> ZooModel:
        return self

    def observe(self, full: pd.DataFrame) -> None:  # noqa: B027 - optional hook
        pass

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError


class SeasonalNaive(ZooModel):
    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return frame["dish_lag7"].to_numpy(dtype=float)


class WeightedMean(ZooModel):
    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return frame["dish_ewm"].to_numpy(dtype=float)


class RulePrior(ZooModel):
    """Tier 0: headcount x meal attendance prior x share of competing dishes x portion norm."""

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        rate = frame["meal"].map(MEAL_RATE_PRIOR).fillna(0.7).to_numpy(dtype=float)
        diners = np.where(
            frame["footfall_mean7"].notna(), frame["footfall_mean7"], frame["expected_diners"] * rate
        ).astype(float)
        share = (1.0 / frame["n_same_course"].clip(lower=1)).to_numpy(dtype=float) * 0.9
        return diners * share * frame.get("portion_norm", pd.Series(1.0, index=frame.index)).to_numpy(dtype=float)


class LinearRatio(ZooModel):
    """Regularised linear model on log(demand / recent level): coefficients read as % effects."""

    alpha: float = 1.0
    l1_ratio: float | None = None

    def fit(self, frame: pd.DataFrame, weights: np.ndarray) -> LinearRatio:
        t0 = time.perf_counter()
        self._learn_levels(frame)
        x = design_matrix(frame, self.spec, self.levels)
        self.columns = list(x.columns)
        self.scaler = StandardScaler().fit(x, sample_weight=weights)
        y = np.log1p(target(frame)) - np.log1p(base_level(frame))
        if self.l1_ratio is None:
            self.est = Ridge(alpha=self.alpha)
        else:
            self.est = ElasticNet(alpha=self.alpha, l1_ratio=self.l1_ratio, max_iter=5000)
        self.est.fit(self.scaler.transform(x), y, sample_weight=weights)
        self.fit_seconds = time.perf_counter() - t0
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        x = design_matrix(frame, self.spec, self.levels)[self.columns]
        log_ratio = self.est.predict(self.scaler.transform(x))
        return np.maximum(0.0, np.expm1(np.log1p(base_level(frame)) + np.clip(log_ratio, -3, 3)))

    def coefficients(self) -> dict[str, float]:
        return dict(zip(self.columns, (self.est.coef_ / self.scaler.scale_).round(5).tolist(), strict=True))


class TreeRatio(ZooModel):
    """Tree ensembles on demand / recent level, weighted by level so large dishes matter more."""

    def _make(self):
        raise NotImplementedError

    def fit(self, frame: pd.DataFrame, weights: np.ndarray) -> TreeRatio:
        t0 = time.perf_counter()
        self._learn_levels(frame)
        x = design_matrix(frame, self.spec, self.levels)
        self.columns = list(x.columns)
        base = base_level(frame)
        y = np.clip(target(frame) / base, 0, 3)
        self.est = self._make()
        self.est.fit(x.to_numpy(), y, sample_weight=weights * base / base.mean())
        self.fit_seconds = time.perf_counter() - t0
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        x = design_matrix(frame, self.spec, self.levels)[self.columns]
        return np.maximum(0.0, self.est.predict(x.to_numpy()) * base_level(frame))


class HistGBM(TreeRatio):
    def _make(self):
        return HistGradientBoostingRegressor(
            max_iter=200, learning_rate=0.06, max_leaf_nodes=24, min_samples_leaf=25,
            l2_regularization=1.0, random_state=42,
        )


class ExtraTrees(TreeRatio):
    def _make(self):
        return ExtraTreesRegressor(
            n_estimators=160, min_samples_leaf=6, max_features=0.5, n_jobs=-1, random_state=42
        )


class LightGBM(TreeRatio):
    def _make(self):
        return lgb.LGBMRegressor(
            n_estimators=350, learning_rate=0.04, num_leaves=24, min_child_samples=25,
            subsample=0.85, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
            random_state=42, verbose=-1, n_jobs=2,
        )


FOOTFALL_FEATURES = [
    "dow", "is_weekend", "month", "is_month_end", "week_of_term", "is_exam", "is_holiday", "is_vacation",
    "is_fest", "event_size", "disruption", "temp_c", "rain_prob", "is_rainy", "rsvp_known",
    "rsvp_eating_rate", "rsvp_skip_rate",
]


def _meal_days(frame: pd.DataFrame) -> pd.DataFrame:
    cols = ["service_date", "meal", "footfall", "footfall_mean7", "footfall_mean28", "footfall_lag1", "footfall_lag7",
            *FOOTFALL_FEATURES]
    return frame[cols].groupby(["service_date", "meal"], as_index=False).first()


class TwoStage(ZooModel):
    """Stage 1 forecasts meal footfall; stage 2 multiplies by the dish's recent share of diners.
    Works for rotating menus and brand-new dishes, where per-dish history is thin."""

    def fit(self, frame: pd.DataFrame, weights: np.ndarray) -> TwoStage:
        t0 = time.perf_counter()
        days = _meal_days(frame.dropna(subset=["footfall"]))
        self.meals = sorted(days["meal"].unique().tolist())
        x = self._x(days)
        base = np.maximum(days["footfall_mean28"].to_numpy(dtype=float), 1.0)
        y = np.clip(days["footfall"].to_numpy(dtype=float) / base, 0, 3)
        self.est = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.06, max_leaf_nodes=16,
                                                 min_samples_leaf=10, random_state=42)
        self.est.fit(x, y)
        # Calibrate share: demand vs footfall x as-of share, per course.
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = pd.Series(target(frame), index=frame.index) / (frame["footfall"] * frame["dish_share4"])
        self.share_adj = (
            pd.DataFrame({"course": frame["course"], "r": ratio}).replace([np.inf, -np.inf], np.nan).dropna()
            .groupby("course")["r"].median().clip(0.5, 1.5).to_dict()
        )
        self.fit_seconds = time.perf_counter() - t0
        return self

    def _x(self, days: pd.DataFrame) -> np.ndarray:
        x = days[FOOTFALL_FEATURES].astype(float).copy()
        x["ff_trend"] = _ratio(days["footfall_mean7"], days["footfall_mean28"])
        x["ff_lag1_ratio"] = _ratio(days["footfall_lag1"], days["footfall_mean28"])
        x["ff_lag7_ratio"] = _ratio(days["footfall_lag7"], days["footfall_mean28"])
        for m in self.meals:
            x[f"meal={m}"] = (days["meal"] == m).astype(float)
        return x.fillna(0.0).to_numpy()

    def predict_footfall(self, frame: pd.DataFrame) -> pd.Series:
        days = _meal_days(frame)
        base = np.maximum(days["footfall_mean28"].to_numpy(dtype=float), 1.0)
        days["ff_hat"] = self.est.predict(self._x(days)) * base
        key = days.set_index(["service_date", "meal"])["ff_hat"]
        return pd.Series(key.reindex(pd.MultiIndex.from_frame(frame[["service_date", "meal"]])).to_numpy(), index=frame.index)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        ff = self.predict_footfall(frame).to_numpy(dtype=float)
        adj = frame["course"].map(self.share_adj).fillna(1.0).to_numpy(dtype=float)
        return np.maximum(0.0, ff * frame["dish_share4"].to_numpy(dtype=float) * adj)


class HoltWinters(ZooModel):
    """Classical framing: multiplicative weekly Holt-Winters on each meal's footfall series,
    times the dish's recent share. Parameters are chosen by one-step-ahead error on training data;
    at prediction time the filter runs over all footfall known before each target date."""

    grid: tuple[float, ...] = (0.1, 0.2, 0.35, 0.5)

    def fit(self, frame: pd.DataFrame, weights: np.ndarray) -> HoltWinters:
        t0 = time.perf_counter()
        self.params: dict[str, tuple[float, float]] = {}
        for meal, s in self._series(frame).items():
            best = (math.inf, (0.2, 0.1))
            for a in self.grid:
                for g in (0.05, 0.15, 0.3):
                    preds = self._filter(s, a, g)
                    mask = s.notna() & preds.notna()
                    if mask.sum() < 14:
                        continue
                    err = float(np.abs(s[mask] - preds[mask]).sum())
                    if err < best[0]:
                        best = (err, (a, g))
            self.params[meal] = best[1]
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = pd.Series(target(frame), index=frame.index) / (frame["footfall"] * frame["dish_share4"])
        self.share_adj = (
            pd.DataFrame({"course": frame["course"], "r": ratio}).replace([np.inf, -np.inf], np.nan).dropna()
            .groupby("course")["r"].median().clip(0.5, 1.5).to_dict()
        )
        self._history: dict[str, pd.Series] = {}
        self.fit_seconds = time.perf_counter() - t0
        return self

    @staticmethod
    def _series(frame: pd.DataFrame) -> dict[str, pd.Series]:
        out = {}
        daily = frame.groupby(["meal", "service_date"])["footfall"].max()
        for meal in daily.index.get_level_values(0).unique():
            s = daily.loc[meal]
            idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
            out[meal] = s.reindex(idx).astype(float)
        return out

    @staticmethod
    def _filter(s: pd.Series, alpha: float, gamma: float) -> pd.Series:
        """One-step-ahead forecasts; missing days (vacations, gaps) skip the state update."""
        vals = s.to_numpy(dtype=float)
        n = len(vals)
        out = np.full(n, np.nan)
        first = vals[: min(n, 28)]
        level = np.nanmean(first) if np.isfinite(first).any() else np.nan
        season = np.ones(7)
        if np.isfinite(level) and level > 0:
            for i in range(min(n, 28)):
                if np.isfinite(vals[i]):
                    season[i % 7] = 0.5 * season[i % 7] + 0.5 * vals[i] / level
        for i in range(n):
            if np.isfinite(level):
                out[i] = level * season[i % 7]
            y = vals[i]
            if not np.isfinite(y) or not np.isfinite(level) or level <= 0:
                if np.isfinite(y):
                    level = y
                continue
            new_level = alpha * (y / max(season[i % 7], 1e-3)) + (1 - alpha) * level
            season[i % 7] = gamma * (y / max(new_level, 1e-3)) + (1 - gamma) * season[i % 7]
            level = new_level
        return pd.Series(out, index=s.index)

    def observe(self, full: pd.DataFrame) -> None:
        self._history = self._series(full)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        if not self._history:
            self.observe(frame)
        ff = np.empty(len(frame))
        cache: dict[str, pd.Series] = {}
        for i, (day, meal) in enumerate(zip(frame["service_date"], frame["meal"], strict=True)):
            s = self._history.get(meal)
            if s is None:
                ff[i] = np.nan
                continue
            if meal not in cache:
                a, g = self.params.get(meal, (0.2, 0.1))
                ext = s.reindex(pd.date_range(s.index.min(), max(s.index.max(), frame["service_date"].max()), freq="D"))
                cache[meal] = self._filter(ext, a, g)
            ff[i] = cache[meal].get(day, np.nan)
        ff = np.where(np.isfinite(ff), ff, frame["footfall_mean7"].to_numpy(dtype=float))
        adj = frame["course"].map(self.share_adj).fillna(1.0).to_numpy(dtype=float)
        return np.maximum(0.0, ff * frame["dish_share4"].to_numpy(dtype=float) * adj)


@dataclass
class Ensemble(ZooModel):
    members: list[ZooModel] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)

    def observe(self, full: pd.DataFrame) -> None:
        for m in self.members:
            m.observe(full)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.sum([w * m.predict(frame) for m, w in zip(self.members, self.weights, strict=True)], axis=0)


def make(name: str, spec: FeatureSpec | None = None) -> ZooModel:
    spec = spec or FeatureSpec()
    if name == "seasonal_naive":
        return SeasonalNaive(name, spec)
    if name == "weighted_mean":
        return WeightedMean(name, spec)
    if name == "rule_prior":
        return RulePrior(name, spec)
    if name == "ridge":
        m = LinearRatio(name, spec)
        m.alpha, m.l1_ratio = 3.0, None
        return m
    if name == "elastic_net":
        m = LinearRatio(name, spec)
        m.alpha, m.l1_ratio = 0.002, 0.5
        return m
    if name == "hist_gbm":
        return HistGBM(name, spec)
    if name == "extra_trees":
        return ExtraTrees(name, spec)
    if name == "lightgbm":
        if not HAS_LIGHTGBM:
            raise RuntimeError("LightGBM is not installed")
        return LightGBM(name, spec)
    if name == "two_stage":
        return TwoStage(name, spec)
    if name == "holt_winters":
        return HoltWinters(name, spec)
    raise ValueError(f"unknown model {name!r}")


def candidates_for_tier(tier: int, has_footfall: bool) -> list[str]:
    """A small field: two baselines, two cheap linear models and two tree models.
    LightGBM, two-stage and Holt-Winters still exist (old versions load and can be built
    by name) but are no longer trained every week: they overlapped with the models below
    and pushed the run past its time budget."""
    names = ["seasonal_naive", "weighted_mean"]
    if tier >= 1:
        names += ["ridge", "elastic_net"]
    if tier >= 2:
        names += ["extra_trees", "hist_gbm"]
    return names


BASELINES = {"seasonal_naive", "weighted_mean", "rule_prior"}

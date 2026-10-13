"""The single feature builder, shared by training and serving (no train/serve skew).

Every feature for a row on service date d is computed only from information available at the
forecast cutoff on d-1: service logs strictly before d, plus things known in advance for d
itself (the menu, the academic calendar, the weather forecast, RSVPs closed at the cutoff).
`tests/test_features_asof.py` proves that changing or deleting any row on or after d leaves the
features for d unchanged.

Input frame (one row per canteen x date x meal x dish, history rows and target rows together):
    service_date, meal, dish_id, served, prepared, sold_out, footfall   (NaN for target rows)
Context frames are joined on date (and meal where relevant).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

NUMERIC_FEATURES = [
    "dow",
    "is_weekend",
    "month",
    "is_month_end",
    "week_of_term",
    "is_exam",
    "is_holiday",
    "is_vacation",
    "is_fest",
    "event_size",
    "disruption",
    "temp_c",
    "rain_prob",
    "is_rainy",
    "expected_diners",
    "rsvp_eating_rate",
    "rsvp_skip_rate",
    "rsvp_known",
    "footfall_lag1",
    "footfall_lag7",
    "footfall_mean7",
    "footfall_mean28",
    "dish_last",
    "dish_lag7",
    "dish_mean4",
    "dish_ewm",
    "dish_share4",
    "dish_dow_mean",
    "dish_dow_n",
    "footfall_dow_mean",
    "dish_n",
    "days_since_served",
    "log_dish_mean4",
    "log_footfall_mean7",
    "n_dishes_meal",
    "n_same_course",
    "is_nonveg",
    "is_special",
]
CENSORED_UPLIFT = 1.1  # assumed unmet demand when a dish sells out; a sold-out day hides how many were turned away
FEEDBACK_FEATURES = ["dish_score", "dish_score_trend", "portion_complaint_rate", "feedback_n"]
CATEGORICAL_FEATURES = ["meal", "course", "diet"]


@dataclass
class FeatureSpec:
    numeric: list[str] = field(default_factory=lambda: list(NUMERIC_FEATURES))
    categorical: list[str] = field(default_factory=lambda: list(CATEGORICAL_FEATURES))
    use_feedback: bool = False

    @property
    def columns(self) -> list[str]:
        cols = list(self.numeric) + (FEEDBACK_FEATURES if self.use_feedback else [])
        return cols + list(self.categorical)

    def hash(self) -> str:
        return hashlib.sha256(",".join(self.columns).encode()).hexdigest()[:16]


@dataclass
class Inputs:
    """Everything the builder may read. All frames are for one canteen."""

    rows: pd.DataFrame  # service_date, meal, dish_id, served, prepared, sold_out, footfall
    dishes: pd.DataFrame  # dish_id, course, diet
    calendar: pd.DataFrame  # day, kind, meal, expected_attendance, disruption
    weather: pd.DataFrame  # day, temp_c, rain_prob
    rsvp: pd.DataFrame  # service_date, meal, eating, skipping
    feedback: pd.DataFrame  # service_date, dish_id, rating, weight, portion
    expected_diners: int
    term_starts: list[pd.Timestamp] = field(default_factory=list)


def _calendar(inputs: Inputs) -> pd.DataFrame:
    cal = inputs.calendar
    if not len(cal):
        return pd.DataFrame(columns=["day", "kind", "meal", "expected_attendance", "disruption"])
    return cal.assign(day=pd.to_datetime(cal["day"]))


def _calendar_flags(df: pd.DataFrame, inputs: Inputs) -> pd.DataFrame:
    """Adds day-level academic flags and meal-aware disruption flags to df."""
    cal = _calendar(inputs)
    for kind, col in [("exam", "is_exam"), ("holiday", "is_holiday"), ("vacation", "is_vacation"), ("fest", "is_fest")]:
        marked = set(cal.loc[cal["kind"] == kind, "day"])
        df[col] = df["service_date"].isin(marked).astype(float)
    events = cal[cal["kind"].isin(["event", "fest"])].groupby("day")["expected_attendance"].sum()
    df["event_size"] = df["service_date"].map(events).astype(float).fillna(0.0) / max(1, inputs.expected_diners)
    disrupt = cal[cal["kind"] == "disruption"]
    whole_day = set(disrupt.loc[disrupt["meal"].isna(), "day"])
    per_meal = set(zip(disrupt.loc[disrupt["meal"].notna(), "day"], disrupt.loc[disrupt["meal"].notna(), "meal"], strict=True))
    keys = list(zip(df["service_date"], df["meal"], strict=True))
    df["disruption"] = [1.0 if (d in whole_day or (d, m) in per_meal) else 0.0 for d, m in keys]
    return df


def _week_of_term(day: pd.Timestamp, starts: list[pd.Timestamp]) -> float:
    past = [s for s in starts if s <= day]
    if not past:
        return float(((day - pd.Timestamp(day.year, 1, 1)).days // 7) % 20)
    return float((day - max(past)).days // 7)


def build(inputs: Inputs, spec: FeatureSpec | None = None) -> pd.DataFrame:
    """Return the input rows with feature columns appended, in the original row order."""
    spec = spec or FeatureSpec()
    df = inputs.rows.copy()
    df["service_date"] = pd.to_datetime(df["service_date"])
    df["_order"] = np.arange(len(df))
    df = df.sort_values(["service_date", "meal", "dish_id"], kind="mergesort").reset_index(drop=True)

    # Demand target. Sold-out rows are right-censored: served is only a lower bound on demand.
    df["demand"] = df["served"].astype(float)
    sold = df["sold_out"].fillna(False).astype(bool).to_numpy()
    df["_sold"] = sold

    # ---- calendar
    d = df["service_date"]
    df["dow"] = d.dt.dayofweek.astype(float)
    df["is_weekend"] = (df["dow"] >= 5).astype(float)
    df["month"] = d.dt.month.astype(float)
    df["is_month_end"] = (d.dt.days_in_month - d.dt.day < 3).astype(float)
    week = {day: _week_of_term(day, inputs.term_starts) for day in d.unique()}
    df["week_of_term"] = d.map(week).astype(float)

    df = _calendar_flags(df, inputs)

    # ---- weather (forecast for the target day)
    w = inputs.weather.rename(columns={"day": "service_date"})
    if len(w):
        w = w.assign(service_date=pd.to_datetime(w["service_date"]))
        df = df.merge(w[["service_date", "temp_c", "rain_prob"]], on="service_date", how="left")
    else:
        df["temp_c"] = np.nan
        df["rain_prob"] = np.nan
    df["temp_c"] = df["temp_c"].fillna(27.0)
    df["rain_prob"] = df["rain_prob"].fillna(0.2)
    df["is_rainy"] = (df["rain_prob"] >= 0.6).astype(float)
    df["expected_diners"] = float(inputs.expected_diners)

    # ---- RSVPs (collected until the cutoff, so known for the target day)
    if len(inputs.rsvp):
        r = inputs.rsvp.assign(service_date=pd.to_datetime(inputs.rsvp["service_date"]))
        df = df.merge(r[["service_date", "meal", "eating", "skipping"]], on=["service_date", "meal"], how="left")
        df["rsvp_known"] = df["eating"].notna().astype(float)
        df["rsvp_eating_rate"] = (df["eating"] / inputs.expected_diners).fillna(0.0)
        df["rsvp_skip_rate"] = (df["skipping"] / inputs.expected_diners).fillna(0.0)
        df = df.drop(columns=["eating", "skipping"])
    else:
        df["rsvp_known"] = 0.0
        df["rsvp_eating_rate"] = 0.0
        df["rsvp_skip_rate"] = 0.0

    # History features compare like with like: term days with term days and vacation days with
    # vacation days. On the first day of term, "recent level" therefore means the last term-time
    # level, not the near-empty vacation weeks just before it.
    df["_regime"] = df["is_vacation"].astype(int)
    # Baselines come from ordinary days; special days' effects are learned from the calendar flags.
    # Otherwise an exam week or a fest would drag the "normal level" around for a month afterwards.
    ordinary = ~((df["is_exam"] > 0) | (df["is_holiday"] > 0) | (df["is_fest"] > 0) | (df["event_size"] > 0)
                 | (df["disruption"] > 0))
    df["_ordinary"] = ordinary.astype(bool)

    # Censored demand. When a dish sells out, "served" only bounds demand from below. Feeding the
    # censored figure into history features starts a spiral: the next plan cooks less and the kitchen
    # runs out again. So a sold-out serving is imputed as the larger of served x uplift and the dish's
    # own uncensored level just before it (first pass), and every history feature reads the imputed series.
    df = df.sort_values(["meal", "dish_id", "_regime", "service_date"], kind="mergesort")
    clean = df["demand"].where(df["_ordinary"] & ~df["_sold"])
    level = clean.groupby([df["meal"], df["dish_id"], df["_regime"], df["service_date"].dt.dayofweek], sort=False).transform(
        lambda x: x.shift(1).rolling(4, min_periods=1).mean()
    )
    imputed = np.fmax(df["demand"] * CENSORED_UPLIFT, level * 1.05)
    df["_hist"] = np.where(df["_sold"], imputed, df["demand"])
    df["target"] = df["_hist"]
    df["_base"] = df["_hist"].where(df["_ordinary"])

    # ---- meal footfall history (one value per date and meal; strictly earlier dates only)
    meal_day = (
        df.groupby(["meal", "_regime", "service_date"], as_index=False)
        .agg(footfall=("footfall", "max"), ordinary=("_ordinary", "all"))
        .sort_values(["meal", "_regime", "service_date"])
    )
    parts = []
    for (meal, regime), g in meal_day.groupby(["meal", "_regime"], sort=False):
        s = g.set_index("service_date")["footfall"].astype(float)  # NaN on target dates
        base = s.where(g.set_index("service_date")["ordinary"])
        feat = pd.DataFrame(index=s.index)
        # Time-based windows with closed="left" exclude the current date, and NaN (target or
        # special-day) values are skipped, so these are as-of safe by construction.
        feat["footfall_mean7"] = base.rolling("7D", closed="left").mean()
        feat["footfall_mean28"] = base.rolling("28D", closed="left").mean()
        known = s.dropna()
        prev = known.copy()
        prev.index = prev.index + pd.Timedelta(days=1)
        feat["footfall_lag1"] = prev.reindex(s.index)
        prev7 = known.copy()
        prev7.index = prev7.index + pd.Timedelta(days=7)
        feat["footfall_lag7"] = prev7.reindex(s.index)
        # Same meal on the same weekday over the last four weeks (weekly menus repeat by weekday).
        feat["footfall_dow_mean"] = base.groupby(base.index.dayofweek).transform(
            lambda x: x.shift(1).rolling(4, min_periods=1).mean()
        )
        # After a long gap the windows are empty: carry the last value of this regime forward.
        # ffill only reads earlier rows, which were themselves computed from earlier data.
        feat["footfall_mean28"] = feat["footfall_mean28"].ffill()
        feat["footfall_mean7"] = feat["footfall_mean7"].fillna(feat["footfall_mean28"])
        feat["meal"] = meal
        feat["_regime"] = regime
        parts.append(feat.reset_index())
    if parts:
        df = df.merge(pd.concat(parts, ignore_index=True), on=["meal", "_regime", "service_date"], how="left")
    for col in ["footfall_lag1", "footfall_lag7", "footfall_mean7", "footfall_mean28", "footfall_dow_mean"]:
        if col not in df:
            df[col] = np.nan

    # ---- dish history (previous servings of the same dish at the same meal, same regime)
    df = df.sort_values(["meal", "dish_id", "_regime", "service_date"], kind="mergesort")
    df["_share"] = df["_base"] / df["footfall"].where(df["footfall"] > 0)
    g = df.groupby(["meal", "dish_id", "_regime"], sort=False)
    df["dish_last"] = g["_hist"].shift(1)
    df["dish_mean4"] = g["_base"].transform(lambda s: s.shift(1).rolling(4, min_periods=1).mean())
    df["dish_ewm"] = g["_base"].transform(lambda s: s.shift(1).ewm(alpha=0.45, ignore_na=True).mean())
    df["dish_share4"] = g["_share"].transform(lambda s: s.shift(1).rolling(4, min_periods=1).mean())
    df["_dow"] = df["service_date"].dt.dayofweek
    gd = df.groupby(["meal", "dish_id", "_regime", "_dow"], sort=False)["_base"]
    df["dish_dow_mean"] = gd.transform(lambda s: s.shift(1).rolling(4, min_periods=1).mean())
    df["dish_dow_n"] = gd.transform(lambda s: s.shift(1).notna().cumsum()).clip(upper=4).astype(float)
    # Calendar-based features use every earlier serving regardless of regime.
    df = df.sort_values(["meal", "dish_id", "service_date"], kind="mergesort")
    ga = df.groupby(["meal", "dish_id"], sort=False)
    df["dish_n"] = ga.cumcount().astype(float)
    df["days_since_served"] = (df["service_date"] - ga["service_date"].shift(1)).dt.days.astype(float)
    lag7 = df[["meal", "dish_id", "service_date", "_hist"]].copy()
    lag7["service_date"] = lag7["service_date"] + pd.Timedelta(days=7)
    lag7 = lag7.rename(columns={"_hist": "dish_lag7"})
    df = df.merge(lag7, on=["meal", "dish_id", "service_date"], how="left")

    # A history row has demand; target rows do not, so "previous serving" features for a
    # target row naturally come from the last logged serving.
    df["days_since_served"] = df["days_since_served"].fillna(60.0).clip(upper=120.0)
    df["is_special"] = (df["days_since_served"] > 10).astype(float)

    # ---- menu structure for the day
    dish_attr = inputs.dishes.set_index("dish_id")
    df["course"] = df["dish_id"].map(dish_attr["course"]).fillna("main").astype(str)
    df["diet"] = df["dish_id"].map(dish_attr["diet"]).fillna("veg").astype(str)
    df["is_nonveg"] = df["diet"].isin(["non_veg", "egg"]).astype(float)
    df["n_dishes_meal"] = df.groupby(["service_date", "meal"])["dish_id"].transform("count").astype(float)
    df["n_same_course"] = df.groupby(["service_date", "meal", "course"])["dish_id"].transform("count").astype(float)

    # ---- fallbacks for brand-new dishes and short histories
    meal_share_default = 1.0 / df["n_same_course"].clip(lower=1)
    df["dish_share4"] = df["dish_share4"].fillna(meal_share_default * 0.85)
    fallback_footfall = df["footfall_mean7"].fillna(df["expected_diners"] * 0.75)
    df["footfall_mean7"] = fallback_footfall
    df["footfall_mean28"] = df["footfall_mean28"].fillna(fallback_footfall)
    df["footfall_lag1"] = df["footfall_lag1"].fillna(fallback_footfall)
    df["footfall_lag7"] = df["footfall_lag7"].fillna(fallback_footfall)
    df["footfall_dow_mean"] = df["footfall_dow_mean"].fillna(df["footfall_mean7"])
    prior_dish = df["dish_share4"] * df["footfall_mean7"]
    df["dish_mean4"] = df["dish_mean4"].fillna(prior_dish)
    df["dish_ewm"] = df["dish_ewm"].fillna(df["dish_mean4"])
    df["dish_last"] = df["dish_last"].fillna(df["dish_mean4"])
    df["dish_lag7"] = df["dish_lag7"].fillna(df["dish_last"])
    df["dish_dow_mean"] = df["dish_dow_mean"].fillna(df["dish_mean4"])
    df["log_dish_mean4"] = np.log1p(df["dish_mean4"].clip(lower=0))
    df["log_footfall_mean7"] = np.log1p(df["footfall_mean7"].clip(lower=0))

    if spec.use_feedback:
        df = _add_feedback(df, inputs)

    df = df.sort_values("_order").drop(columns=["_order", "_share", "_dow", "_regime", "_hist", "_base", "_ordinary", "_sold"]).reset_index(drop=True)
    return df


def _add_feedback(df: pd.DataFrame, inputs: Inputs) -> pd.DataFrame:
    """Smoothed preference as of the day before service (no same-day or future opinions)."""
    fb = inputs.feedback
    if fb is None or not len(fb):
        for col in FEEDBACK_FEATURES:
            df[col] = 0.0 if col != "dish_score" else 3.5
        return df
    fb = fb.assign(service_date=pd.to_datetime(fb["service_date"]))
    prior = float(fb["rating"].mean())
    daily = (
        fb.assign(wr=fb["rating"] * fb["weight"], complaint=(fb["portion"] == "too_little").astype(float))
        .groupby(["dish_id", "service_date"])
        .agg(wr=("wr", "sum"), w=("weight", "sum"), n=("rating", "size"), complaints=("complaint", "sum"))
        .reset_index()
        .sort_values(["dish_id", "service_date"])
    )
    rows = []
    half_life, k = 21.0, 5.0
    for dish_id, g in daily.groupby("dish_id"):
        dates = g["service_date"].to_numpy()
        targets = df.loc[df["dish_id"] == dish_id, "service_date"].unique()
        for t in targets:
            mask = dates < np.datetime64(t)
            if not mask.any():
                rows.append((dish_id, t, prior, 0.0, 0.0, 0.0))
                continue
            sub = g[mask]
            age = (np.datetime64(t) - sub["service_date"].to_numpy()).astype("timedelta64[D]").astype(float)
            decay = 0.5 ** (age / half_life)
            score = (np.sum(decay * sub["wr"]) + k * prior) / (np.sum(decay * sub["w"]) + k)
            recent = sub[age <= 28]
            older = sub[(age > 28) & (age <= 56)]
            trend = 0.0
            if recent["w"].sum() >= 5 and older["w"].sum() >= 5:
                trend = recent["wr"].sum() / recent["w"].sum() - older["wr"].sum() / older["w"].sum()
            n = float(sub["n"].sum())
            complaint_rate = float(sub["complaints"].sum()) / max(1.0, n)
            rows.append((dish_id, t, float(score), float(trend), complaint_rate, n))
    feat = pd.DataFrame(rows, columns=["dish_id", "service_date", *FEEDBACK_FEATURES])
    df = df.merge(feat, on=["dish_id", "service_date"], how="left")
    df["dish_score"] = df["dish_score"].fillna(prior)
    for col in ["dish_score_trend", "portion_complaint_rate", "feedback_n"]:
        df[col] = df[col].fillna(0.0)
    return df


def neutral_overrides(df: pd.DataFrame) -> dict[str, dict[str, object]]:
    """Counterfactual settings used to explain a forecast: each driver group is switched to a
    'nothing special' value and the change in prediction is that driver's effect."""
    return {
        "rain": {"rain_prob": 0.1, "is_rainy": 0.0},
        "exam": {"is_exam": 0.0},
        "holiday": {"is_holiday": 0.0, "is_vacation": 0.0},
        "event": {"event_size": 0.0, "is_fest": 0.0},
        "disruption": {"disruption": 0.0},
        "weekday": {"dow": 2.0, "is_weekend": 0.0},
        "rsvp": {"rsvp_known": 0.0, "rsvp_eating_rate": 0.0, "rsvp_skip_rate": 0.0},
        "rating": {"dish_score": float(df["dish_score"].mean()) if "dish_score" in df else 3.5,
                   "dish_score_trend": 0.0},
        "month_end": {"is_month_end": 0.0},
    }

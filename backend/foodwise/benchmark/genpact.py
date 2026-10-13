"""Real-data benchmark: FoodWise's forecasting method on the public Genpact food demand dataset.

The app's canteens run on a simulated college with known ground truth. This module answers the
other question a reviewer asks: does the method hold up on real, messy data? It uses weekly orders
of 51 meals at 77 fulfilment centres of a meal-delivery company, released for the Genpact Machine
Learning Hackathon (Analytics Vidhya, 2018) and mirrored on Kaggle as "Food Demand Forecasting".

One function per cleaning step, so each can be explained, tested and shown on its own:

    load -> inspect -> handle_missing -> remove_duplicates -> fix_invalid -> investigate_outliers
         -> build_features -> validate -> backtest

test.csv has no orders (it was the hidden competition set), so models are scored on the last
weeks of train.csv, always trained only on weeks before the ones they predict.
"""

from __future__ import annotations

import json
import logging
import math
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from foodwise.core.storage import storage
from foodwise.ml.zoo import HAS_LIGHTGBM

if HAS_LIGHTGBM:
    import lightgbm as lgb

log = logging.getLogger("foodwise.benchmark")

FILES = {"train": "train.csv", "meals": "meal_info.csv", "centers": "fulfilment_center_info.csv"}
COLUMNS = {
    "train": ["id", "week", "center_id", "meal_id", "checkout_price", "base_price", "emailer_for_promotion",
              "homepage_featured", "num_orders"],
    "meals": ["meal_id", "category", "cuisine"],
    "centers": ["center_id", "city_code", "region_code", "center_type", "op_area"],
}
KEY = ["center_id", "meal_id"]
FLAGS = ["emailer_for_promotion", "homepage_featured"]
REPORT_KEY = "benchmark/genpact/report.json"
CLEAN_KEY = "benchmark/genpact/clean.csv"
FOLDS = 4
FOLD_WEEKS = 5
OUTLIER_Z = 5.0
SEED = 42

ORDER_FEATURES = ["lag1", "lag2", "lag3", "lag4", "mean4", "mean8", "ewm", "meal_level"]
OTHER_FEATURES = ["weeks_since_last", "n_prev", "has_history", "checkout_price", "base_price", "discount",
                  "price_change", "price_vs_recent", *FLAGS, "op_area", "week_of_year"]
CATEGORICAL = ["center_type", "category", "cuisine", "region_code"]
FEATURE_GROUPS = [
    ("Order history (as of the previous week)", ["lag1", "lag2", "lag3", "lag4", "mean4", "mean8", "ewm"]),
    ("Availability", ["weeks_since_last", "n_prev", "has_history"]),
    ("Same meal at other centres", ["meal_level"]),
    ("Price", ["checkout_price", "base_price", "discount", "price_change", "price_vs_recent"]),
    ("Promotion (planned in advance)", FLAGS),
    ("Centre and meal", ["center_type", "region_code", "op_area", "category", "cuisine"]),
    ("Season", ["week_of_year"]),
]
MODELS = [
    ("naive", "Naive (same as last week)", "baseline"),
    ("moving_average", "Moving average (last 4 weeks)", "baseline"),
    ("ridge", "Ridge regression", "linear"),
    ("extra_trees", "Extra trees", "trees"),
    ("hist_gbm", "Histogram gradient boosting", "boosting"),
] + ([("lightgbm", "LightGBM", "boosting")] if HAS_LIGHTGBM else [])

Progress = Callable[[str, float], None]


class BenchmarkError(Exception):
    """The input files are missing or are not the expected dataset; the message is shown to people."""


@dataclass
class Raw:
    train: pd.DataFrame
    meals: pd.DataFrame
    centers: pd.DataFrame


def _step(n: int, key: str, title: str, findings: list[str], action: str, counts: dict | None = None, **extra) -> dict:
    return {"n": n, "key": key, "title": title, "findings": findings, "action": action, "counts": counts or {}, **extra}


def _n(count: int, noun: str, plural: str | None = None) -> str:
    return f"{count:,} {noun if count == 1 else plural or noun + 's'}"


def _plain(v):
    """JSON-safe values: numpy scalars become Python numbers, NaN and infinity become null."""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if not math.isfinite(float(v)) else float(v)
    return v


# --------------------------------------------------------------------------- load


def missing_files(data_dir: Path) -> list[str]:
    return [name for name in FILES.values() if not (Path(data_dir) / name).is_file()]


def load(data_dir: Path) -> Raw:
    data_dir = Path(data_dir)
    absent = missing_files(data_dir)
    if absent:
        raise BenchmarkError(f"Missing {', '.join(absent)} in {data_dir}. Download the Kaggle dataset "
                             "'Food Demand Forecasting' and unzip it there.")
    frames = {}
    for part, name in FILES.items():
        df = pd.read_csv(data_dir / name)
        df.columns = [str(c).strip() for c in df.columns]
        lacking = [c for c in COLUMNS[part] if c not in df.columns]
        if lacking:
            raise BenchmarkError(f"{name} has no column(s) {', '.join(lacking)}. Is this the Genpact food demand dataset?")
        frames[part] = df[COLUMNS[part]]
    return Raw(**frames)


# --------------------------------------------------------------------------- 1. inspect


def inspect(raw: Raw) -> dict:
    t = raw.train
    week = pd.to_numeric(t["week"], errors="coerce")
    orders = pd.to_numeric(t["num_orders"], errors="coerce")
    pairs = t.groupby(KEY).ngroups
    n_weeks = int(week.max() - week.min() + 1)
    q = orders.quantile([0.5, 0.99, 1.0])
    files = {name: {"rows": len(df), "columns": list(df.columns)}
             for name, df in (("train.csv", t), ("meal_info.csv", raw.meals), ("fulfilment_center_info.csv", raw.centers))}
    findings = [
        f"train.csv has {len(t):,} rows and {t.shape[1]} columns. One row is one meal at one centre in one week.",
        f"Weeks {int(week.min())} to {int(week.max())}, {t['center_id'].nunique()} centres, {t['meal_id'].nunique()} meals "
        f"and {pairs:,} centre-meal pairs. meal_info.csv has {len(raw.meals)} meals; "
        f"fulfilment_center_info.csv has {len(raw.centers)} centres.",
        f"The target is num_orders: median {q[0.5]:,.0f}, 99th percentile {q[0.99]:,.0f}, maximum {q[1.0]:,.0f}. "
        "It is strongly right-skewed.",
        f"{pairs:,} pairs over {n_weeks} weeks would be {pairs * n_weeks:,} rows, but there are {len(t):,}. "
        "Pairs are not on sale every week.",
    ]
    action = ("Defined the target as num_orders, learned on a log scale because of the skew. A week with no row "
              "means the meal was not offered, so it is treated as a gap, not as zero orders.")
    return _step(1, "inspect", "Understand and inspect the dataset", findings, action,
                 {"rows": len(t), "columns": t.shape[1], "centres": t["center_id"].nunique(),
                  "meals": t["meal_id"].nunique(), "pairs": pairs, "first_week": int(week.min()),
                  "last_week": int(week.max())},
                 files=files, dtypes={c: str(t[c].dtype) for c in t.columns},
                 sample=_plain(t.head(5).to_dict(orient="records")))


# --------------------------------------------------------------------------- 2. missing values


def handle_missing(raw: Raw) -> tuple[Raw, dict]:
    t, meals, centers = raw.train.copy(), raw.meals.copy(), raw.centers.copy()
    per_column = {c: int(n) for c, n in t.isna().sum().items()}
    lookup = {f"meal_info.{c}": int(n) for c, n in meals.isna().sum().items()}
    lookup |= {f"fulfilment_center_info.{c}": int(n) for c, n in centers.isna().sum().items()}

    essential = ["week", *KEY, "checkout_price", "base_price", "num_orders"]
    drop = t[essential].isna().any(axis=1)
    flags_filled = int(t[FLAGS].isna().sum().sum())
    t[FLAGS] = t[FLAGS].fillna(0)
    t = t[~drop]
    meals = meals.dropna(subset=["meal_id"])
    centers = centers.dropna(subset=["center_id"])
    for c in ("category", "cuisine"):
        meals[c] = meals[c].fillna("Unknown")
    centers["center_type"] = centers["center_type"].fillna("Unknown")
    centers["op_area"] = pd.to_numeric(centers["op_area"], errors="coerce")
    centers["op_area"] = centers["op_area"].fillna(centers["op_area"].median())
    centers[["city_code", "region_code"]] = centers[["city_code", "region_code"]].fillna(-1)

    total = sum(per_column.values()) + sum(lookup.values())
    findings = [
        f"{_n(total, 'missing value')} across all three files." if total else "No missing values in any of the three files.",
        "Missing per column in train.csv: " + ", ".join(f"{c} {n:,}" for c, n in per_column.items() if n)
        if any(per_column.values()) else "Every column of train.csv is complete.",
    ]
    if any(lookup.values()):
        findings.append("Missing in the lookup files: " + ", ".join(f"{c} {n:,}" for c, n in lookup.items() if n))
    if total:
        action = (f"Dropped {_n(int(drop.sum()), 'row')} missing the week, centre, meal, a price or the target, because "
                  f"a guessed target would teach the model a made-up number. Filled {_n(flags_filled, 'missing promotion flag')} "
                  "with 0 (no promotion). Missing categories in the lookup files become 'Unknown' and a missing centre "
                  "area takes the median area.")
    else:
        action = ("Nothing to drop or fill. The rules stay in place for a future file: rows missing the target or a "
                  "price would be dropped rather than guessed, and a missing promotion flag would be read as 0.")
    return Raw(t, meals, centers), _step(2, "missing", "Find missing values", findings, action,
                                         {"missing_total": total, "rows_dropped": int(drop.sum()),
                                          "flags_filled": flags_filled},
                                         per_column=per_column, lookup=lookup)


# --------------------------------------------------------------------------- 3. duplicates


def remove_duplicates(raw: Raw) -> tuple[Raw, dict]:
    t = raw.train
    exact = int(t.duplicated().sum())
    t = t.drop_duplicates()
    same_id = int(t["id"].duplicated().sum())
    same_key = int(t.duplicated(subset=["week", *KEY]).sum())
    t = t.drop_duplicates(subset=["week", *KEY], keep="first")
    meals_dup = int(raw.meals["meal_id"].duplicated().sum())
    centers_dup = int(raw.centers["center_id"].duplicated().sum())
    meals = raw.meals.drop_duplicates(subset=["meal_id"], keep="first")
    centers = raw.centers.drop_duplicates(subset=["center_id"], keep="first")
    findings = [
        f"{_n(exact, 'row')} {'is an exact copy' if exact == 1 else 'are exact copies'} of another row.",
        f"{_n(same_key, 'further row')} {'repeats' if same_key == 1 else 'repeat'} a (week, centre, meal) that already "
        f"has a row; {_n(same_id, 'row')} {'repeats' if same_id == 1 else 'repeat'} an id.",
        f"{_n(meals_dup, 'repeated meal id')} in meal_info.csv and {_n(centers_dup, 'repeated centre id')} in "
        "fulfilment_center_info.csv.",
    ]
    found = exact + same_key + meals_dup + centers_dup
    action = ("A centre sells a meal once per week, so (week, centre, meal) is the natural key, not the whole row. "
              + ("Removed exact copies, then kept the first row for any key that still repeated. A repeated lookup id "
                 "keeps its first entry so the later join cannot multiply rows." if found else
                 "No duplicates were found, so nothing was removed; the check runs on every file."))
    return Raw(t, meals, centers), _step(3, "duplicates", "Detect duplicate records", findings, action,
                                         {"exact": exact, "same_key": same_key, "same_id": same_id,
                                          "lookup": meals_dup + centers_dup})


# --------------------------------------------------------------------------- 4. invalid and inconsistent data


def fix_invalid(raw: Raw) -> tuple[pd.DataFrame, dict]:
    t, meals, centers = raw.train.copy(), raw.meals.copy(), raw.centers.copy()
    unparsable = 0
    for c in COLUMNS["train"]:
        before = t[c].notna()
        t[c] = pd.to_numeric(t[c], errors="coerce")
        unparsable += int((before & t[c].isna()).sum())
    meals["meal_id"] = pd.to_numeric(meals["meal_id"], errors="coerce")
    centers["center_id"] = pd.to_numeric(centers["center_id"], errors="coerce")
    meals, centers = meals.dropna(subset=["meal_id"]), centers.dropna(subset=["center_id"])

    rules = {
        "not_a_number": t.isna().any(axis=1),
        "bad_week": (t["week"] < 1) | (t["week"] % 1 != 0),
        "negative_orders": t["num_orders"] < 0,
        "price_not_positive": (t["checkout_price"] <= 0) | (t["base_price"] <= 0),
        "flag_not_0_or_1": ~t["emailer_for_promotion"].isin([0, 1]) | ~t["homepage_featured"].isin([0, 1]),
        "unknown_centre": ~t["center_id"].isin(centers["center_id"]),
        "unknown_meal": ~t["meal_id"].isin(meals["meal_id"]),
    }
    bad = np.zeros(len(t), dtype=bool)
    for mask in rules.values():
        bad |= mask.to_numpy()
    counts = {k: int(v.sum()) for k, v in rules.items()}
    t = t[~bad]

    fractional = int((t["num_orders"] % 1 != 0).sum())
    zero = int((t["num_orders"] == 0).sum())
    ratio = t["checkout_price"] / t["base_price"]
    implausible_price = int(((ratio > 3) | (ratio < 1 / 3)).sum())
    t["num_orders"] = t["num_orders"].round()
    for c in ("id", "week", *KEY, "num_orders", *FLAGS):
        t[c] = t[c].astype(int)

    relabelled = 0
    for c in ("category", "cuisine"):
        before = meals[c].astype(str)
        meals[c] = before.str.strip().str.replace(r"\s+", " ", regex=True).str.title()
        relabelled += int((before != meals[c]).sum())
    before = centers["center_type"].astype(str)
    centers["center_type"] = before.str.strip().str.upper()
    relabelled += int((before != centers["center_type"]).sum())
    meals["meal_id"], centers["center_id"] = meals["meal_id"].astype(int), centers["center_id"].astype(int)
    centers["region_code"] = pd.to_numeric(centers["region_code"], errors="coerce").fillna(-1).astype(int)

    df = t.merge(centers[["center_id", "region_code", "center_type", "op_area"]], on="center_id", how="inner")
    df = df.merge(meals, on="meal_id", how="inner").sort_values(["week", *KEY]).reset_index(drop=True)

    cities = int(centers["city_code"].nunique())
    findings = [
        f"{_n(unparsable, 'value')} could not be read as numbers." if unparsable else "Every numeric column parses as a number.",
        "Impossible values: " + ", ".join(f"{k.replace('_', ' ')} {v:,}" for k, v in counts.items()) + ".",
        f"{_n(zero, 'row')} with zero orders and {_n(fractional, 'row')} with a fractional order count.",
        f"{_n(implausible_price, 'row')} selling at more than 3 times, or less than a third of, the base price.",
        (f"{_n(relabelled, 'category label')} needed tidying (extra spaces, inconsistent capitals)." if relabelled
         else "Category labels are already consistent.")
        + " Categories: " + ", ".join(sorted(meals["category"].unique()))
        + ". Cuisines: " + ", ".join(sorted(meals["cuisine"].unique())) + ".",
    ]
    action = ((f"Dropped {_n(int(bad.sum()), 'row')} with impossible values, since they cannot be true. " if bad.any()
               else "No impossible values, so no rows were dropped. ")
              + "Fixed the column types and rounded order counts to whole orders. Zero orders and unusual prices are "
              "possible, so they are counted but kept. Category text is trimmed and capitalised consistently, then the "
              f"meal and centre details are joined onto each row. city_code is not used: {cities} arbitrary codes with "
              "no order to them, and region_code already carries location.")
    return df, _step(4, "invalid", "Fix inconsistent and invalid data", findings, action,
                     {"unparsable": unparsable, **counts, "rows_dropped": int(bad.sum()), "zero_orders": zero,
                      "implausible_price": implausible_price, "labels_tidied": relabelled})


# --------------------------------------------------------------------------- 5. outliers


def investigate_outliers(df: pd.DataFrame) -> dict:
    y = np.log1p(df["num_orders"].astype(float))
    groups = [df["center_id"], df["meal_id"]]
    med = y.groupby(groups).transform("median")
    mad = (y - med).abs().groupby(groups).transform("median")
    z = 0.6745 * (y - med) / np.maximum(mad, 0.05)
    high, low = z > OUTLIER_Z, z < -OUTLIER_Z
    promo = (df["emailer_for_promotion"] == 1) | (df["homepage_featured"] == 1)
    over_15k = int((df["num_orders"] > 15000).sum())
    top = df.assign(z=z, typical=np.expm1(med).round(), promo=promo)[high | low]
    top = top.reindex(top["z"].abs().sort_values(ascending=False).index).head(8)
    examples = [
        {"week": int(r.week), "center_id": int(r.center_id), "meal_id": int(r.meal_id), "category": r.category,
         "orders": int(r.num_orders), "typical": int(r.typical), "promotion": bool(r.promo)}
        for r in top.itertuples()
    ]
    promo_all = float(promo.mean())
    promo_high = float(promo[high].mean()) if high.any() else 0.0
    # "Explained by promotions" only when promoted weeks are clearly over-represented among the spikes.
    promo_driven = bool(high.any() and promo_high >= 0.5 and promo_high >= 2 * promo_all)
    findings = [
        f"{_n(int(high.sum()), 'unusually high week')} and {_n(int(low.sum()), 'unusually low week')}, judged against "
        f"each centre-meal pair's own typical level (robust z-score beyond {OUTLIER_Z:g} on log orders).",
        f"For comparison, a fixed cut at 15,000 orders would remove {_n(over_15k, 'row')}.",
    ]
    if high.any():
        findings.insert(1, f"{promo_high:.0%} of the unusually high weeks had an email or homepage promotion, against "
                           f"{promo_all:.0%} of all weeks. "
                           + ("Most spikes coincide with promotions." if promo_driven
                              else "Promotions explain only part of the spikes."))
    why = ("Most spikes coincide with promotions, so they are real demand the model should learn, and the promotion "
           "flags are inputs that explain them." if promo_driven else
           "Nothing in the data marks them as errors: an order count cannot be checked against a receipt, and deleting "
           "real busy weeks would teach the model to under-forecast them.")
    action = (("Kept every outlier. " + why if (high.any() or low.any()) else "No outliers to act on. ")
              + " Learning on log orders stops a few huge weeks from dominating the fit. The robust z-score uses the "
              "median and the median absolute deviation, because a mean and standard deviation are themselves pulled "
              "by the outliers being looked for.")
    return _step(5, "outliers", "Investigate outliers", findings, action,
                 {"high": int(high.sum()), "low": int(low.sum()), "over_15000": over_15k,
                  "promo_share_high": round(promo_high, 3), "promo_share_all": round(promo_all, 3),
                  "promo_driven": promo_driven},
                 examples=examples)


# --------------------------------------------------------------------------- 6. features


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """One row per observed (week, centre, meal), with inputs known before that week.

    Order history is computed on a calendar-week grid per pair, so "last week" means the previous
    calendar week, not the previous row. Every window is shifted by one week first, so a row never
    sees its own orders or anything later.
    """
    df = df.sort_values([*KEY, "week"]).reset_index(drop=True)
    spans = df.groupby(KEY)["week"].agg(["min", "max"])
    lens = (spans["max"] - spans["min"] + 1).to_numpy()
    grid = pd.DataFrame({
        "center_id": np.repeat(spans.index.get_level_values("center_id").to_numpy(), lens),
        "meal_id": np.repeat(spans.index.get_level_values("meal_id").to_numpy(), lens),
        "week": np.concatenate([np.arange(a, b + 1) for a, b in zip(spans["min"], spans["max"], strict=True)]),
    })
    grid = grid.merge(df[[*KEY, "week", "num_orders", "checkout_price"]], on=[*KEY, "week"], how="left")
    by = [grid["center_id"], grid["meal_id"]]
    orders = grid["num_orders"].astype(float)

    for k in (1, 2, 3, 4):
        grid[f"lag{k}"] = orders.groupby(by, sort=False).shift(k)
    prev = orders.groupby(by, sort=False).shift(1)
    for n in (4, 8):
        grid[f"mean{n}"] = prev.groupby(by, sort=False).rolling(n, min_periods=1).mean().reset_index(level=[0, 1], drop=True)
    grid["ewm"] = prev.groupby(by, sort=False).transform(lambda s: s.ewm(alpha=0.5, ignore_na=True).mean())
    seen = grid["week"].where(orders.notna()).astype(float)
    last_seen = seen.groupby(by, sort=False).shift(1).groupby(by, sort=False).ffill()
    grid["weeks_since_last"] = grid["week"] - last_seen
    observed = orders.notna().astype(int)
    grid["n_prev"] = observed.groupby(by, sort=False).cumsum() - observed
    price = grid["checkout_price"]
    prev_price = price.groupby(by, sort=False).shift(1)
    grid["price_change"] = price / prev_price.groupby(by, sort=False).ffill() - 1
    recent_price = prev_price.groupby(by, sort=False).rolling(4, min_periods=1).mean().reset_index(level=[0, 1], drop=True)
    grid["price_vs_recent"] = price / recent_price
    # The same meal's recent level across all centres, for pairs with little history of their own.
    grid["meal_level"] = grid.groupby(["meal_id", "week"])["mean4"].transform("mean")

    hist_cols = [c for c in grid.columns if c not in ("num_orders", "checkout_price")]
    frame = df.merge(grid.loc[orders.notna(), hist_cols], on=[*KEY, "week"], how="left")

    weekly = df.groupby("week")["num_orders"].mean().sort_index()
    prior = weekly.expanding().mean().shift(1).fillna(weekly.iloc[0])
    frame["meal_level"] = frame["meal_level"].fillna(frame["week"].map(prior))
    level = frame["mean4"].fillna(frame["meal_level"])
    for c in ("lag1", "lag2", "lag3", "lag4", "mean4", "mean8", "ewm"):
        frame[c] = frame[c].fillna(level)
    frame["weeks_since_last"] = frame["weeks_since_last"].fillna(52).clip(upper=52)
    frame["n_prev"] = frame["n_prev"].clip(upper=52)
    frame["has_history"] = (frame["n_prev"] > 0).astype(float)
    frame["price_change"] = frame["price_change"].replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-1, 3)
    frame["price_vs_recent"] = frame["price_vs_recent"].replace([np.inf, -np.inf], np.nan).fillna(1.0).clip(0, 5)
    frame["discount"] = ((frame["base_price"] - frame["checkout_price"]) / frame["base_price"]).clip(-1, 1)
    frame["week_of_year"] = (frame["week"] - 1) % 52 + 1
    return frame.sort_values(["week", *KEY]).reset_index(drop=True)


def category_levels(frame: pd.DataFrame) -> dict[str, list[str]]:
    return {c: sorted(frame[c].astype(str).unique().tolist()) for c in CATEGORICAL}


def design(frame: pd.DataFrame, levels: dict[str, list[str]]) -> pd.DataFrame:
    """The numeric matrix the models see. Order features go on a log scale like the target; categories
    are one-hot encoded. Scaling is applied only inside the linear model, since trees do not need it."""
    x = pd.DataFrame(index=frame.index)
    for c in ORDER_FEATURES:
        x[f"log_{c}"] = np.log1p(frame[c].astype(float).clip(lower=0))
    for c in OTHER_FEATURES:
        x[c] = frame[c].astype(float)
    for cat, values in levels.items():
        col = frame[cat].astype(str)
        for v in values:
            x[f"{cat}={v}"] = (col == v).astype(float)
    return x


def feature_step(frame: pd.DataFrame, levels: dict[str, list[str]]) -> dict:
    n_onehot = sum(len(v) for v in levels.values())
    width = len(ORDER_FEATURES) + len(OTHER_FEATURES) + n_onehot
    cold = int((frame["n_prev"] == 0).sum())
    findings = [
        f"{len(ORDER_FEATURES) + len(OTHER_FEATURES) + len(CATEGORICAL)} features in {len(FEATURE_GROUPS)} groups, "
        f"{width} model columns once the {len(CATEGORICAL)} categorical features are one-hot encoded.",
        "Order history uses calendar weeks: lag1 is the previous calendar week, and a week the meal was not "
        "offered stays empty instead of counting as zero.",
        f"{_n(cold, 'row')} {'is' if cold == 1 else 'are'} a pair's first week, with no history of its own; history "
        "features there fall back to the same meal's level at other centres.",
    ]
    action = ("Built lags (1 to 4 weeks), 4- and 8-week averages and an exponentially weighted average, all shifted "
              "so they only use earlier weeks. Added discount and price-change features, the promotion flags (known "
              "in advance), centre and meal attributes, and week of year. Order counts and the target are log-"
              "transformed; categories are one-hot encoded; the linear model standardises its inputs. Removed id "
              "(an identifier with no meaning) and city_code.")
    return _step(6, "features", "Prepare features for ML", findings, action,
                 {"features": len(ORDER_FEATURES) + len(OTHER_FEATURES) + len(CATEGORICAL), "model_columns": width,
                  "cold_start_rows": cold},
                 groups=[{"group": g, "columns": cols} for g, cols in FEATURE_GROUPS])


# --------------------------------------------------------------------------- 7. validate


def leakage_check(df: pd.DataFrame, cutoff: int | None = None, pairs: int = 300) -> bool:
    """Change every order after `cutoff` and rebuild the features. Rows up to the week after the cutoff
    must not move: their inputs may only depend on weeks before them."""
    keys = df[KEY].drop_duplicates().sample(n=min(pairs, len(df[KEY].drop_duplicates())), random_state=SEED)
    sample = df.merge(keys, on=KEY)
    cutoff = cutoff or int(sample["week"].quantile(0.8))
    a = build_features(sample)
    changed = sample.copy()
    later = changed["week"] > cutoff
    changed.loc[later, "num_orders"] = changed.loc[later, "num_orders"] * 3 + 7
    b = build_features(changed)
    cols = [c for c in ORDER_FEATURES + OTHER_FEATURES if c in a]
    keep = a["week"] <= cutoff + 1
    return bool(np.allclose(a.loc[keep, cols].to_numpy(float), b.loc[keep, cols].to_numpy(float), equal_nan=True))


def validate(df: pd.DataFrame, frame: pd.DataFrame, x: pd.DataFrame) -> dict:
    nan_cells = int(x.isna().sum().sum())
    dup_keys = int(frame.duplicated(subset=["week", *KEY]).sum())
    no_leak = leakage_check(df)
    max_week = int(frame["week"].max())
    first_test = max_week - FOLDS * FOLD_WEEKS + 1
    findings = [
        f"The cleaned table has {len(df):,} rows; the feature matrix has {nan_cells:,} empty cells and "
        f"{dup_keys:,} repeated (week, centre, meal) keys.",
        "Leakage test: every order after a cut-off week was changed and the features rebuilt. Features up to "
        f"the week after the cut-off {'did not change, so no row sees its own future' if no_leak else 'CHANGED: a feature uses future data'}.",
        f"Models are tested on weeks {first_test} to {max_week} in {FOLDS} blocks of {FOLD_WEEKS} weeks, each "
        "trained only on the weeks before its block. A random split would let the model learn from the future.",
    ]
    action = "Saved the cleaned table as a CSV so every step can be checked by hand."
    return _step(7, "validate", "Validate and save", findings, action,
                 {"rows": len(df), "nan_cells": nan_cells, "duplicate_keys": dup_keys, "leakage_passed": no_leak},
                 passed=bool(nan_cells == 0 and dup_keys == 0 and no_leak))


# --------------------------------------------------------------------------- backtest


def _fit_predict(name: str, x_train: pd.DataFrame, y_train: np.ndarray, x_test: pd.DataFrame,
                 test: pd.DataFrame) -> np.ndarray:
    if name == "naive":
        return test["lag1"].to_numpy(float)
    if name == "moving_average":
        return test["mean4"].to_numpy(float)
    y = np.log1p(y_train)
    if name == "ridge":
        scaler = StandardScaler().fit(x_train)
        pred = Ridge(alpha=3.0).fit(scaler.transform(x_train), y).predict(scaler.transform(x_test))
    else:
        if name == "extra_trees":
            est = ExtraTreesRegressor(n_estimators=60, min_samples_leaf=30, max_features=0.5, n_jobs=-1,
                                      random_state=SEED)
        elif name == "hist_gbm":
            est = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, max_leaf_nodes=63,
                                                min_samples_leaf=40, l2_regularization=1.0, random_state=SEED)
        elif name == "lightgbm":
            est = lgb.LGBMRegressor(n_estimators=400, learning_rate=0.05, num_leaves=63, min_child_samples=40,
                                    subsample=0.85, subsample_freq=1, colsample_bytree=0.8, random_state=SEED,
                                    verbose=-1)
        else:
            raise ValueError(f"unknown model {name!r}")
        pred = est.fit(x_train.to_numpy(), y).predict(x_test.to_numpy())
    return np.expm1(np.clip(pred, 0, 12))


def scores(y: np.ndarray, p: np.ndarray) -> dict:
    total = float(y.sum())
    return {
        "wape": float(np.abs(y - p).sum() / total),
        "rmsle": float(np.sqrt(np.mean((np.log1p(np.maximum(p, 0)) - np.log1p(y)) ** 2))),
        "bias": float((p - y).sum() / total),
    }


def backtest(frame: pd.DataFrame, x: pd.DataFrame, progress: Progress | None = None) -> dict:
    progress = progress or (lambda *_: None)
    y = frame["num_orders"].to_numpy(float)
    max_week = int(frame["week"].max())
    first_test = max_week - FOLDS * FOLD_WEEKS + 1
    preds = {name: np.full(len(frame), np.nan) for name, _, _ in MODELS}
    seconds = dict.fromkeys(preds, 0.0)
    folds = []
    steps, done = FOLDS * len(MODELS), 0
    for k in range(FOLDS):
        lo, hi = first_test + k * FOLD_WEEKS, first_test + (k + 1) * FOLD_WEEKS - 1
        train = ((frame["week"] < lo) & (frame["has_history"] > 0)).to_numpy()
        test = frame["week"].between(lo, hi).to_numpy()
        folds.append({"fold": k + 1, "train_weeks": [int(frame["week"].min()), lo - 1], "test_weeks": [lo, hi],
                      "train_rows": int(train.sum()), "test_rows": int(test.sum())})
        for name, label, _ in MODELS:
            progress(f"Fold {k + 1} of {FOLDS}: {label}", done / steps)
            t0 = time.perf_counter()
            preds[name][test] = _fit_predict(name, x[train], y[train], x[test], frame[test])
            seconds[name] += time.perf_counter() - t0
            done += 1
            log.info("genpact fold %d %s %.1fs", k + 1, name, time.perf_counter() - t0)

    tested = frame["week"] >= first_test
    cold = tested & (frame["n_prev"] == 0)
    rows = []
    for name, label, family in MODELS:
        p = preds[name]
        per_fold = [scores(y[frame["week"].between(*f["test_weeks"]).to_numpy()],
                           p[frame["week"].between(*f["test_weeks"]).to_numpy()])["wape"] for f in folds]
        rows.append({"model": name, "label": label, "family": family, **scores(y[tested], p[tested]),
                     "per_fold_wape": per_fold, "fit_seconds": round(seconds[name], 1)})
    best_baseline = min((r for r in rows if r["family"] == "baseline"), key=lambda r: r["wape"])
    for r in rows:
        r["skill"] = 1 - r["wape"] / best_baseline["wape"]
        r["beats_baseline_every_fold"] = all(
            w < b for w, b in zip(r["per_fold_wape"], best_baseline["per_fold_wape"], strict=True))
    learned = [r for r in rows if r["family"] != "baseline" and r["skill"] > 0]
    winner = min(learned, key=lambda r: r["wape"]) if learned else best_baseline
    for r in rows:
        r["status"] = ("selected" if r is winner else "baseline" if r["family"] == "baseline"
                       else "beats_baseline" if r["skill"] > 0 else "worse_than_baseline")
    rows.sort(key=lambda r: (r["family"] == "baseline", r["wape"]))
    return {"folds": folds, "leaderboard": rows, "winner": winner["model"], "best_baseline": best_baseline["model"],
            "horizon": "1 week ahead", "tested_rows": int(tested.sum()), "cold_start_rows": int(cold.sum())}


# --------------------------------------------------------------------------- run and store


def clean(raw: Raw) -> tuple[pd.DataFrame, list[dict]]:
    """Steps 1 to 5: the cleaned table, plus a report entry for each step."""
    s1 = inspect(raw)
    raw, s2 = handle_missing(raw)
    raw, s3 = remove_duplicates(raw)
    df, s4 = fix_invalid(raw)
    s5 = investigate_outliers(df)
    return df, [s1, s2, s3, s4, s5]


def run(data_dir: Path, progress: Progress | None = None) -> tuple[dict, pd.DataFrame]:
    progress = progress or (lambda *_: None)
    t0 = time.perf_counter()
    progress("Reading the files", 0.01)
    raw = load(data_dir)
    progress("Cleaning", 0.04)
    df, steps = clean(raw)
    progress("Building features", 0.08)
    frame = build_features(df)
    levels = category_levels(frame)
    x = design(frame, levels)
    steps.append(feature_step(frame, levels))
    progress("Checking for leakage", 0.14)
    steps.append(validate(df, frame, x))
    result = backtest(frame, x, progress=lambda label, f: progress(label, 0.18 + 0.8 * f))
    versions = {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__,
                "scikit-learn": sklearn.__version__}
    if HAS_LIGHTGBM:
        versions["lightgbm"] = lgb.__version__
    report = {
        "dataset": {
            "name": "Food Demand Forecasting (Genpact Machine Learning Hackathon)",
            "source": "Analytics Vidhya / Genpact, 2018; mirrored on Kaggle as 'Food Demand Forecasting'.",
            "description": "Weekly orders of meals at the fulfilment centres of a meal-delivery company, with "
                           "prices, promotions and centre and meal attributes.",
            "files": steps[0]["files"],
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "duration_seconds": round(time.perf_counter() - t0, 1),
        "rows_raw": len(raw.train),
        "rows_clean": len(df),
        "steps": steps,
        **result,
        "notes": [
            "Weekly delivery-centre data, not a canteen: there is no weather, exam calendar, RSVP or footfall "
            "data, so this checks the forecasting method, not FoodWise's canteen-specific features.",
            "Forecasts are one week ahead using last week's actual orders. The original competition forecast 10 "
            "weeks ahead, so these errors are not comparable with its leaderboard.",
            "test.csv is not used: it has no orders, so it cannot be scored.",
        ],
        "library_versions": versions,
    }
    progress("Done", 1.0)
    return _plain(report), df


def save(report: dict, df: pd.DataFrame) -> None:
    storage().put(CLEAN_KEY, df.to_csv(index=False).encode())
    storage().put(REPORT_KEY, json.dumps(report).encode())


def saved_report() -> dict | None:
    if not storage().exists(REPORT_KEY):
        return None
    return json.loads(storage().get(REPORT_KEY))


def clean_csv_path() -> Path | None:
    return storage().local_path(CLEAN_KEY) if storage().exists(CLEAN_KEY) else None

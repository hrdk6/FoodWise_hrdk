"""ML platform guarantees: no leakage, time-ordered validation, calibrated intervals, and a golden
dataset (the simulated college) on which learned models must beat the baselines."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import numpy as np
import pandas as pd
import pytest

from foodwise.ml import zoo
from foodwise.ml.conformal import Calibrator
from foodwise.ml.features import FeatureSpec, Inputs, build
from foodwise.ml.selection import assert_time_ordered, blend, make_folds, run_candidates, score
from foodwise.sim.world import CENTRAL_CANTEEN, DISHES, NORTH_MESS, World

TODAY = date(2026, 10, 12)


@lru_cache(maxsize=4)
def golden(canteen_key: str, days: int = 240) -> tuple[Inputs, pd.DataFrame]:
    """History logged by hand-planning kitchen staff in the simulated college, with ground truth."""
    c = {"north_mess": NORTH_MESS, "central_canteen": CENTRAL_CANTEEN}[canteen_key]
    w = World(TODAY, history_days=days)
    rows, last = [], {}
    d = w.start
    while d < TODAY:
        for meal, dishes in w.menu(c, d).items():
            ff = w.footfall(c, d, meal)
            for dk in dishes:
                dem = w.demand(c, d, meal, dk, ff, dishes)
                lw = last.get((meal, dk, (d - timedelta(days=7)).toordinal()))
                prep = w.manual_prep(c, d, meal, dk, dishes, lw[0] if lw else None, bool(lw and lw[1]))
                served = min(dem, prep)
                rows.append(dict(service_date=d, meal=meal, dish_id=dk, served=served, prepared=prep,
                                 sold_out=dem > prep + 0.5, footfall=ff, true_demand=dem))
                last[(meal, dk, d.toordinal())] = (served, dem > prep + 0.5)
        d += timedelta(days=1)
    df = pd.DataFrame(rows)
    cal = pd.DataFrame([dict(day=e.day, kind=e.kind, meal=e.meal, expected_attendance=float(e.attendance or 0),
                             disruption=e.disruption) for e in w.events if e.canteen in (None, c.key)])
    weather = pd.DataFrame([dict(day=x, temp_c=w.weather(x)[0], rain_prob=w.weather(x)[1])
                            for x in pd.date_range(w.start, TODAY + timedelta(days=3)).date])
    inputs = Inputs(
        rows=df.drop(columns=["true_demand"]), dishes=pd.DataFrame([dict(dish_id=x.key, course=x.course, diet=x.diet) for x in DISHES]),
        calendar=cal, weather=weather, rsvp=pd.DataFrame(columns=["service_date", "meal", "eating", "skipping"]),
        feedback=pd.DataFrame(columns=["service_date", "dish_id", "rating", "weight", "portion"]),
        expected_diners=c.diners, term_starts=[pd.Timestamp(e.day) for e in w.events if e.kind == "term_start"],
    )
    return inputs, df


FEATURES_TO_CHECK = ["dish_last", "dish_mean4", "dish_ewm", "dish_lag7", "dish_dow_mean", "dish_share4",
                     "footfall_mean7", "footfall_mean28", "footfall_lag1", "footfall_lag7", "footfall_dow_mean",
                     "days_since_served"]


@pytest.mark.parametrize("cutoff_offset", [30, 75, 140])
def test_features_never_read_the_future(cutoff_offset):
    """Leakage guard: perturbing or deleting every row on or after day d must not change the features of d."""
    inputs, _ = golden("north_mess", 160)
    rows = inputs.rows
    cutoff = pd.Timestamp(TODAY - timedelta(days=cutoff_offset))
    base = build(inputs)
    target = (pd.to_datetime(base["service_date"]) == cutoff).to_numpy()
    assert target.any()

    rng = np.random.default_rng(cutoff_offset)
    future = pd.to_datetime(rows["service_date"]) >= cutoff
    perturbed = rows.astype({"served": float, "footfall": float})
    perturbed.loc[future, "served"] = rng.uniform(0, 5000, future.sum())
    perturbed.loc[future, "footfall"] = rng.uniform(0, 5000, future.sum())
    # Keep the cutoff day's own rows present but with unknown outcomes (as at forecast time).
    perturbed.loc[pd.to_datetime(perturbed["service_date"]) == cutoff, ["served", "footfall"]] = np.nan
    after = build(Inputs(**{**inputs.__dict__, "rows": perturbed}))
    later_dropped = rows[pd.to_datetime(rows["service_date"]) <= cutoff].copy()
    truncated = build(Inputs(**{**inputs.__dict__, "rows": later_dropped}))

    for col in FEATURES_TO_CHECK:
        a = base.loc[target, col].to_numpy(dtype=float)
        b = after.loc[target, col].to_numpy(dtype=float)
        c = truncated.loc[pd.to_datetime(truncated["service_date"]) == cutoff, col].to_numpy(dtype=float)
        np.testing.assert_allclose(a, b, err_msg=f"{col} changed when future rows changed")
        np.testing.assert_allclose(a, c, err_msg=f"{col} changed when future rows were removed")


def test_walk_forward_folds_are_time_ordered():
    dates = pd.Series(pd.date_range("2026-01-01", periods=200, freq="D"))
    folds = make_folds(dates, n_folds=4, horizon_days=14, min_train_days=28)
    assert len(folds) == 4
    assert_time_ordered(folds, dates)
    assert folds[-1].test_end == dates.max() + pd.Timedelta(days=1)


def test_leakage_guard_rejects_random_splits():
    from foodwise.ml.selection import Fold

    dates = pd.Series(pd.date_range("2026-01-01", periods=60, freq="D"))
    overlapping = [Fold(0, dates[30], dates[45]), Fold(1, dates[40], dates[55])]
    with pytest.raises(AssertionError):
        assert_time_ordered(overlapping, dates)


def test_conformal_intervals_hit_nominal_coverage_on_exchangeable_data():
    rng = np.random.default_rng(3)
    yhat = rng.uniform(50, 400, 4000)
    y = yhat * np.exp(rng.normal(0, 0.12, 4000))
    cal = Calibrator.fit(y[:2000], yhat[:2000], np.array(["lunch"] * 2000))
    q = cal.quantiles(yhat[2000:], np.array(["lunch"] * 2000))
    cover = np.mean((y[2000:] >= q[:, 1]) & (y[2000:] <= q[:, 9]))
    assert 0.76 <= cover <= 0.84
    assert np.all(np.diff(q, axis=1) >= 0)


def test_thin_evidence_gives_wide_intervals():
    cal = Calibrator.fit([], [], [])
    q = cal.quantiles(np.array([100.0]), np.array(["lunch"]))[0]
    assert q[1] < 75 and q[9] > 135


@pytest.mark.slow
@pytest.mark.parametrize("canteen", ["north_mess", "central_canteen"])
def test_golden_dataset_learned_models_beat_baselines(canteen):
    """ML regression: on the simulated college, the best learned model must beat the best baseline by a
    clear margin, stay unbiased, and keep intervals near nominal coverage."""
    inputs, _ = golden(canteen)
    frame = build(inputs)
    frame = frame[frame["demand"].notna()].reset_index(drop=True)
    folds = make_folds(frame["service_date"], 4, 14, 28)
    names = zoo.candidates_for_tier(3, True)
    results = run_candidates(frame, names, folds, FeatureSpec(), 300)
    ens = blend(results, frame, folds)
    if ens:
        results["ensemble"] = ens
    cost = np.full(len(frame), 30.0)
    scored = {n: score(r, frame, folds, 0.8, cost, 4.0) for n, r in results.items() if r.error is None}
    best_base = min(scored[n].metrics["wape"] for n in zoo.BASELINES if n in scored)
    learned = {n: s for n, s in scored.items() if n not in zoo.BASELINES}
    best_name = min(learned, key=lambda n: learned[n].metrics["wape"])
    best = learned[best_name].metrics
    assert best["wape"] <= best_base * 0.92, f"{best_name} {best['wape']:.3f} vs baseline {best_base:.3f}"
    assert abs(best["bias"]) <= 0.05
    assert 0.7 <= best["coverage80"] <= 0.9


def test_models_recover_the_planted_rain_effect_at_the_canteen():
    """Central Canteen lunch drops about 15% when it rains (planted in the world). A linear model's
    counterfactual explanation for a rainy lunch must point the same way."""
    from foodwise.ml.explain import drivers

    inputs, _ = golden("central_canteen")
    frame = build(inputs)
    hist = frame[frame["demand"].notna()].reset_index(drop=True)
    model = zoo.make("ridge").fit(hist, np.ones(len(hist)))
    lunch = hist[(hist["meal"] == "lunch") & (hist["rain_prob"] >= 0.7) & (hist["is_vacation"] == 0)].head(40).reset_index(drop=True)
    assert len(lunch) > 5
    effects = [d["effect"] for row in drivers(model, lunch, model.predict(lunch)) for d in row if d["key"] == "rain"]
    assert effects and np.median(effects) < -0.03

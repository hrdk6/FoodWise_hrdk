"""Real-data benchmark pipeline, run on a small file shaped like the Genpact dataset with planted problems."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from foodwise.benchmark import genpact


def _write(tmp_path, weeks: int = 60) -> None:
    rng = np.random.default_rng(7)
    centres = [10, 11, 12, 13, 14, 15]
    meals = [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008]
    rows, rid = [], 1_000_000
    for c in centres:
        for m in meals:
            level = rng.uniform(80, 900)
            price = rng.uniform(120, 400)
            for w in range(1, weeks + 1):
                if rng.random() < 0.08:  # not offered this week
                    continue
                promo = int(rng.random() < 0.12)
                checkout = price * (0.8 if promo else rng.uniform(0.97, 1.03))
                orders = level * (1 + 0.15 * np.sin(w / 8)) * (2.2 if promo else 1.0) * np.exp(rng.normal(0, 0.12))
                rows.append([rid, w, c, m, round(checkout, 2), round(price, 2), promo, 0, int(orders)])
                rid += 1
    train = pd.DataFrame(rows, columns=genpact.COLUMNS["train"]).astype(object)
    planted = [
        train.iloc[5].tolist(), train.iloc[6].tolist(), train.iloc[7].tolist(),       # 3 exact duplicates
        [9_999_999, *train.iloc[8].tolist()[1:-1], 1],                                 # repeats a (week, centre, meal)
    ]
    train = pd.concat([train, pd.DataFrame(planted, columns=train.columns)], ignore_index=True)
    train.loc[20, "num_orders"] = None                    # missing target
    train.loc[21, "num_orders"] = -40                     # impossible
    train.loc[22, "checkout_price"] = 0                   # impossible
    train.loc[23, "meal_id"] = 4242                       # not in meal_info
    train.loc[24, "homepage_featured"] = 2                # not a flag
    train.loc[25, "base_price"] = "abc"                   # not a number
    train.loc[26, "emailer_for_promotion"] = None         # missing flag, filled with 0
    train.to_csv(tmp_path / "train.csv", index=False)
    pd.DataFrame({"meal_id": meals, "category": [" rice  bowl ", "Pizza", "Beverages", "Pasta", "Soup", "Salad",
                                                 "Starters", "Desert"],
                  "cuisine": ["Indian", "Italian", "Thai", "Italian", "Thai", "Continental", "Indian", "indian"]}
                 ).to_csv(tmp_path / "meal_info.csv", index=False)
    pd.DataFrame({"center_id": centres, "city_code": [500 + i for i in range(6)], "region_code": [56, 56, 34, 34, 77, 77],
                  "center_type": ["TYPE_A", "type_b ", "TYPE_C", "TYPE_A", "TYPE_B", "TYPE_C"],
                  "op_area": [3.7, 6.7, 4.0, None, 3.6, 4.4]}).to_csv(tmp_path / "fulfilment_center_info.csv", index=False)


def test_missing_files_are_reported(tmp_path):
    assert set(genpact.missing_files(tmp_path)) == set(genpact.FILES.values())
    with pytest.raises(genpact.BenchmarkError):
        genpact.load(tmp_path)


def test_cleaning_catches_each_planted_problem(tmp_path):
    _write(tmp_path)
    df, steps = genpact.clean(genpact.load(tmp_path))
    by = {s["key"]: s["counts"] for s in steps}
    assert by["missing"]["rows_dropped"] == 1 and by["missing"]["flags_filled"] == 1
    assert by["duplicates"]["exact"] == 3 and by["duplicates"]["same_key"] == 1
    inv = by["invalid"]
    assert inv["negative_orders"] == 1 and inv["price_not_positive"] == 1 and inv["unknown_meal"] == 1
    assert inv["flag_not_0_or_1"] == 1 and inv["unparsable"] == 1
    assert inv["labels_tidied"] == 3
    assert "Rice Bowl" in set(df["category"]) and set(df["center_type"]) == {"TYPE_A", "TYPE_B", "TYPE_C"}
    assert not df.duplicated(subset=["week", "center_id", "meal_id"]).any()
    assert df["num_orders"].ge(0).all() and df.isna().sum().sum() == 0


def test_outlier_conclusion_follows_the_data(tmp_path):
    _write(tmp_path)
    df, steps = genpact.clean(genpact.load(tmp_path))
    assert steps[4]["counts"]["promo_driven"] and "coincide with promotions" in steps[4]["findings"][1]
    # The same spikes with the promotion flags wiped: the report must not credit promotions any more.
    step = genpact.investigate_outliers(df.assign(emailer_for_promotion=0))
    assert not step["counts"]["promo_driven"] and "only part" in step["findings"][1]


def test_features_never_see_the_future(tmp_path):
    _write(tmp_path)
    df, _ = genpact.clean(genpact.load(tmp_path))
    assert genpact.leakage_check(df, cutoff=40)
    frame = genpact.build_features(df)
    # lag1 is last calendar week's orders when the meal was on sale then.
    row = frame[(frame["n_prev"] > 3) & (frame["weeks_since_last"] == 1)].iloc[0]
    prev = df[(df["center_id"] == row.center_id) & (df["meal_id"] == row.meal_id) & (df["week"] == row.week - 1)]
    assert row.lag1 == prev["num_orders"].iloc[0]


def test_benchmark_runs_and_learned_models_beat_the_baseline(tmp_path):
    _write(tmp_path)
    report, df = genpact.run(tmp_path)
    assert [s["n"] for s in report["steps"]] == [1, 2, 3, 4, 5, 6, 7]
    assert report["steps"][-1]["passed"]
    assert len(report["folds"]) == genpact.FOLDS
    assert report["folds"][0]["test_weeks"] == [41, 45] and report["folds"][0]["train_weeks"][1] == 40
    board = {r["model"]: r for r in report["leaderboard"]}
    assert report["winner"] not in ("naive", "moving_average")
    assert board[report["winner"]]["wape"] < board["naive"]["wape"]
    assert report["rows_clean"] == len(df)

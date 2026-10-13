"""Plain-language reason codes for a forecast.

Model-agnostic counterfactuals: for each driver that is active on the day (rain, exams, an event,
a weekend, RSVPs, the dish's rating), switch it to a neutral value, re-predict with the same model,
and report the change. The same method works for every model in the zoo, so a manager sees
explanations in one consistent form whichever algorithm is champion.

    Rain forecast -6%, exam week -9%, rated 4.3/5 +4%, 1,040 RSVPs
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from foodwise.ml.features import DOW_NAMES, neutral_overrides
from foodwise.ml.zoo import ZooModel

MIN_EFFECT = 0.015


def _active(group: str, row: pd.Series) -> bool:
    return {
        "rain": row.get("rain_prob", 0) >= 0.45,
        "exam": row.get("is_exam", 0) > 0,
        "holiday": row.get("is_holiday", 0) > 0 or row.get("is_vacation", 0) > 0,
        "event": row.get("event_size", 0) > 0 or row.get("is_fest", 0) > 0,
        "disruption": row.get("disruption", 0) > 0,
        "weekday": row.get("dow", 2) not in (1, 2, 3),
        "rsvp": row.get("rsvp_known", 0) > 0,
        "rating": "dish_score" in row and row.get("feedback_n", 0) >= 5,
        "month_end": row.get("is_month_end", 0) > 0,
    }.get(group, False)


def _label(group: str, row: pd.Series) -> str:
    if group == "rain":
        return f"Rain forecast ({row['rain_prob']:.0%})"
    if group == "exam":
        return "Exam week"
    if group == "holiday":
        return "Vacation" if row.get("is_vacation", 0) > 0 else "Holiday"
    if group == "event":
        return "Fest on campus" if row.get("is_fest", 0) > 0 else "Campus event"
    if group == "disruption":
        return "Disruption reported"
    if group == "weekday":
        return DOW_NAMES[int(row["dow"])]
    if group == "rsvp":
        n = int(round(row["rsvp_eating_rate"] * row["expected_diners"]))
        return f"{n:,} RSVPs"
    if group == "rating":
        return f"Rated {row['dish_score']:.1f}/5"
    if group == "month_end":
        return "Month end"
    return group


def drivers(model: ZooModel, frame: pd.DataFrame, base: np.ndarray, top: int = 4) -> list[list[dict]]:
    out: list[list[dict]] = [[] for _ in range(len(frame))]
    if len(frame) == 0:
        return out
    overrides = neutral_overrides(frame)
    for group, values in overrides.items():
        cols = {k: v for k, v in values.items() if k in frame.columns}
        if not cols:
            continue
        active = np.array([_active(group, row) for _, row in frame.iterrows()])
        if not active.any():
            continue
        cf = frame.copy()
        for col, v in cols.items():
            cf.loc[active, col] = v
        try:
            pred_cf = model.predict(cf)
        except Exception:  # noqa: BLE001 - explanations must never block a forecast
            continue
        for i in np.flatnonzero(active):
            if pred_cf[i] <= 0:
                continue
            effect = base[i] / pred_cf[i] - 1.0
            if abs(effect) >= MIN_EFFECT:
                out[i].append({"key": group, "label": _label(group, frame.iloc[i]), "effect": round(float(effect), 4)})
    for i in range(len(out)):
        out[i] = sorted(out[i], key=lambda d: -abs(d["effect"]))[:top]
    return out


def summary_line(drivers_: list[dict]) -> str:
    parts = []
    for d in drivers_:
        pct = round(d["effect"] * 100)
        parts.append(f"{d['label']} {'+' if pct > 0 else '−'}{abs(pct)}%")
    return ", ".join(parts)

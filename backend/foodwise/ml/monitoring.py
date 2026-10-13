"""Live model monitoring: accuracy against actuals, error drift, data drift, and shadow decisions.

* Error drift: Page-Hinkley test on daily log-errors of the champion's stored forecasts.
* Data drift: population stability index (PSI) of key inputs, training window vs the last 28 days.
* Shadow: a challenger is promoted only if it beats the champion on the same live days.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from foodwise.db.models import CalendarDay, Forecast, ModelVersion, ServiceLog
from foodwise.ml import registry
from foodwise.ml.training import promote


def psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    expected, actual = np.asarray(expected, float), np.asarray(actual, float)
    expected, actual = expected[np.isfinite(expected)], actual[np.isfinite(actual)]
    if len(expected) < 20 or len(actual) < 10:
        return 0.0
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return float(np.sum((a - e) * np.log(a / e)))


def page_hinkley(x: np.ndarray, delta: float = 0.02, threshold: float = 0.6) -> int | None:
    """Index where a sustained upward shift in x is detected, or None."""
    mean, cum, min_cum = 0.0, 0.0, 0.0
    for i, v in enumerate(x, start=1):
        mean += (v - mean) / i
        cum += v - mean - delta
        min_cum = min(min_cum, cum)
        if cum - min_cum > threshold:
            return i - 1
    return None


def tracked(db: Session, canteen_id: uuid.UUID, since: date, kind: str = "model",
            model_version_id: uuid.UUID | None = None) -> pd.DataFrame:
    """Stored forecasts joined to what actually happened."""
    q = (
        select(Forecast.service_date, Forecast.meal, Forecast.dish_id, Forecast.p50, Forecast.p10, Forecast.p90,
               Forecast.recommended_qty, Forecast.status, Forecast.override_qty, ServiceLog.served_qty,
               ServiceLog.prepared_qty, ServiceLog.sold_out)
        .join(ServiceLog, and_(
            ServiceLog.canteen_id == Forecast.canteen_id, ServiceLog.service_date == Forecast.service_date,
            ServiceLog.meal == Forecast.meal, ServiceLog.dish_id == Forecast.dish_id,
        ))
        .where(Forecast.canteen_id == canteen_id, Forecast.service_date >= since, Forecast.kind == kind)
    )
    if model_version_id is not None:
        q = q.where(Forecast.model_version_id == model_version_id)
    cols = ["service_date", "meal", "dish_id", "p50", "p10", "p90", "recommended", "status", "override_qty",
            "actual", "prepared", "sold_out"]
    return pd.DataFrame(db.execute(q).all(), columns=cols)


def evaluate_canteen(db: Session, canteen_id: uuid.UUID, today: date) -> dict:
    champ = registry.champion(db, canteen_id)
    if champ is None:
        return {"status": "no_model"}
    since = today - timedelta(days=56)
    df = tracked(db, canteen_id, since)
    df = df[~df["sold_out"]]
    result: dict = {"checked_at": datetime.now(UTC).isoformat(), "status": "ok", "alerts": []}
    if len(df) >= 20:
        recent = df[df["service_date"] >= today - timedelta(days=28)]
        y, p = recent["actual"].to_numpy(float), recent["p50"].to_numpy(float)
        if y.sum() > 0:
            result["live_wape"] = round(float(np.abs(y - p).sum() / y.sum()), 4)
            result["live_bias"] = round(float((p - y).sum() / y.sum()), 4)
            result["live_coverage80"] = round(float(np.mean((y >= recent["p10"]) & (y <= recent["p90"]))), 4)
        daily = (
            df.assign(err=np.abs(np.log((df["actual"] + 1) / (df["p50"] + 1))))
            .groupby("service_date")["err"].mean().sort_index()
        )
        alarm = page_hinkley(daily.to_numpy())
        if alarm is not None and alarm >= len(daily) - 14:
            result["status"] = "degraded"
            result["alerts"].append(f"Forecast error rose from {daily.index[alarm]} (Page-Hinkley test).")
        trained_wape = (champ.metrics or {}).get("wape")
        if trained_wape and result.get("live_wape") and result["live_wape"] > trained_wape * 1.5:
            result["status"] = "degraded"
            result["alerts"].append(
                f"Live error {result['live_wape']:.0%} is well above the backtest's {trained_wape:.0%}."
            )
        if abs(result.get("live_bias", 0)) > 0.08:
            result["alerts"].append(f"Forecasts are running {result['live_bias']:+.0%} against actuals.")
    # Service level: the plan cooks at the critical ratio, so dishes should run short on roughly
    # (1 - ratio) of servings. Running short much more often means the ranges are too narrow.
    everything = tracked(db, canteen_id, today - timedelta(days=28))
    if len(everything) >= 20:
        target = 1 - float((champ.metrics or {}).get("critical_ratio", 0.8))
        observed = float(everything["sold_out"].mean())
        result["shortage_target"] = round(target, 3)
        result["shortage_observed"] = round(observed, 3)
        if observed > target + 0.05:
            result["alerts"].append(
                f"Ran short on {observed:.0%} of dishes against a {target:.0%} target. Ranges are too narrow on "
                "recent days; raise the service level or retrain."
            )

    # Data drift on inputs the model leans on.
    window = champ.data_window or {}
    if window.get("from") and window.get("to"):
        logs = pd.DataFrame(
            db.execute(
                select(ServiceLog.service_date, ServiceLog.meal, ServiceLog.footfall).where(
                    ServiceLog.canteen_id == canteen_id, ServiceLog.service_date >= date.fromisoformat(window["from"])
                )
            ).all(),
            columns=["service_date", "meal", "footfall"],
        ).drop_duplicates(["service_date", "meal"])
        train_end = date.fromisoformat(window["to"])
        cut = today - timedelta(days=28)
        # Reference: the last eight term-time weeks the model trained on. Comparing against the whole
        # window would flag every change of season or the end of a vacation as drift.
        vacation = {d for (d,) in db.execute(select(CalendarDay.day).where(CalendarDay.kind == "vacation")).all()}
        drift = {}
        if len(logs):
            logs = logs[~logs["service_date"].isin(vacation)]
            ref = logs[(logs["service_date"] > train_end - timedelta(days=56)) & (logs["service_date"] <= train_end)]
            recent = logs[logs["service_date"] > cut]
            scale = ref.groupby("meal")["footfall"].median()
            norm = lambda d: (d["footfall"] / d["meal"].map(scale)).to_numpy(float)  # noqa: E731
            if len(ref) >= 20 and len(recent) >= 10:
                drift["footfall"] = round(psi(norm(ref), norm(recent)), 3)
        result["psi"] = drift
        for name, value in drift.items():
            if value > 0.25:
                result["alerts"].append(f"Input drift in {name} (PSI {value:.2f}). Retraining is recommended.")
                if result["status"] == "ok":
                    result["status"] = "drift"
    return result


def evaluate_shadow(db: Session, canteen_id: uuid.UUID, today: date) -> dict:
    ch = registry.challenger(db, canteen_id)
    champ = registry.champion(db, canteen_id)
    if ch is None or champ is None:
        return {"decision": "none"}
    started = date.fromisoformat((ch.shadow or {}).get("started", today.isoformat()))
    need = int((ch.shadow or {}).get("days", 7))
    shadow = tracked(db, canteen_id, started, kind="shadow", model_version_id=ch.id)
    live = tracked(db, canteen_id, started, kind="model")
    keys = ["service_date", "meal", "dish_id"]
    both = shadow.merge(live[[*keys, "p50"]], on=keys, suffixes=("_ch", "_champ"))
    both = both[~both["sold_out"]]
    days = both["service_date"].nunique()
    if days < need:
        ch.shadow = {**(ch.shadow or {}), "days_observed": int(days)}
        return {"decision": "pending", "days_observed": int(days), "days_needed": need}
    y = both["actual"].to_numpy(float)
    w_ch = float(np.abs(y - both["p50_ch"]).sum() / y.sum())
    w_champ = float(np.abs(y - both["p50_champ"]).sum() / y.sum())
    ch.shadow = {**(ch.shadow or {}), "days_observed": int(days), "wape_challenger": round(w_ch, 4),
                 "wape_champion": round(w_champ, 4)}
    if w_ch <= w_champ:
        promote(db, ch, f"Won the {days}-day shadow period: {w_ch:.1%} vs {w_champ:.1%} error.")
        return {"decision": "promoted", "wape_challenger": w_ch, "wape_champion": w_champ}
    ch.stage = "rejected"
    ch.shadow = {**ch.shadow, "decision": f"Lost the shadow period: {w_ch:.1%} vs {w_champ:.1%} error.",
                 "decided_at": datetime.now(UTC).isoformat()}
    return {"decision": "rejected", "wape_challenger": w_ch, "wape_champion": w_champ}


def rollback(db: Session, canteen_id: uuid.UUID) -> ModelVersion | None:
    """Degraded champion -> retired; the most recent retired version comes back as champion."""
    champ = registry.champion(db, canteen_id)
    if champ is None:
        return None
    previous = db.execute(
        select(ModelVersion)
        .where(ModelVersion.canteen_id == canteen_id, ModelVersion.stage == "retired", ModelVersion.id != champ.id)
        .order_by(ModelVersion.retired_at.desc().nullslast())
        .limit(1)
    ).scalar_one_or_none()
    if previous is None:
        return None
    promote(db, previous, f"Rolled back from v{champ.version}, which had degraded.")
    return previous

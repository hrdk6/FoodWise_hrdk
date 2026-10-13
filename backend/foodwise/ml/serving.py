"""Generate forecasts for a canteen and day: quantiles, the cost-optimal quantity, and reasons.

If the champion cannot be loaded or fails, the plan falls back to a clearly labelled statistical
baseline (weighted recent mean with intervals from its own recent errors). It is never presented
as a model forecast.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from foodwise.db.models import Canteen, Forecast, ModelVersion, Tenant
from foodwise.domain.newsvendor import (
    QUANTILE_LEVELS,
    critical_ratio,
    expected_waste_and_shortage,
    quantile_at,
    round_for_unit,
)
from foodwise.domain.settings import effective_settings
from foodwise.ml import explain, registry, zoo
from foodwise.ml.conformal import Calibrator, calibration_groups
from foodwise.ml.data import dishes_frame, history_frame, inputs_for, menu_frame
from foodwise.ml.features import FeatureSpec, build
from foodwise.ml.training import canteen_costs
from foodwise.services.weather import ensure_weather

log = logging.getLogger("foodwise.serving")


@dataclass
class Scenario:
    """What-if adjustments applied on top of the real context for the day."""

    headcount_pct: float = 0.0
    rain_prob: float | None = None
    temp_c: float | None = None
    exam: bool | None = None
    holiday: bool | None = None
    event_attendance: int | None = None
    disruption: bool | None = None
    menu: dict[str, list[str]] | None = None  # meal -> dish ids
    shortage_to_waste_ratio: float | None = None


@dataclass
class Prediction:
    meal: str
    dish_id: str
    unit: str
    quantiles: list[float]
    recommended: float
    expected_waste: float
    expected_shortage: float
    drivers: list[dict]
    context: dict
    kind: str
    algorithm: str
    model_version_id: uuid.UUID | None
    critical_ratio: float
    extra: dict = field(default_factory=dict)


def _baseline_calibrator(hist_frame: pd.DataFrame) -> Calibrator:
    recent = hist_frame[hist_frame["service_date"] >= hist_frame["service_date"].max() - pd.Timedelta(days=60)]
    recent = recent[recent["demand"].notna() & ~recent["sold_out"]]
    return Calibrator.fit(recent["demand"].to_numpy(), recent["dish_ewm"].to_numpy(), calibration_groups(recent))


def _apply_scenario(frame: pd.DataFrame, target: np.ndarray, sc: Scenario, diners: int) -> pd.DataFrame:
    f = frame.copy()
    if sc.rain_prob is not None:
        f.loc[target, "rain_prob"] = sc.rain_prob
        f.loc[target, "is_rainy"] = float(sc.rain_prob >= 0.6)
    if sc.temp_c is not None:
        f.loc[target, "temp_c"] = sc.temp_c
    if sc.exam is not None:
        f.loc[target, "is_exam"] = float(sc.exam)
    if sc.holiday is not None:
        f.loc[target, "is_holiday"] = float(sc.holiday)
    if sc.event_attendance is not None:
        f.loc[target, "event_size"] = sc.event_attendance / max(1, diners)
        f.loc[target, "is_fest"] = float(sc.event_attendance >= diners * 0.5)
    if sc.disruption is not None:
        f.loc[target, "disruption"] = float(sc.disruption)
    return f


def predict_day(
    db: Session,
    tenant: Tenant,
    canteen: Canteen,
    day: date,
    *,
    meals: list[str] | None = None,
    model_version: ModelVersion | None = None,
    scenario: Scenario | None = None,
    explain_drivers: bool = True,
) -> list[Prediction]:
    settings = effective_settings(tenant.settings)
    costs = canteen_costs(settings, canteen)
    ratio = scenario.shortage_to_waste_ratio if scenario and scenario.shortage_to_waste_ratio else costs["shortage_to_waste_ratio"]
    cr = critical_ratio(ratio)

    if scenario and scenario.menu:
        rows = []
        for meal, dish_ids in scenario.menu.items():
            rows += [{"service_date": pd.Timestamp(day), "meal": meal, "dish_id": d} for d in dish_ids]
        target_rows = pd.DataFrame(rows, columns=["service_date", "meal", "dish_id"])
        for col in ["served", "prepared", "footfall"]:
            target_rows[col] = np.nan
        target_rows["sold_out"] = False
    else:
        target_rows = menu_frame(db, canteen.id, day, meals)
    if target_rows.empty:
        return []

    hist = history_frame(db, canteen.id, before=day)
    ensure_weather(db, tenant.id, canteen, day, tenant.timezone)

    mv = model_version if model_version is not None else registry.champion(db, canteen.id)
    kind, artifact = "model", None
    if mv is not None:
        try:
            artifact = registry.load_artifact(mv.artifact_uri)
        except Exception as exc:  # noqa: BLE001 - fall back rather than fail the plan
            log.error("champion artifact unavailable, serving baseline: %s", exc)
            kind = "baseline"
    else:
        kind = "baseline" if len(hist) >= 40 else "prior"
    if artifact is not None and artifact.algorithm == "rule_prior":
        kind = "prior"
    spec = artifact.spec if artifact is not None else FeatureSpec()

    all_rows = pd.concat([hist, target_rows], ignore_index=True)
    all_rows["_target"] = np.r_[np.zeros(len(hist), dtype=bool), np.ones(len(target_rows), dtype=bool)]
    start = (pd.Timestamp(day) - pd.Timedelta(days=420)).date()
    inputs = inputs_for(db, canteen, all_rows.drop(columns=["_target"]), start, day)
    frame = build(inputs, spec)
    target = all_rows["_target"].to_numpy()
    if scenario:
        frame = _apply_scenario(frame, target, scenario, canteen.expected_diners)
    tf = frame[target].reset_index(drop=True)

    if kind == "model":
        model, calibrator, algorithm = artifact.model, artifact.calibrator, artifact.algorithm
        try:
            model.observe(frame)
            p50 = model.predict(tf)
        except Exception as exc:  # noqa: BLE001
            log.error("champion failed to predict, serving baseline: %s", exc)
            kind = "baseline"
    if kind == "baseline":
        model, algorithm = zoo.make("weighted_mean"), "weighted_mean"
        calibrator = _baseline_calibrator(frame[~target])
        p50 = model.predict(tf)
    elif kind == "prior":
        model, algorithm = zoo.make("rule_prior"), "rule_prior"
        calibrator = Calibrator.fit([], [], [])
        p50 = model.predict(tf)

    q = calibrator.quantiles(p50, calibration_groups(tf))
    if scenario and scenario.headcount_pct:
        q = q * (1 + scenario.headcount_pct)
        p50 = p50 * (1 + scenario.headcount_pct)
    drivers = explain.drivers(model, tf, p50) if explain_drivers and kind == "model" else [[] for _ in range(len(tf))]

    dishes = dishes_frame(db).set_index("dish_id")
    out = []
    for i, row in tf.iterrows():
        unit = str(dishes.loc[row["dish_id"], "unit"]) if row["dish_id"] in dishes.index else "plates"
        values = [round(float(v), 2) for v in q[i]]
        rec = round_for_unit(quantile_at(QUANTILE_LEVELS, values, cr), unit)
        waste, short = expected_waste_and_shortage(QUANTILE_LEVELS, values, rec)
        out.append(Prediction(
            meal=row["meal"],
            dish_id=row["dish_id"],
            unit=unit,
            quantiles=values,
            recommended=rec,
            expected_waste=round(waste, 2),
            expected_shortage=round(short, 2),
            drivers=drivers[i],
            context={
                "rain_prob": round(float(row["rain_prob"]), 2),
                "temp_c": round(float(row["temp_c"]), 1),
                "is_exam": bool(row["is_exam"]),
                "is_holiday": bool(row["is_holiday"] or row["is_vacation"]),
                "event": bool(row["event_size"] > 0 or row["is_fest"] > 0),
                "rsvp_eating": int(round(row["rsvp_eating_rate"] * row["expected_diners"])) if row["rsvp_known"] else None,
                "last_served": None if pd.isna(row["dish_last"]) else round(float(row["dish_last"]), 1),
                "last_week": None if pd.isna(row["dish_lag7"]) else round(float(row["dish_lag7"]), 1),
                "footfall_mean7": round(float(row["footfall_mean7"]), 0),
                "summary": explain.summary_line(drivers[i]),
            },
            kind=kind,
            algorithm=algorithm,
            model_version_id=mv.id if (mv is not None and kind != "baseline") else None,
            critical_ratio=round(cr, 4),
        ))
    return out


def generate(
    db: Session,
    tenant: Tenant,
    canteen: Canteen,
    day: date,
    *,
    meals: list[str] | None = None,
    include_shadow: bool = True,
) -> list[Forecast]:
    """Create or refresh the stored plan for a day. Human decisions are preserved; if an approved
    quantity moves by more than 5%, the forecast goes back to proposed and is flagged."""
    preds = predict_day(db, tenant, canteen, day, meals=meals)
    saved = _upsert(db, tenant.id, canteen.id, day, preds)
    if include_shadow:
        ch = registry.challenger(db, canteen.id)
        if ch is not None:
            shadow = predict_day(db, tenant, canteen, day, meals=meals, model_version=ch, explain_drivers=False)
            for p in shadow:
                p.kind = "shadow"
            _upsert(db, tenant.id, canteen.id, day, shadow)
    return saved


def _upsert(db: Session, tenant_id: uuid.UUID, canteen_id: uuid.UUID, day: date, preds: list[Prediction]) -> list[Forecast]:
    if not preds:
        return []
    kinds = {"shadow"} if preds[0].kind == "shadow" else {"model", "baseline", "prior"}
    existing = {
        (f.meal, str(f.dish_id)): f
        for f in db.execute(
            select(Forecast).where(Forecast.canteen_id == canteen_id, Forecast.service_date == day,
                                   Forecast.kind.in_(kinds))
        ).scalars()
    }
    now = datetime.now(UTC)
    out = []
    for p in preds:
        f = existing.get((p.meal, p.dish_id))
        if f is None:
            f = Forecast(tenant_id=tenant_id, canteen_id=canteen_id, service_date=day, meal=p.meal,
                         dish_id=uuid.UUID(p.dish_id), status="proposed")
            db.add(f)
        elif f.status == "approved" and abs(p.recommended - f.recommended_qty) > 0.05 * max(f.recommended_qty, 1):
            f.status = "proposed"
            f.changed_after_approval = True
        f.kind = p.kind
        f.model_version_id = p.model_version_id
        f.algorithm = p.algorithm
        f.unit = p.unit
        f.levels = list(QUANTILE_LEVELS)
        f.quantiles = p.quantiles
        f.p10 = quantile_at(QUANTILE_LEVELS, p.quantiles, 0.1)
        f.p50 = quantile_at(QUANTILE_LEVELS, p.quantiles, 0.5)
        f.p90 = quantile_at(QUANTILE_LEVELS, p.quantiles, 0.9)
        f.critical_ratio = p.critical_ratio
        f.recommended_qty = p.recommended
        f.drivers = p.drivers
        f.context = {**p.context, "expected_waste": p.expected_waste, "expected_shortage": p.expected_shortage}
        f.generated_at = now
        out.append(f)
    db.flush()
    return out


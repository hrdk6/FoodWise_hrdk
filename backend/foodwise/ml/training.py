"""Training pipeline for one canteen.

    validated logs -> as-of features -> walk-forward folds -> candidate zoo -> ensemble
    -> conformal calibration -> promotion gates -> register (champion / challenger / rejected)

The first model a canteen gets becomes champion directly (there is nothing to shadow against).
Later winners become challengers that must also win a live shadow period before promotion.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from foodwise.config import get_settings
from foodwise.core.timeutil import local_today
from foodwise.db.base import tenant_session
from foodwise.db.models import Canteen, ModelVersion, Tenant
from foodwise.domain.newsvendor import QUANTILE_LEVELS, critical_ratio
from foodwise.domain.readiness import readiness
from foodwise.domain.settings import effective_settings
from foodwise.ml import registry, zoo
from foodwise.ml.conformal import Calibrator
from foodwise.ml.data import dishes_frame, history_frame, inputs_for
from foodwise.ml.features import FeatureSpec, build
from foodwise.ml.selection import (
    Scored,
    blend,
    check_gates,
    make_folds,
    recommended,
    run_candidates,
    sample_weights,
    score,
)

log = logging.getLogger("foodwise.ml")
Progress = Callable[[str, float], None]


def canteen_costs(tenant_settings: dict, canteen: Canteen) -> dict:
    costs = dict(tenant_settings["costs"])
    costs.update(canteen.costs or {})
    return costs


def _fit_final(name: str, frame: pd.DataFrame, spec: FeatureSpec, members: list[str] | None,
               weights: list[float] | None) -> zoo.ZooModel:
    w = sample_weights(frame)
    if name == "ensemble":
        fitted = [zoo.make(m, spec).fit(frame, w) for m in members or []]
        ens = zoo.Ensemble("ensemble", spec, members=fitted, weights=list(weights or []))
        return ens
    return zoo.make(name, spec).fit(frame, w)


def _backtest_series(frame: pd.DataFrame, oof: np.ndarray, q: np.ndarray | None, cr: float,
                     kg: np.ndarray) -> list[dict]:
    """Daily totals in kg (dishes are logged in plates, kg and pieces; kg is the common unit)."""
    mask = np.isfinite(oof) & frame["demand"].notna().to_numpy()
    if not mask.any():
        return []
    df = pd.DataFrame({
        "date": frame["service_date"].to_numpy()[mask],
        "actual": (frame["demand"].to_numpy(dtype=float) * kg)[mask],
        "p50": (oof * kg)[mask],
        "prepared": (frame["prepared"].to_numpy(dtype=float) * kg)[mask],
    })
    if q is not None:
        qm = q[mask]
        has = np.isfinite(qm[:, 0])
        rec = np.full(len(qm), np.nan)
        rec[has] = recommended(qm[has], cr)
        df["p10"] = qm[:, QUANTILE_LEVELS.index(0.1)] * kg[mask]
        df["p90"] = qm[:, QUANTILE_LEVELS.index(0.9)] * kg[mask]
        df["recommended"] = rec * kg[mask]
    daily = df.groupby("date").sum(min_count=1).reset_index()
    out = []
    for _, r in daily.iterrows():
        out.append({k: (r[k].date().isoformat() if k == "date" else (None if pd.isna(r[k]) else round(float(r[k]), 1)))
                    for k in daily.columns})
    return out


def train_canteen(
    tenant_id: uuid.UUID,
    canteen_id: uuid.UUID,
    *,
    as_of: date | None = None,
    trigger: str = "manual",
    progress: Progress | None = None,
) -> dict:
    t_start = time.perf_counter()
    budget = get_settings().training_time_budget_s

    def step(label: str, frac: float) -> None:
        if progress:
            progress(label, frac)

    with tenant_session(tenant_id) as db:
        tenant = db.get(Tenant, tenant_id)
        canteen = db.get(Canteen, canteen_id)
        if tenant is None or canteen is None:
            raise ValueError("canteen not found for tenant")
        settings = effective_settings(tenant.settings)
        as_of = as_of or local_today(tenant.timezone) + timedelta(days=1)
        costs = canteen_costs(settings, canteen)
        cr = critical_ratio(costs["shortage_to_waste_ratio"])
        step("Loading history", 0.05)

        hist = history_frame(db, canteen_id, before=as_of)
        days = int(hist["service_date"].nunique()) if len(hist) else 0
        tier = readiness(days, settings["tiers"]).tier
        version = registry.next_version(db, canteen_id)
        current = registry.champion(db, canteen_id)

        if tier == 0 or len(hist) < 40:
            return _register_prior(db, tenant_id, canteen, version, tier, days, current)

        step("Building features", 0.12)
        start = hist["service_date"].min().date()
        inputs = inputs_for(db, canteen, hist, start, as_of)
        spec = FeatureSpec(use_feedback=False)
        frame = build(inputs, spec)
        frame = frame[frame["demand"].notna()].reset_index(drop=True)
        has_footfall = bool(frame["footfall"].notna().mean() > 0.8)
        folds = make_folds(
            frame["service_date"],
            n_folds=4 if tier >= 2 else 2,
            horizon_days=14 if tier >= 2 else 7,
            min_train_days=28 if tier >= 2 else 10,
        )
        if not folds:
            return _register_prior(db, tenant_id, canteen, version, tier, days, current)

        dishes = dishes_frame(db).set_index("dish_id")
        cost_per_unit = frame["dish_id"].map(dishes["cost_per_unit"]).fillna(30.0).to_numpy(dtype=float)
        kg = frame["dish_id"].map(dishes["portion_kg"]).fillna(0.3).to_numpy(dtype=float)
        ratio = float(costs["shortage_to_waste_ratio"])

        names = zoo.candidates_for_tier(tier, has_footfall)
        step(f"Backtesting {len(names)} models", 0.2)
        results = run_candidates(frame, names, folds, spec, budget)
        if tier >= 2:
            step("Blending an ensemble", 0.7)
            ens = blend(results, frame, folds)
            if ens is not None:
                results["ensemble"] = ens

        step("Scoring and calibrating", 0.78)
        scored: dict[str, Scored] = {
            n: score(r, frame, folds, cr, cost_per_unit, ratio)
            for n, r in results.items()
            if r.error is None and not r.skipped
        }
        baselines = [s for n, s in scored.items() if n in zoo.BASELINES]
        best_baseline = min(baselines, key=lambda s: s.metrics["wape"])
        champ_scored = scored.get(current.algorithm) if current else None

        gates: dict[str, list[dict]] = {}
        passing: list[str] = []
        for name, s in scored.items():
            if name in zoo.BASELINES:
                continue
            gates[name] = check_gates(s, best_baseline, champ_scored, settings["models"], tier_allows=True)
            if all(g["passed"] for g in gates[name]):
                passing.append(name)
        winner = min(passing, key=lambda n: scored[n].metrics["wape"]) if passing else None

        ablation = None
        if tier >= 3 and winner:
            step("Testing feedback features", 0.84)
            ablation, spec, frame, scored = _feedback_ablation(
                inputs, winner, results, scored, folds, cr, cost_per_unit, ratio, budget, spec, frame
            )

        chosen = winner or (None if current else best_baseline.name)
        best_learned = min(
            (n for n in scored if n not in zoo.BASELINES), key=lambda n: scored[n].metrics["wape"], default=None
        )
        record_name = chosen or best_learned or best_baseline.name
        res = results[record_name]

        step("Fitting the final model", 0.9)
        model = _fit_final(record_name, frame, spec, res.members, res.weights)
        artifact = registry.Artifact(record_name, model, spec, scored[record_name].calibrator)
        uri = registry.save_artifact(tenant_id, canteen_id, version, artifact)

        now = datetime.now(UTC)
        if chosen and current is None:
            stage, promoted = "champion", now
        elif winner:
            stage, promoted = "challenger", None
        else:
            stage, promoted = "rejected", None

        leaderboard = []
        for name, r in results.items():
            s = scored.get(name)
            status = (
                "skipped" if r.skipped else "failed" if r.error else
                "baseline" if name in zoo.BASELINES else
                "selected" if name == record_name and stage != "rejected" else
                "passed" if name in passing else "failed_gates"
            )
            leaderboard.append({
                "algorithm": name,
                "label": zoo.LABELS.get(name, name),
                "family": zoo.FAMILIES.get(name, "other"),
                "status": status,
                "metrics": s.metrics if s else {},
                "fold_wape": s.fold_wape if s else [],
                "fit_seconds": r.fit_seconds,
                "gates": gates.get(name, []),
                "error": r.error,
                "members": r.members,
                "weights": r.weights,
            })
        leaderboard.sort(key=lambda e: (e["metrics"].get("wape") is None, e["metrics"].get("wape") or 9))

        sc = scored[record_name]
        mv = ModelVersion(
            tenant_id=tenant_id,
            canteen_id=canteen_id,
            version=version,
            algorithm=record_name,
            framing="two_stage" if record_name in ("two_stage", "holt_winters") else "direct",
            stage=stage,
            tier=tier,
            metrics={**sc.metrics, "fold_wape": sc.fold_wape, "critical_ratio": round(cr, 3),
                     "best_baseline": best_baseline.name, "best_baseline_wape": best_baseline.metrics["wape"],
                     "skill": round(1 - sc.metrics["wape"] / best_baseline.metrics["wape"], 4)
                     if best_baseline.metrics["wape"] else None,
                     "by_meal": _by_meal(frame, res.oof),
                     "tradeoff": _tradeoff(frame, sc.q)},
            leaderboard=leaderboard,
            gates=gates.get(record_name, []),
            backtest={"series": _backtest_series(frame, res.oof, sc.q, cr, kg), "unit": "kg"},
            feature_list=spec.columns,
            feature_hash=spec.hash(),
            data_window={
                "from": frame["service_date"].min().date().isoformat(),
                "to": frame["service_date"].max().date().isoformat(),
                "rows": len(frame),
                "days": days,
                "as_of": as_of.isoformat(),
                "folds": [{"test_start": f.test_start.date().isoformat(),
                           "test_end": (f.test_end - pd.Timedelta(days=1)).date().isoformat()} for f in folds],
                "trigger": trigger,
            },
            library_versions=registry.library_versions(),
            feedback_ablation=ablation,
            seed=42,
            artifact_uri=uri,
            training_seconds=round(time.perf_counter() - t_start, 1),
            shadow={"started": as_of.isoformat(), "days": settings["models"]["shadow_days"]} if stage == "challenger" else None,
            promoted_at=promoted,
        )
        db.add(mv)
        db.commit()
        step("Done", 1.0)
        log.info("trained %s v%d for canteen %s: %s (%s)", record_name, version, canteen.name, stage,
                 sc.metrics.get("wape"))
        return {"model_version_id": str(mv.id), "version": version, "algorithm": record_name, "stage": stage,
                "wape": sc.metrics.get("wape"), "tier": tier, "seconds": mv.training_seconds}


TRADEOFF_LEVELS = (0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95)


def _tradeoff(frame: pd.DataFrame, q: np.ndarray | None) -> list[dict]:
    """What each service level would have cost on this canteen's own backtest: waste versus
    how often, and by how much, the kitchen would have run short."""
    if q is None:
        return []
    ok = np.isfinite(q[:, 0]) & frame["demand"].notna().to_numpy() & ~frame["sold_out"].to_numpy(dtype=bool)
    if ok.sum() < 30:
        return []
    y = frame["demand"].to_numpy(dtype=float)[ok]
    prepared = frame["prepared"].to_numpy(dtype=float)[ok]
    out = [{"level": None, "label": "As logged", "waste_pct": round(float(np.maximum(prepared - y, 0).sum() / y.sum()), 4),
            "short_rows_pct": None, "short_units_pct": None}]
    for level in TRADEOFF_LEVELS:
        rec = np.ceil(recommended(q[ok], level) - 1e-9)
        out.append({
            "level": level,
            "label": f"P{round(level * 100)}",
            "waste_pct": round(float(np.maximum(rec - y, 0).sum() / y.sum()), 4),
            "short_rows_pct": round(float(np.mean(y > rec + 0.5)), 4),
            "short_units_pct": round(float(np.maximum(y - rec, 0).sum() / y.sum()), 4),
        })
    return out


def _by_meal(frame: pd.DataFrame, oof: np.ndarray) -> dict[str, float]:
    out = {}
    ok = np.isfinite(oof) & ~frame["sold_out"].to_numpy(dtype=bool)
    for meal in frame["meal"].unique():
        m = ok & (frame["meal"] == meal).to_numpy()
        y = frame["demand"].to_numpy(dtype=float)[m]
        if y.sum() > 0:
            out[str(meal)] = round(float(np.abs(y - oof[m]).sum() / y.sum()), 4)
    return out


def _feedback_ablation(inputs, winner, results, scored, folds, cr, cost_per_unit, ratio, budget, spec, frame):
    """Earn-your-place rule: feedback features stay only if they measurably reduce error."""
    spec_fb = FeatureSpec(use_feedback=True)
    frame_fb = build(inputs, spec_fb)
    frame_fb = frame_fb[frame_fb["demand"].notna()].reset_index(drop=True)
    names = results[winner].members if winner == "ensemble" else [winner]
    res_fb = run_candidates(frame_fb, list(names or []), folds, spec_fb, budget)
    cand = blend(res_fb, frame_fb, folds) if winner == "ensemble" else res_fb.get(winner)
    if cand is None or cand.error:
        return {"tested": False, "message": "Feedback features could not be tested this run."}, spec, frame, scored
    s_fb = score(cand, frame_fb, folds, cr, cost_per_unit, ratio)
    before, after = scored[winner].metrics["wape"], s_fb.metrics["wape"]
    delta = after / before - 1 if before else 0.0
    kept = delta <= -0.005
    ablation = {
        "tested": True,
        "wape_without": before,
        "wape_with": after,
        "delta": round(delta, 4),
        "kept": kept,
        "message": (
            f"Feedback features kept: they cut error by {abs(delta):.1%}."
            if kept else "No measurable impact yet, so feedback features are left out of this model."
        ),
    }
    if kept:
        results[winner] = cand
        scored = dict(scored)
        scored[winner] = s_fb
        return ablation, spec_fb, frame_fb, scored
    return ablation, spec, frame, scored


def _register_prior(db: Session, tenant_id, canteen: Canteen, version: int, tier: int, days: int,
                    current: ModelVersion | None) -> dict:
    """Tier 0: a labelled rule-based plan. Never presented as a learned model."""
    if current is not None:
        return {"stage": "unchanged", "tier": tier, "message": "Not enough history to train yet."}
    spec = FeatureSpec()
    artifact = registry.Artifact("rule_prior", zoo.make("rule_prior", spec), spec, Calibrator.fit([], [], []))
    uri = registry.save_artifact(tenant_id, canteen.id, version, artifact)
    mv = ModelVersion(
        tenant_id=tenant_id, canteen_id=canteen.id, version=version, algorithm="rule_prior", framing="rule",
        stage="champion", tier=tier, metrics={"note": "Learning: rule-based plan until 14 days of logs."},
        leaderboard=[], gates=[], backtest={}, feature_list=spec.columns, feature_hash=spec.hash(),
        data_window={"days": days}, library_versions=registry.library_versions(), artifact_uri=uri,
        promoted_at=datetime.now(UTC),
    )
    db.add(mv)
    db.commit()
    return {"model_version_id": str(mv.id), "version": version, "algorithm": "rule_prior", "stage": "champion",
            "tier": tier}


def promote(db: Session, mv: ModelVersion, reason: str) -> None:
    now = datetime.now(UTC)
    current = registry.champion(db, mv.canteen_id)
    if current is not None and current.id != mv.id:
        current.stage = "retired"
        current.retired_at = now
    mv.stage = "champion"
    mv.promoted_at = now
    mv.shadow = {**(mv.shadow or {}), "decision": reason, "decided_at": now.isoformat()}

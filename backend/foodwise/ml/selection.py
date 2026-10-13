"""Walk-forward model selection, honest metrics and promotion gates.

* Splits are expanding-window and strictly time-ordered (`assert_time_ordered` is the leakage
  guard; a test fails if any split is not).
* Every candidate is scored on the same folds. Interval metrics on fold k use a calibrator fitted
  only on folds before k, so coverage is measured out of sample too.
* Sold-out rows are right-censored: excluded from error metrics (their "served" understates
  demand) and counted separately.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import nnls

from foodwise.domain.newsvendor import QUANTILE_LEVELS, quantile_at
from foodwise.ml import zoo
from foodwise.ml.conformal import Calibrator, calibration_groups
from foodwise.ml.features import FeatureSpec

log = logging.getLogger("foodwise.ml")


@dataclass(frozen=True)
class Fold:
    index: int
    test_start: pd.Timestamp
    test_end: pd.Timestamp  # exclusive

    def train_mask(self, dates: pd.Series) -> np.ndarray:
        return (dates < self.test_start).to_numpy()

    def test_mask(self, dates: pd.Series) -> np.ndarray:
        return ((dates >= self.test_start) & (dates < self.test_end)).to_numpy()


def make_folds(dates: pd.Series, n_folds: int, horizon_days: int, min_train_days: int) -> list[Fold]:
    end = pd.Timestamp(dates.max()) + pd.Timedelta(days=1)
    unique = pd.Series(sorted(pd.to_datetime(dates.unique())))
    folds = []
    for k in range(n_folds, 0, -1):
        start = end - pd.Timedelta(days=horizon_days * k)
        if (unique < start).sum() < min_train_days:
            continue
        folds.append(Fold(len(folds), start, start + pd.Timedelta(days=horizon_days)))
    assert_time_ordered(folds, dates)
    return folds


def assert_time_ordered(folds: list[Fold], dates: pd.Series) -> None:
    """Leakage guard: training data always precedes test data, and folds move forward in time."""
    for f in folds:
        train = dates[f.train_mask(dates)]
        test = dates[f.test_mask(dates)]
        if len(train) and len(test) and train.max() >= test.min():
            raise AssertionError(f"fold {f.index} trains on data at or after its test window")
    for a, b in zip(folds, folds[1:], strict=False):
        if not (a.test_start < b.test_start and a.test_end <= b.test_start):
            raise AssertionError("folds must be ordered and non-overlapping")


# --------------------------------------------------------------------------- metrics


def wape(y: np.ndarray, p: np.ndarray) -> float:
    denom = np.sum(np.abs(y))
    return float(np.sum(np.abs(y - p)) / denom) if denom > 0 else float("nan")


def bias(y: np.ndarray, p: np.ndarray) -> float:
    denom = np.sum(y)
    return float(np.sum(p - y) / denom) if denom > 0 else float("nan")


def scaled_pinball(y: np.ndarray, q: np.ndarray, levels=QUANTILE_LEVELS) -> float:
    losses = []
    for j, tau in enumerate(levels):
        diff = y - q[:, j]
        losses.append(np.sum(np.maximum(tau * diff, (tau - 1) * diff)))
    denom = np.sum(np.abs(y))
    return float(np.mean(losses) / denom) if denom > 0 else float("nan")


def coverage(y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    return float(np.mean((y >= lo) & (y <= hi))) if len(y) else float("nan")


def recommended(q: np.ndarray, cr: float, levels=QUANTILE_LEVELS) -> np.ndarray:
    return np.array([quantile_at(levels, row, cr) for row in q])


def bootstrap_reduction(days: np.ndarray, waste_model: np.ndarray, waste_manual: np.ndarray, n: int = 400,
                        seed: int = 7) -> dict:
    """Waste reduction vs manual preparation with a day-level bootstrap interval."""
    df = pd.DataFrame({"d": days, "m": waste_model, "h": waste_manual}).groupby("d").sum()
    if df["h"].sum() <= 0 or len(df) < 5:
        return {"point": None, "lo": None, "hi": None, "days": len(df)}
    rng = np.random.default_rng(seed)
    point = 1 - df["m"].sum() / df["h"].sum()
    samples = []
    m, h = df["m"].to_numpy(), df["h"].to_numpy()
    for _ in range(n):
        idx = rng.integers(0, len(df), len(df))
        hs = h[idx].sum()
        if hs > 0:
            samples.append(1 - m[idx].sum() / hs)
    lo, hi = np.percentile(samples, [5, 95])
    return {"point": round(float(point), 4), "lo": round(float(lo), 4), "hi": round(float(hi), 4), "days": len(df)}


# --------------------------------------------------------------------------- evaluation


@dataclass
class CandidateResult:
    name: str
    oof: np.ndarray
    fold_wape: list[float] = field(default_factory=list)
    fit_seconds: float = 0.0
    error: str | None = None
    skipped: bool = False
    weights: list[float] | None = None
    members: list[str] | None = None


def sample_weights(frame: pd.DataFrame, half_life_days: float = 150.0) -> np.ndarray:
    age = (frame["service_date"].max() - frame["service_date"]).dt.days.to_numpy(dtype=float)
    w = 0.5 ** (age / half_life_days)
    # Imputed (sold-out) targets are estimates, so they count for less.
    return np.where(frame["sold_out"].to_numpy(dtype=bool), 0.5 * w, w)


def run_candidates(
    frame: pd.DataFrame,
    names: list[str],
    folds: list[Fold],
    spec: FeatureSpec,
    budget_s: float,
) -> dict[str, CandidateResult]:
    t_start = time.perf_counter()
    dates = frame["service_date"]
    y_known = frame["demand"].notna().to_numpy()
    results: dict[str, CandidateResult] = {}
    for name in names:
        res = CandidateResult(name, np.full(len(frame), np.nan))
        if time.perf_counter() - t_start > budget_s and name not in zoo.BASELINES:
            res.skipped = True
            res.error = "skipped: training time budget reached"
            results[name] = res
            continue
        t0 = time.perf_counter()
        try:
            for fold in folds:
                train = fold.train_mask(dates) & y_known
                test = fold.test_mask(dates)
                model = zoo.make(name, spec)
                tr = frame[train]
                model.fit(tr, sample_weights(tr))
                model.observe(frame[fold.train_mask(dates) | test])
                res.oof[test] = model.predict(frame[test])
            for fold in folds:
                mask = fold.test_mask(dates) & y_known & ~frame["sold_out"].to_numpy(dtype=bool)
                res.fold_wape.append(wape(frame["demand"].to_numpy()[mask], res.oof[mask]))
        except Exception as exc:  # noqa: BLE001 - one bad candidate must not sink the run
            log.warning("candidate %s failed: %s", name, exc)
            res.error = str(exc)[:300]
        res.fit_seconds = round(time.perf_counter() - t0, 2)
        results[name] = res
    return results


def blend(results: dict[str, CandidateResult], frame: pd.DataFrame, folds: list[Fold], top: int = 3) -> CandidateResult | None:
    """Non-negative blend of the best learned models. Weights for fold k are fitted on earlier
    folds only, so the blend's out-of-fold score is honest."""
    dates = frame["service_date"]
    uncensored = frame["demand"].notna().to_numpy() & ~frame["sold_out"].to_numpy(dtype=bool)
    ranked = sorted(
        (r for r in results.values() if r.error is None and not r.skipped and r.name not in zoo.BASELINES),
        key=lambda r: np.nanmean(r.fold_wape),
    )[:top]
    if len(ranked) < 2 or len(folds) < 2:
        return None
    y = frame["demand"].to_numpy(dtype=float)
    oof = np.full(len(frame), np.nan)
    fold_wape = []
    for fold in folds:
        test = fold.test_mask(dates)
        prev = np.zeros(len(frame), dtype=bool)
        for f in folds:
            if f.test_end <= fold.test_start:
                prev |= f.test_mask(dates)
        w = _nnls_weights([r.oof for r in ranked], y, prev & uncensored)
        oof[test] = np.sum([wi * r.oof[test] for wi, r in zip(w, ranked, strict=True)], axis=0)
        m = test & uncensored
        fold_wape.append(wape(y[m], oof[m]))
    all_mask = np.zeros(len(frame), dtype=bool)
    for f in folds:
        all_mask |= f.test_mask(dates)
    final_w = _nnls_weights([r.oof for r in ranked], y, all_mask & uncensored)
    return CandidateResult("ensemble", oof, fold_wape, sum(r.fit_seconds for r in ranked), weights=final_w,
                           members=[r.name for r in ranked])


def _nnls_weights(preds: list[np.ndarray], y: np.ndarray, mask: np.ndarray) -> list[float]:
    if mask.sum() < 20:
        return [1.0 / len(preds)] * len(preds)
    a = np.column_stack([p[mask] for p in preds])
    w, _ = nnls(a, y[mask])
    if w.sum() <= 0:
        return [1.0 / len(preds)] * len(preds)
    w = w / w.sum()
    # Shrink toward equal weights when evidence is thin, so a short history cannot overfit the blend.
    trust = min(1.0, mask.sum() / 1500)
    w = trust * w + (1 - trust) / len(preds)
    return [round(float(x), 4) for x in w]


@dataclass
class Scored:
    name: str
    metrics: dict
    fold_wape: list[float]
    calibrator: Calibrator
    q: np.ndarray | None = None  # out-of-fold quantile grid (NaN where no earlier fold calibrates it)


def score(
    res: CandidateResult,
    frame: pd.DataFrame,
    folds: list[Fold],
    cr: float,
    cost_per_unit: np.ndarray,
    shortage_ratio: float,
) -> Scored:
    dates = frame["service_date"]
    y = frame["demand"].to_numpy(dtype=float)
    meals = calibration_groups(frame)
    known = frame["demand"].notna().to_numpy()
    censored = frame["sold_out"].to_numpy(dtype=bool)
    in_folds = np.zeros(len(frame), dtype=bool)
    for f in folds:
        in_folds |= f.test_mask(dates)
    ok = in_folds & known & ~censored & np.isfinite(res.oof)

    # Interval metrics: fold k is calibrated on folds before k.
    q_all = np.full((len(frame), len(QUANTILE_LEVELS)), np.nan)
    for i, fold in enumerate(folds):
        if i == 0:
            continue
        prev = np.zeros(len(frame), dtype=bool)
        for f in folds[:i]:
            prev |= f.test_mask(dates)
        cal = Calibrator.fit(y[prev & ok], res.oof[prev & ok], meals[prev & ok])
        test = fold.test_mask(dates) & np.isfinite(res.oof)
        q_all[test] = cal.quantiles(res.oof[test], meals[test])
    iv = ok & np.isfinite(q_all[:, 0])
    holdout = folds[-1].test_mask(dates) & ok

    metrics: dict = {
        "wape": round(wape(y[ok], res.oof[ok]), 4),
        "holdout_wape": round(wape(y[holdout], res.oof[holdout]), 4) if holdout.any() else None,
        "bias": round(bias(y[ok], res.oof[ok]), 4),
        "rows": int(ok.sum()),
        "censored_rows": int((in_folds & censored).sum()),
    }
    if iv.any():
        q = q_all[iv]
        rec = recommended(q, cr)
        yy = y[iv]
        cost = cost_per_unit[iv]
        prepared = frame["prepared"].to_numpy(dtype=float)[iv]
        waste_model = np.maximum(rec - yy, 0)
        short_model = np.maximum(yy - rec, 0)
        waste_manual = np.maximum(prepared - yy, 0)
        short_manual = np.maximum(yy - prepared, 0)
        i10, i90 = QUANTILE_LEVELS.index(0.1), QUANTILE_LEVELS.index(0.9)
        metrics.update(
            {
                "coverage80": round(coverage(yy, q[:, i10], q[:, i90]), 4),
                "pinball": round(scaled_pinball(yy, q), 4),
                "sim_cost": round(float(np.sum(cost * (waste_model + shortage_ratio * short_model))), 1),
                "manual_cost": round(float(np.sum(cost * (waste_manual + shortage_ratio * short_manual))), 1),
                "waste_units": round(float(waste_model.sum()), 1),
                "manual_waste_units": round(float(waste_manual.sum()), 1),
                "shortage_rate": round(float(np.mean(short_model > 0.5)), 4),
                "manual_shortage_rate": round(float(np.mean(short_manual > 0.5)), 4),
                "waste_reduction": bootstrap_reduction(
                    dates.to_numpy()[iv], waste_model * cost, waste_manual * cost
                ),
            }
        )
    calibrator = Calibrator.fit(y[ok], res.oof[ok], meals[ok])
    return Scored(res.name, metrics, [round(x, 4) for x in res.fold_wape], calibrator, q_all)


def check_gates(
    cand: Scored,
    best_baseline: Scored,
    champion: Scored | None,
    cfg: dict,
    tier_allows: bool,
) -> list[dict]:
    m, b = cand.metrics, best_baseline.metrics
    gates = []
    skill = 1 - m["wape"] / b["wape"] if b["wape"] else 0.0
    gates.append({"gate": "beats_baseline", "passed": skill >= cfg["min_skill_vs_baseline"],
                  "detail": f"{skill:+.1%} WAPE vs {best_baseline.name} (needs {cfg['min_skill_vs_baseline']:+.0%})"})
    if champion is not None and champion.name != cand.name:
        cw, cm = champion.metrics["wape"], float(np.nanmedian(champion.fold_wape))
        ok = m["wape"] <= cw and float(np.nanmedian(cand.fold_wape)) <= cm
        gates.append({"gate": "beats_champion", "passed": ok,
                      "detail": f"WAPE {m['wape']:.1%} vs champion {cw:.1%}"})
    cov = m.get("coverage80")
    tol = cfg["coverage_tolerance_pp"] / 100
    gates.append({"gate": "calibrated", "passed": cov is not None and abs(cov - 0.8) <= tol,
                  "detail": "not enough folds" if cov is None else f"80% interval covered {cov:.0%} (target 80% +- {tol:.0%})"})
    gates.append({"gate": "unbiased", "passed": abs(m["bias"]) <= cfg["max_abs_bias"],
                  "detail": f"bias {m['bias']:+.1%} (limit +-{cfg['max_abs_bias']:.0%})"})
    if champion is not None and "sim_cost" in m and "sim_cost" in champion.metrics and champion.name != cand.name:
        gates.append({"gate": "cheaper", "passed": m["sim_cost"] <= champion.metrics["sim_cost"],
                      "detail": f"simulated cost {m['sim_cost']:,.0f} vs champion {champion.metrics['sim_cost']:,.0f}"})
    gates.append({"gate": "enough_history", "passed": tier_allows, "detail": "allowed at this readiness tier" if tier_allows else "needs more history"})
    return gates

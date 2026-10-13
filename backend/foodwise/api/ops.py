"""Admin console: how FoodWise is performing.

For FoodWise engineers (the `admin` account). Everything here is metadata and aggregates across
organisations: model metrics, request latency, the job queue, database health and sign-in
security. No dish logs, feedback text or donation records leave an organisation through here.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, select, text

from foodwise.core import audit, jobs
from foodwise.core.deps import Ctx, client_ip, need
from foodwise.core.errors import Conflict, NotFound
from foodwise.core.ratelimit import limiter
from foodwise.core.telemetry import http
from foodwise.core.timeutil import local_today
from foodwise.db.base import system_session
from foodwise.db.models import (
    AuditLog,
    Canteen,
    Forecast,
    Job,
    ModelVersion,
    ServiceCheck,
    ServiceLog,
    SurplusIncident,
    Tenant,
    User,
    WebVital,
    WorkerHeartbeat,
)
from foodwise.domain.incident_state import ACTIVE
from foodwise.domain.readiness import readiness
from foodwise.domain.settings import effective_settings

router = APIRouter(prefix="/ops", tags=["ops"])
telemetry_router = APIRouter(prefix="/telemetry", tags=["ops"])

WORKER_ALIVE_S = 90
EXTERNAL = {"openstreetmap": "OpenStreetMap places", "wikimedia": "Wikimedia pictures", "open-meteo": "Open-Meteo weather"}
VITAL_LIMITS = {"LCP": (2500, 4000), "INP": (200, 500), "CLS": (0.1, 0.25), "FCP": (1800, 3000), "TTFB": (800, 1800)}


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


# --------------------------------------------------------------------------- overview


class Attention(BaseModel):
    level: Literal["critical", "warning", "info"]
    area: str
    message: str


class MinutePoint(BaseModel):
    at: datetime
    requests: int
    server_errors: int
    client_errors: int
    p50_ms: float | None
    p95_ms: float | None


class ApiSummary(BaseModel):
    requests: int
    server_errors: int
    client_errors: int
    error_rate: float | None
    p50_ms: float | None
    p95_ms: float | None
    requests_per_min: float
    up_since: datetime
    series: list[MinutePoint]


class JobSummary(BaseModel):
    queued: int
    running: int
    dead: int
    succeeded_24h: int
    failed_24h: int
    oldest_queued_s: float | None


class WorkerRow(BaseModel):
    worker_id: str
    alive: bool
    started_at: datetime
    last_seen_at: datetime
    seconds_since_seen: float
    jobs_done: int
    jobs_failed: int
    current_job: str | None


class DbSummary(BaseModel):
    ok: bool
    latency_ms: float
    size_mb: float
    connections: int
    cache_hit: float | None


class ModelSummary(BaseModel):
    canteens: int
    with_champion: int
    degraded: int
    drift: int
    alerts: int
    median_live_wape: float | None
    shadow_running: int


class ExternalRow(BaseModel):
    service: str
    label: str
    calls_24h: int
    failures_24h: int
    failure_rate: float | None
    avg_ms: float | None
    max_ms: float | None
    last_ok_at: datetime | None
    last_error_at: datetime | None
    last_error: str | None


class VitalRow(BaseModel):
    metric: str
    p75: float | None
    samples: int
    good: float | None
    poor: float | None
    rating: Literal["good", "needs-improvement", "poor", "none"]
    good_limit: float
    poor_limit: float


class Overview(BaseModel):
    generated_at: datetime
    attention: list[Attention]
    api: ApiSummary
    jobs: JobSummary
    workers: list[WorkerRow]
    database: DbSummary
    models: ModelSummary
    organisations: int
    canteens: int
    logged_today: int
    forecasts_today: int
    open_surplus: int
    external: list[ExternalRow]
    vitals: list[VitalRow]


def _api_summary(minutes: int) -> ApiSummary:
    series, _, total = http.window(minutes)
    return ApiSummary(
        requests=total.count, server_errors=total.server_errors, client_errors=total.client_errors,
        error_rate=round(total.server_errors / total.count, 4) if total.count else None,
        p50_ms=total.quantile(0.5), p95_ms=total.quantile(0.95), requests_per_min=round(total.count / minutes, 2),
        up_since=http.started_at, series=series,
    )


def _jobs(db) -> JobSummary:
    counts = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    since = datetime.now(UTC) - timedelta(hours=24)
    done = dict(db.execute(select(Job.status, func.count()).where(Job.finished_at >= since).group_by(Job.status)).all())
    oldest = db.execute(select(func.min(Job.run_at)).where(Job.status == "queued", Job.run_at <= func.now())).scalar()
    return JobSummary(queued=counts.get("queued", 0), running=counts.get("running", 0), dead=counts.get("dead", 0),
                      succeeded_24h=done.get("succeeded", 0), failed_24h=done.get("failed", 0) + done.get("dead", 0),
                      oldest_queued_s=round((datetime.now(UTC) - oldest).total_seconds(), 1) if oldest else None)


def _workers(db) -> list[WorkerRow]:
    now = datetime.now(UTC)
    rows = db.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.last_seen_at > now - timedelta(hours=6))
                      .order_by(WorkerHeartbeat.last_seen_at.desc())).scalars()
    return [WorkerRow(worker_id=w.worker_id, alive=(now - w.last_seen_at).total_seconds() < WORKER_ALIVE_S,
                      started_at=w.started_at, last_seen_at=w.last_seen_at,
                      seconds_since_seen=round((now - w.last_seen_at).total_seconds(), 1), jobs_done=w.jobs_done,
                      jobs_failed=w.jobs_failed, current_job=w.current_job) for w in rows]


def _db(db) -> DbSummary:
    t0 = time.perf_counter()
    db.execute(text("SELECT 1"))
    latency = (time.perf_counter() - t0) * 1000
    size = db.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
    conns = db.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")).scalar_one()
    hit = db.execute(text(
        "SELECT sum(blks_hit)::float / NULLIF(sum(blks_hit) + sum(blks_read), 0) FROM pg_stat_database "
        "WHERE datname = current_database()")).scalar()
    return DbSummary(ok=True, latency_ms=round(latency, 2), size_mb=round(size / 1e6, 1), connections=conns,
                     cache_hit=round(hit, 4) if hit is not None else None)


def _external(db) -> list[ExternalRow]:
    since = datetime.now(UTC) - timedelta(hours=24)
    out = []
    for service, label in EXTERNAL.items():
        calls, fails, total_ms, max_ms = db.execute(
            select(func.coalesce(func.sum(ServiceCheck.calls), 0), func.coalesce(func.sum(ServiceCheck.failures), 0),
                   func.coalesce(func.sum(ServiceCheck.total_ms), 0), func.max(ServiceCheck.max_ms))
            .where(ServiceCheck.service == service, ServiceCheck.hour >= since)).one()
        last = db.execute(select(ServiceCheck).where(ServiceCheck.service == service)
                          .order_by(ServiceCheck.hour.desc()).limit(1)).scalar_one_or_none()
        last_ok = db.execute(select(func.max(ServiceCheck.last_ok_at)).where(ServiceCheck.service == service)).scalar()
        last_err = db.execute(select(ServiceCheck.last_error_at, ServiceCheck.last_error).where(
            ServiceCheck.service == service, ServiceCheck.last_error_at.is_not(None))
            .order_by(ServiceCheck.last_error_at.desc()).limit(1)).first()
        out.append(ExternalRow(
            service=service, label=label, calls_24h=int(calls), failures_24h=int(fails),
            failure_rate=round(fails / calls, 3) if calls else None, avg_ms=round(total_ms / calls, 1) if calls else None,
            max_ms=round(max_ms, 1) if max_ms else None, last_ok_at=last_ok or (last.last_ok_at if last else None),
            last_error_at=last_err[0] if last_err else None, last_error=last_err[1] if last_err else None,
        ))
    return out


def _vitals(db, hours: int = 24, route: str | None = None) -> list[VitalRow]:
    since = datetime.now(UTC) - timedelta(hours=hours)
    q = select(WebVital.metric, WebVital.value, WebVital.rating).where(WebVital.at >= since)
    if route:
        q = q.where(WebVital.route == route)
    by: dict[str, list[tuple[float, str]]] = {}
    for metric, value, rating in db.execute(q):
        by.setdefault(metric, []).append((value, rating))
    out = []
    for metric, (good_limit, poor_limit) in VITAL_LIMITS.items():
        vals = by.get(metric, [])
        p75 = _pct([v for v, _ in vals], 0.75)
        rating = "none" if p75 is None else "good" if p75 <= good_limit else "needs-improvement" if p75 <= poor_limit else "poor"
        out.append(VitalRow(metric=metric, p75=p75, samples=len(vals),
                            good=round(sum(r == "good" for _, r in vals) / len(vals), 3) if vals else None,
                            poor=round(sum(r == "poor" for _, r in vals) / len(vals), 3) if vals else None,
                            rating=rating, good_limit=good_limit, poor_limit=poor_limit))
    return out


def _model_rows(db) -> list[dict]:
    """One row per active canteen across organisations: champion, challenger and live health."""
    canteens = db.execute(select(Canteen, Tenant).join(Tenant, Tenant.id == Canteen.tenant_id)
                          .where(Canteen.is_active.is_(True)).order_by(Tenant.name, Canteen.name)).all()
    versions: dict[uuid.UUID, list[ModelVersion]] = {}
    for mv in db.execute(select(ModelVersion).order_by(ModelVersion.version.desc())).scalars():
        versions.setdefault(mv.canteen_id, []).append(mv)
    days = dict(db.execute(select(ServiceLog.canteen_id, func.count(func.distinct(ServiceLog.service_date)))
                           .group_by(ServiceLog.canteen_id)).all())
    last_train = {}
    for j in db.execute(select(Job).where(Job.kind == "train_canteen").order_by(Job.created_at.desc()).limit(400)).scalars():
        cid = (j.payload or {}).get("canteen_id")
        if cid and cid not in last_train:
            last_train[cid] = j
    rows = []
    for c, t in canteens:
        vs = versions.get(c.id, [])
        champ = next((v for v in vs if v.stage in ("champion", "degraded")), None)
        chall = next((v for v in vs if v.stage == "challenger"), None)
        today = local_today(t.timezone)
        fc_today = db.execute(select(func.count()).select_from(Forecast).where(
            Forecast.canteen_id == c.id, Forecast.service_date == today, Forecast.kind != "shadow")).scalar_one()
        fc_tomorrow = db.execute(select(func.count()).select_from(Forecast).where(
            Forecast.canteen_id == c.id, Forecast.service_date == today + timedelta(days=1), Forecast.kind != "shadow")).scalar_one()
        r = readiness(int(days.get(c.id, 0)), effective_settings(t.settings)["tiers"])
        m = (champ.metrics or {}) if champ else {}
        mon = (champ.monitoring or {}) if champ else {}
        job = last_train.get(str(c.id))
        rows.append({
            "tenant": t.name, "tenant_id": str(t.id), "canteen": c.name, "canteen_id": str(c.id), "tier": r.tier,
            "tier_name": r.name, "days": r.days,
            "champion": None if champ is None else {
                "id": str(champ.id), "version": champ.version, "algorithm": champ.algorithm, "stage": champ.stage,
                "trained_at": champ.created_at.isoformat(), "promoted_at": champ.promoted_at.isoformat() if champ.promoted_at else None,
                # Age from the end of the training data (equals the training date outside the demo,
                # where the seed replays months of history in one go).
                "age_days": (today - date.fromisoformat((champ.data_window or {})["as_of"])).days
                if (champ.data_window or {}).get("as_of") else (datetime.now(UTC) - champ.created_at).days,
                "wape": m.get("wape"), "skill": m.get("skill"),
                "coverage80": m.get("coverage80"), "bias": m.get("bias"), "holdout_wape": m.get("holdout_wape"),
                "critical_ratio": m.get("critical_ratio"), "training_seconds": champ.training_seconds,
                "rows": (champ.data_window or {}).get("rows"), "best_baseline": m.get("best_baseline"),
            },
            "monitoring": {
                "status": mon.get("status", "no_model" if champ is None else "unchecked"), "live_wape": mon.get("live_wape"),
                "live_bias": mon.get("live_bias"), "live_coverage80": mon.get("live_coverage80"),
                "shortage_observed": mon.get("shortage_observed"), "shortage_target": mon.get("shortage_target"),
                "psi_footfall": (mon.get("psi") or {}).get("footfall"), "alerts": mon.get("alerts", []),
                "checked_at": mon.get("checked_at"),
            },
            "challenger": None if chall is None else {
                "id": str(chall.id), "version": chall.version, "algorithm": chall.algorithm,
                "wape": (chall.metrics or {}).get("wape"), "days_observed": (chall.shadow or {}).get("days_observed", 0),
                "days_needed": (chall.shadow or {}).get("days", 7),
            },
            "versions": len(vs),
            "last_training": None if job is None else {
                "status": job.status, "at": (job.finished_at or job.created_at).isoformat(),
                "error": (job.last_error or "").split("\n")[0][:200] or None, "trigger": (job.payload or {}).get("trigger"),
            },
            "forecasts_today": fc_today, "forecasts_tomorrow": fc_tomorrow,
        })
    return rows


@router.get("/overview", response_model=Overview)
def overview(ctx: Ctx = Depends(need("ops:read"))) -> Overview:
    attention: list[Attention] = []
    api = _api_summary(60)
    with system_session() as db:
        job = _jobs(db)
        workers = _workers(db)
        dbs = _db(db)
        models = _model_rows(db)
        ext = _external(db)
        vitals = _vitals(db)
        orgs = db.execute(select(func.count()).select_from(Tenant)).scalar_one()
        canteens = db.execute(select(func.count()).select_from(Canteen).where(Canteen.is_active.is_(True))).scalar_one()
        logged_today = db.execute(select(func.count(func.distinct(ServiceLog.canteen_id)))
                                  .where(ServiceLog.created_at >= func.date_trunc("day", func.now()))).scalar_one()
        fc_today = db.execute(select(func.count()).select_from(Forecast)
                              .where(Forecast.generated_at >= func.date_trunc("day", func.now()))).scalar_one()
        open_surplus = db.execute(select(func.count()).select_from(SurplusIncident)
                                  .where(SurplusIncident.status.in_(ACTIVE))).scalar_one()

    if not any(w.alive for w in workers):
        attention.append(Attention(level="critical", area="Workers",
                                   message="No worker has checked in for 90 seconds. Forecasts, training and picture lookups are not running."))
    if job.dead:
        attention.append(Attention(level="warning", area="Jobs", message=f"{job.dead} job{'s' if job.dead > 1 else ''} in the dead-letter queue."))
    if job.oldest_queued_s and job.oldest_queued_s > 300:
        attention.append(Attention(level="warning", area="Jobs", message=f"The oldest queued job has waited {job.oldest_queued_s / 60:.0f} minutes."))
    if api.error_rate and api.error_rate > 0.01:
        attention.append(Attention(level="critical" if api.error_rate > 0.05 else "warning", area="API",
                                   message=f"{api.error_rate:.1%} of requests failed with a server error in the last hour."))
    if api.p95_ms and api.p95_ms > 1500:
        attention.append(Attention(level="warning", area="API", message=f"Slow responses: p95 latency is {api.p95_ms:.0f} ms."))
    for r in models:
        mon = r["monitoring"]
        if mon["status"] in ("degraded", "drift"):
            attention.append(Attention(level="warning", area="Models",
                                       message=f"{r['tenant']}, {r['canteen']}: {mon['status']}. " + " ".join(mon["alerts"][:1])))
        elif mon["alerts"]:
            attention.append(Attention(level="info", area="Models", message=f"{r['tenant']}, {r['canteen']}: {mon['alerts'][0]}"))
        if r["last_training"] and r["last_training"]["status"] == "dead":
            attention.append(Attention(level="warning", area="Models", message=f"{r['tenant']}, {r['canteen']}: last training failed."))
        if r["tier"] >= 1 and r["forecasts_tomorrow"] == 0:
            attention.append(Attention(level="info", area="Forecasts", message=f"{r['tenant']}, {r['canteen']}: no plan for tomorrow yet."))
    for e in ext:
        if e.calls_24h >= 3 and (e.failure_rate or 0) > 0.25:
            attention.append(Attention(level="warning", area="External", message=f"{e.label}: {e.failure_rate:.0%} of calls failed today."))
    for v in vitals:
        if v.rating == "poor":
            attention.append(Attention(level="warning", area="Web", message=f"{v.metric} p75 is {v.p75:g} (poor) for real users."))

    live = [r["monitoring"]["live_wape"] for r in models if r["monitoring"]["live_wape"] is not None]
    msum = ModelSummary(
        canteens=len(models), with_champion=sum(1 for r in models if r["champion"]),
        degraded=sum(1 for r in models if r["monitoring"]["status"] == "degraded"),
        drift=sum(1 for r in models if r["monitoring"]["status"] == "drift"),
        alerts=sum(len(r["monitoring"]["alerts"]) for r in models),
        median_live_wape=round(median(live), 4) if live else None,
        shadow_running=sum(1 for r in models if r["challenger"]),
    )
    order = {"critical": 0, "warning": 1, "info": 2}
    return Overview(generated_at=datetime.now(UTC), attention=sorted(attention, key=lambda a: order[a.level]), api=api,
                    jobs=job, workers=workers, database=dbs, models=msum, organisations=orgs, canteens=canteens,
                    logged_today=logged_today, forecasts_today=fc_today, open_surplus=open_surplus, external=ext,
                    vitals=vitals)


# --------------------------------------------------------------------------- models


class CanteenRef(BaseModel):
    id: str
    name: str
    tenant: str


class ModelsOut(BaseModel):
    rows: list[dict]
    series: list[dict[str, float | str | None]]
    canteens: list[CanteenRef]


@router.get("/models", response_model=ModelsOut)
def models(days: int = Query(30, ge=7, le=120), ctx: Ctx = Depends(need("ops:read"))) -> ModelsOut:
    """Champion health per canteen, and daily live error (WAPE of the median forecast against
    servings, sold-out rows excluded because their demand is censored)."""
    since = date.today() - timedelta(days=days)
    with system_session() as db:
        rows = _model_rows(db)
        daily = db.execute(
            select(Forecast.canteen_id, Forecast.service_date,
                   func.sum(func.abs(ServiceLog.served_qty - Forecast.p50)), func.sum(ServiceLog.served_qty),
                   func.avg(case((and_(ServiceLog.served_qty >= Forecast.p10, ServiceLog.served_qty <= Forecast.p90), 1.0),
                                 else_=0.0)),
                   func.count())
            .join(ServiceLog, and_(ServiceLog.canteen_id == Forecast.canteen_id, ServiceLog.service_date == Forecast.service_date,
                                   ServiceLog.meal == Forecast.meal, ServiceLog.dish_id == Forecast.dish_id))
            .where(Forecast.service_date >= since, Forecast.kind == "model", ServiceLog.sold_out.is_(False))
            .group_by(Forecast.canteen_id, Forecast.service_date).order_by(Forecast.service_date)
        ).all()
    names = {r["canteen_id"]: f"{r['canteen']}" for r in rows}
    by_day: dict[str, dict] = {}
    for cid, day, abs_err, served, cover, _n in daily:
        if not served:
            continue
        d = by_day.setdefault(day.isoformat(), {"date": day.isoformat()})
        d[str(cid)] = round(float(abs_err) / float(served), 4)
        d[f"{cid}:coverage"] = round(float(cover or 0), 3)
    canteens = [{"id": r["canteen_id"], "name": names[r["canteen_id"]], "tenant": r["tenant"]} for r in rows
                if any(r["canteen_id"] in d for d in by_day.values())]
    return ModelsOut(rows=rows, series=[by_day[k] for k in sorted(by_day)], canteens=canteens)


class VersionOut(BaseModel):
    id: uuid.UUID
    tenant: str
    canteen: str
    version: int
    algorithm: str
    stage: str
    tier: int
    created_at: datetime
    promoted_at: datetime | None
    training_seconds: float
    metrics: dict
    leaderboard: list[dict]
    gates: list[dict]
    monitoring: dict | None
    shadow: dict | None
    data_window: dict
    feature_count: int
    library_versions: dict


@router.get("/models/versions/{version_id}", response_model=VersionOut)
def version(version_id: uuid.UUID, ctx: Ctx = Depends(need("ops:read"))) -> VersionOut:
    with system_session() as db:
        mv = db.get(ModelVersion, version_id)
        if mv is None:
            raise NotFound("Model version not found.")
        c = db.get(Canteen, mv.canteen_id)
        t = db.get(Tenant, mv.tenant_id)
        metrics = {k: v for k, v in (mv.metrics or {}).items() if k != "tradeoff"}
        return VersionOut(id=mv.id, tenant=t.name, canteen=c.name, version=mv.version, algorithm=mv.algorithm,
                          stage=mv.stage, tier=mv.tier, created_at=mv.created_at, promoted_at=mv.promoted_at,
                          training_seconds=mv.training_seconds, metrics=metrics, leaderboard=mv.leaderboard or [],
                          gates=mv.gates or [], monitoring=mv.monitoring, shadow=mv.shadow, data_window=mv.data_window or {},
                          feature_count=len(mv.feature_list or []), library_versions=mv.library_versions or {})


class QueuedJob(BaseModel):
    job_id: uuid.UUID
    message: str


@router.post("/models/{canteen_id}/retrain", response_model=QueuedJob, status_code=202)
def retrain(canteen_id: uuid.UUID, ctx: Ctx = Depends(need("ops:manage"))) -> QueuedJob:
    with system_session() as db:
        c = db.get(Canteen, canteen_id)
        if c is None:
            raise NotFound("Canteen not found.")
        job_id = jobs.enqueue(db, "train_canteen", {"canteen_id": str(c.id), "trigger": "requested by FoodWise engineering"},
                              tenant_id=c.tenant_id, dedupe_key=f"train:{c.id}")
        # The organisation sees in its own audit trail that an engineer started this.
        audit.record(db, ctx.principal, "model.retrain_requested", tenant_id=c.tenant_id, target_type="canteen",
                     target_id=c.id)
        db.commit()
        return QueuedJob(job_id=job_id, message=f"Retraining queued for {c.name}.")


@router.post("/models/{canteen_id}/monitor", response_model=QueuedJob, status_code=202)
def monitor_now(canteen_id: uuid.UUID, ctx: Ctx = Depends(need("ops:manage"))) -> QueuedJob:
    with system_session() as db:
        c = db.get(Canteen, canteen_id)
        if c is None:
            raise NotFound("Canteen not found.")
        job_id = jobs.enqueue(db, "monitor_canteen", {"canteen_id": str(c.id)}, tenant_id=c.tenant_id,
                              dedupe_key=f"monitor-now:{c.id}")
        db.commit()
        return QueuedJob(job_id=job_id, message=f"Health check queued for {c.name}.")


# --------------------------------------------------------------------------- API traffic


class RouteRow(BaseModel):
    route: str
    requests: int
    server_errors: int
    client_errors: int
    error_rate: float
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    max_ms: float


class RequestNote(BaseModel):
    at: datetime
    route: str
    status: int
    ms: float
    request_id: str


class ApiOut(BaseModel):
    minutes: int
    summary: ApiSummary
    routes: list[RouteRow]
    errors: list[RequestNote]
    slow: list[RequestNote]


@router.get("/api", response_model=ApiOut)
def api_traffic(minutes: int = Query(60, ge=5, le=1440), ctx: Ctx = Depends(need("ops:read"))) -> ApiOut:
    series, routes, _ = http.window(minutes)
    summary = _api_summary(minutes)
    rows = [RouteRow(route=k, requests=v.count, server_errors=v.server_errors, client_errors=v.client_errors,
                     error_rate=round(v.server_errors / v.count, 4) if v.count else 0.0, p50_ms=v.quantile(0.5),
                     p95_ms=v.quantile(0.95), p99_ms=v.quantile(0.99), max_ms=round(v.max_ms, 1))
            for k, v in routes.items()]
    rows.sort(key=lambda r: -r.requests)
    # Long windows: one point per 5 or 15 minutes so the chart stays readable.
    step = 1 if minutes <= 120 else 5 if minutes <= 360 else 15
    if step > 1:
        grouped = []
        for i in range(0, len(series), step):
            chunk = series[i:i + step]
            p95 = [c["p95_ms"] for c in chunk if c["p95_ms"] is not None]
            p50 = [c["p50_ms"] for c in chunk if c["p50_ms"] is not None]
            grouped.append({"at": chunk[0]["at"], "requests": sum(c["requests"] for c in chunk),
                            "server_errors": sum(c["server_errors"] for c in chunk),
                            "client_errors": sum(c["client_errors"] for c in chunk),
                            "p50_ms": round(median(p50), 1) if p50 else None, "p95_ms": max(p95) if p95 else None})
        summary.series = grouped
    return ApiOut(minutes=minutes, summary=summary, routes=rows[:60], errors=list(http.errors)[:40], slow=list(http.slow)[:40])


# --------------------------------------------------------------------------- jobs and workers


class KindRow(BaseModel):
    kind: str
    queued: int
    running: int
    succeeded: int
    failed: int
    dead: int
    avg_s: float | None
    p95_s: float | None
    max_s: float | None


class JobRow(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    tenant: str | None
    attempts: int
    max_attempts: int
    created_at: datetime
    run_at: datetime
    finished_at: datetime | None
    locked_by: str | None
    error: str | None


class JobsOut(BaseModel):
    summary: JobSummary
    workers: list[WorkerRow]
    kinds: list[KindRow]
    dead: list[JobRow]
    running: list[JobRow]
    recent_failures: list[JobRow]


def _job_row(j: Job, tenants: dict) -> JobRow:
    return JobRow(id=j.id, kind=j.kind, status=j.status, tenant=tenants.get(j.tenant_id), attempts=j.attempts,
                  max_attempts=j.max_attempts, created_at=j.created_at, run_at=j.run_at, finished_at=j.finished_at,
                  locked_by=j.locked_by, error=(j.last_error or "").strip().split("\n")[-1][:300] or None)


@router.get("/jobs", response_model=JobsOut)
def jobs_view(ctx: Ctx = Depends(need("ops:read"))) -> JobsOut:
    since = datetime.now(UTC) - timedelta(hours=24)
    with system_session() as db:
        tenants = dict(db.execute(select(Tenant.id, Tenant.name)).all())
        kinds: dict[str, dict] = {}
        for kind, status, n in db.execute(select(Job.kind, Job.status, func.count()).where(
                (Job.created_at >= since) | Job.status.in_(["queued", "running", "dead"])).group_by(Job.kind, Job.status)):
            kinds.setdefault(kind, {})[status] = n
        durations: dict[str, list[float]] = {}
        for kind, started, finished in db.execute(select(Job.kind, Job.locked_at, Job.finished_at).where(
                Job.finished_at >= since, Job.status == "succeeded", Job.locked_at.is_not(None))):
            durations.setdefault(kind, []).append((finished - started).total_seconds())
        krows = []
        for kind in sorted(kinds, key=lambda k: -sum(kinds[k].values())):
            st, ds = kinds[kind], durations.get(kind, [])
            krows.append(KindRow(kind=kind, queued=st.get("queued", 0), running=st.get("running", 0),
                                 succeeded=st.get("succeeded", 0), failed=st.get("failed", 0), dead=st.get("dead", 0),
                                 avg_s=round(sum(ds) / len(ds), 2) if ds else None, p95_s=_pct(ds, 0.95),
                                 max_s=round(max(ds), 2) if ds else None))
        dead = db.execute(select(Job).where(Job.status == "dead").order_by(Job.finished_at.desc().nullslast()).limit(30)).scalars()
        running = db.execute(select(Job).where(Job.status == "running").order_by(Job.locked_at)).scalars()
        failures = db.execute(select(Job).where(Job.status == "failed", Job.last_error.is_not(None))
                              .order_by(Job.created_at.desc()).limit(20)).scalars()
        return JobsOut(summary=_jobs(db), workers=_workers(db), kinds=krows, dead=[_job_row(j, tenants) for j in dead],
                       running=[_job_row(j, tenants) for j in running],
                       recent_failures=[_job_row(j, tenants) for j in failures])


@router.post("/jobs/{job_id}/retry", response_model=JobRow)
def retry_job(job_id: uuid.UUID, ctx: Ctx = Depends(need("ops:manage"))) -> JobRow:
    with system_session() as db:
        j = db.get(Job, job_id)
        if j is None:
            raise NotFound("Job not found.")
        if j.status not in ("dead", "failed"):
            raise Conflict("Only a failed job can be retried.")
        j.status, j.attempts, j.run_at, j.locked_by, j.locked_at, j.finished_at = "queued", 0, datetime.now(UTC), None, None, None
        audit.record(db, ctx.principal, "job.retried", tenant_id=j.tenant_id, target_type="job", target_id=j.id,
                     meta={"kind": j.kind})
        db.commit()
        return _job_row(j, dict(db.execute(select(Tenant.id, Tenant.name)).all()))


# --------------------------------------------------------------------------- database


class TableRow(BaseModel):
    table: str
    rows: int
    dead_rows: int
    size_mb: float
    last_vacuum: datetime | None
    seq_scans: int
    index_scans: int


class DatabaseOut(BaseModel):
    summary: DbSummary
    version: str
    migration: str | None
    connections: dict[str, int]
    max_connections: int
    commits: int
    rollbacks: int
    deadlocks: int
    longest_query_s: float | None
    tables: list[TableRow]


@router.get("/database", response_model=DatabaseOut)
def database(ctx: Ctx = Depends(need("ops:read"))) -> DatabaseOut:
    with system_session() as db:
        summary = _db(db)
        version_str = db.execute(text("SHOW server_version")).scalar_one()
        migration = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        conns = dict(db.execute(text("SELECT coalesce(state, 'background'), count(*) FROM pg_stat_activity "
                                     "WHERE datname = current_database() GROUP BY 1")).all())
        maxc = int(db.execute(text("SHOW max_connections")).scalar_one())
        commits, rollbacks, deadlocks = db.execute(text(
            "SELECT xact_commit, xact_rollback, deadlocks FROM pg_stat_database WHERE datname = current_database()")).one()
        longest = db.execute(text(
            "SELECT extract(epoch FROM max(now() - query_start)) FROM pg_stat_activity "
            "WHERE datname = current_database() AND state = 'active' AND pid <> pg_backend_pid()")).scalar()
        tables = db.execute(text(
            "SELECT relname, n_live_tup, n_dead_tup, pg_total_relation_size(relid), "
            "greatest(last_autovacuum, last_vacuum), seq_scan, coalesce(idx_scan, 0) "
            "FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 15")).all()
        return DatabaseOut(
            summary=summary, version=version_str, migration=migration, connections=conns, max_connections=maxc,
            commits=commits, rollbacks=rollbacks, deadlocks=deadlocks,
            longest_query_s=round(float(longest), 2) if longest else None,
            tables=[TableRow(table=t[0], rows=t[1], dead_rows=t[2], size_mb=round(t[3] / 1e6, 2), last_vacuum=t[4],
                             seq_scans=t[5], index_scans=t[6]) for t in tables],
        )


# --------------------------------------------------------------------------- organisations


class OrgRow(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    is_demo: bool
    created_at: datetime
    canteens: int
    accounts: int
    last_sign_in: datetime | None
    days_logged: int
    last_log: date | None
    forecasts_7d: int
    decided_share: float | None
    open_surplus: int
    surplus_30d: int
    dishes: int
    dishes_with_picture: int


@router.get("/organisations", response_model=list[OrgRow])
def organisations(ctx: Ctx = Depends(need("ops:read"))) -> list[OrgRow]:
    from foodwise.db.models import Dish

    week = date.today() - timedelta(days=7)
    month = datetime.now(UTC) - timedelta(days=30)
    out = []
    with system_session() as db:
        for t in db.execute(select(Tenant).order_by(Tenant.created_at)).scalars():
            canteens = db.execute(select(func.count()).select_from(Canteen).where(Canteen.tenant_id == t.id)).scalar_one()
            accounts, last_in = db.execute(select(func.count(), func.max(User.last_login_at)).where(User.tenant_id == t.id)).one()
            days, last = db.execute(select(func.count(func.distinct(ServiceLog.service_date)), func.max(ServiceLog.service_date))
                                    .where(ServiceLog.tenant_id == t.id)).one()
            fc, decided = db.execute(select(func.count(), func.count().filter(Forecast.status != "proposed")).where(
                Forecast.tenant_id == t.id, Forecast.service_date >= week, Forecast.service_date < date.today(),
                Forecast.kind != "shadow")).one()
            open_s = db.execute(select(func.count()).select_from(SurplusIncident).where(
                SurplusIncident.tenant_id == t.id, SurplusIncident.status.in_(ACTIVE))).scalar_one()
            month_s = db.execute(select(func.count()).select_from(SurplusIncident).where(
                SurplusIncident.tenant_id == t.id, SurplusIncident.created_at >= month)).scalar_one()
            dishes, pictured = db.execute(select(func.count(), func.count().filter(Dish.image_status == "found"))
                                          .where(Dish.tenant_id == t.id)).one()
            out.append(OrgRow(id=t.id, name=t.name, status=t.status, is_demo=t.is_demo, created_at=t.created_at,
                              canteens=canteens, accounts=accounts, last_sign_in=last_in, days_logged=days or 0,
                              last_log=last, forecasts_7d=fc, decided_share=round(decided / fc, 3) if fc else None,
                              open_surplus=open_s, surplus_30d=month_s, dishes=dishes, dishes_with_picture=pictured))
    return out


# --------------------------------------------------------------------------- sign-in security


class HourCount(BaseModel):
    hour: datetime
    failures: int


class FailedSignIn(BaseModel):
    at: datetime
    account: str
    ip: str | None


class SecurityOut(BaseModel):
    sign_ins_24h: int
    failed_24h: int
    locked_accounts: int
    token_reuse_7d: int
    failures_by_hour: list[HourCount]
    recent_failures: list[FailedSignIn]


def _mask(email: str | None) -> str:
    if not email or "@" not in email:
        return "unknown"
    name, domain = email.split("@", 1)
    return f"{name[:2]}***@{domain}"


@router.get("/security", response_model=SecurityOut)
def security_view(ctx: Ctx = Depends(need("ops:read"))) -> SecurityOut:
    now = datetime.now(UTC)
    day = now - timedelta(hours=24)
    with system_session() as db:
        count = lambda action, since: db.execute(select(func.count()).select_from(AuditLog).where(  # noqa: E731
            AuditLog.action == action, AuditLog.at >= since)).scalar_one()
        sign_ins = count("auth.login", day) + count("auth.demo_login", day)
        failed = count("auth.login_failed", day)
        reuse = count("auth.refresh_reuse_detected", now - timedelta(days=7))
        locked = db.execute(select(func.count()).select_from(User).where(User.locked_until > now)).scalar_one()
        hour = func.date_trunc("hour", AuditLog.at).label("hour")
        hours = db.execute(select(hour, func.count()).where(
            AuditLog.action == "auth.login_failed", AuditLog.at >= day).group_by(hour).order_by(hour)).all()
        recent = db.execute(select(AuditLog).where(AuditLog.action == "auth.login_failed")
                            .order_by(AuditLog.at.desc()).limit(15)).scalars()
        return SecurityOut(
            sign_ins_24h=sign_ins, failed_24h=failed, locked_accounts=locked, token_reuse_7d=reuse,
            failures_by_hour=[{"hour": h.isoformat(), "failures": n} for h, n in hours],
            recent_failures=[{"at": r.at.isoformat(), "account": _mask(r.actor_label),
                              "ip": (r.ip or "").rsplit(".", 1)[0] + ".x" if r.ip and "." in r.ip else r.ip} for r in recent],
        )


# --------------------------------------------------------------------------- real-user web performance


class VitalsOut(BaseModel):
    hours: int
    overall: list[VitalRow]
    routes: list[dict[str, float | int | str | None]]


@router.get("/web-vitals", response_model=VitalsOut)
def web_vitals(hours: int = Query(24, ge=1, le=24 * 14), ctx: Ctx = Depends(need("ops:read"))) -> VitalsOut:
    since = datetime.now(UTC) - timedelta(hours=hours)
    with system_session() as db:
        overall = _vitals(db, hours)
        rows = db.execute(select(WebVital.route, WebVital.metric, WebVital.value).where(WebVital.at >= since)).all()
    per: dict[str, dict[str, list[float]]] = {}
    for route, metric, value in rows:
        per.setdefault(route, {}).setdefault(metric, []).append(value)
    routes = [{"route": r, "samples": sum(len(v) for v in m.values()),
               **{f"{k}_p75": _pct(m.get(k, []), 0.75) for k in VITAL_LIMITS}} for r, m in per.items()]
    routes.sort(key=lambda r: -r["samples"])
    return VitalsOut(hours=hours, overall=overall, routes=routes[:30])


class VitalIn(BaseModel):
    metric: Literal["LCP", "INP", "CLS", "FCP", "TTFB"]
    value: float = Field(ge=0, le=600000)
    rating: Literal["good", "needs-improvement", "poor"]
    route: str = Field(max_length=120, pattern=r"^/[A-Za-z0-9/_:\-]*$")
    connection: str | None = Field(default=None, max_length=12)


class VitalsIn(BaseModel):
    samples: list[VitalIn] = Field(min_length=1, max_length=20)


@telemetry_router.post("/vitals", status_code=204)
def post_vitals(body: VitalsIn, request: Request) -> None:
    """Core Web Vitals from browsers (sent with navigator.sendBeacon, so no sign-in header).
    Only the metric, the route pattern and the connection type are kept."""
    limiter.hit(f"vitals-ip:{client_ip(request)}", 60, 60)
    with system_session() as db:
        db.add_all([WebVital(metric=s.metric, value=s.value, rating=s.rating, route=s.route, connection=s.connection)
                    for s in body.samples])
        db.commit()

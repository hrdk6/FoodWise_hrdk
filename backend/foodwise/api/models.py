"""Model registry: versions, leaderboards, training jobs, promotion, rollback and monitoring."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from foodwise.api.common import JobRef
from foodwise.core import audit, jobs
from foodwise.core.deps import Ctx, need
from foodwise.core.errors import Conflict, NotFound
from foodwise.db.models import Canteen, Job, ModelVersion
from foodwise.ml import monitoring, zoo
from foodwise.ml.training import promote
from foodwise.services import webhooks

router = APIRouter(tags=["models"])


class VersionBrief(BaseModel):
    id: uuid.UUID
    canteen_id: uuid.UUID
    version: int
    algorithm: str
    label: str
    family: str
    framing: str
    stage: str
    tier: int
    wape: float | None
    skill: float | None
    coverage80: float | None
    created_at: datetime
    promoted_at: datetime | None
    retired_at: datetime | None
    trigger: str | None
    shadow: dict | None


class VersionDetail(VersionBrief):
    metrics: dict
    leaderboard: list[dict]
    gates: list[dict]
    backtest: dict
    feature_list: list[str]
    feature_hash: str
    data_window: dict
    library_versions: dict
    feedback_ablation: dict | None
    monitoring: dict | None
    training_seconds: float
    seed: int


def _brief(m: ModelVersion) -> dict:
    metrics = m.metrics or {}
    return dict(
        id=m.id, canteen_id=m.canteen_id, version=m.version, algorithm=m.algorithm,
        label=zoo.LABELS.get(m.algorithm, m.algorithm), family=zoo.FAMILIES.get(m.algorithm, "other"), framing=m.framing,
        stage=m.stage, tier=m.tier, wape=metrics.get("wape"), skill=metrics.get("skill"),
        coverage80=metrics.get("coverage80"), created_at=m.created_at, promoted_at=m.promoted_at,
        retired_at=m.retired_at, trigger=(m.data_window or {}).get("trigger"), shadow=m.shadow,
    )


@router.get("/models", response_model=list[VersionBrief])
def list_models(canteen_id: uuid.UUID, ctx: Ctx = Depends(need("models:read"))) -> list[VersionBrief]:
    ctx.check_canteen(canteen_id)
    rows = ctx.db.execute(
        select(ModelVersion).where(ModelVersion.canteen_id == canteen_id).order_by(ModelVersion.version.desc())
    ).scalars()
    return [VersionBrief(**_brief(m)) for m in rows]


@router.get("/models/{version_id}", response_model=VersionDetail)
def get_model(version_id: uuid.UUID, ctx: Ctx = Depends(need("models:read"))) -> VersionDetail:
    m = ctx.db.get(ModelVersion, version_id)
    if m is None:
        raise NotFound("Model version not found.")
    ctx.check_canteen(m.canteen_id)
    return VersionDetail(
        **_brief(m), metrics=m.metrics or {}, leaderboard=m.leaderboard or [], gates=m.gates or [],
        backtest=m.backtest or {}, feature_list=m.feature_list or [], feature_hash=m.feature_hash,
        data_window=m.data_window or {}, library_versions=m.library_versions or {},
        feedback_ablation=m.feedback_ablation, monitoring=m.monitoring, training_seconds=m.training_seconds, seed=m.seed,
    )


class TrainIn(BaseModel):
    canteen_id: uuid.UUID


@router.post("/models/train", response_model=JobRef, status_code=202)
def train(body: TrainIn, ctx: Ctx = Depends(need("models:train"))) -> JobRef:
    ctx.check_canteen(body.canteen_id)
    if ctx.db.get(Canteen, body.canteen_id) is None:
        raise NotFound("Canteen not found.")
    job_id = jobs.enqueue(ctx.db, "train_canteen", {"canteen_id": str(body.canteen_id), "trigger": f"manual by {ctx.principal.name}"},
                          tenant_id=ctx.tenant_id, dedupe_key=f"train:{body.canteen_id}", max_attempts=2)
    audit.record(ctx.db, ctx.principal, "model.train_requested", target_type="canteen", target_id=body.canteen_id)
    ctx.db.commit()
    return JobRef(job_id=str(job_id))


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    attempts: int
    progress: dict | None
    result: dict | None
    last_error: str | None
    created_at: datetime
    finished_at: datetime | None


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, ctx: Ctx = Depends(need("models:read"))) -> JobOut:
    j = ctx.db.get(Job, job_id)
    if j is None:
        raise NotFound("Job not found.")
    return JobOut(id=j.id, kind=j.kind, status=j.status, attempts=j.attempts, progress=j.progress, result=j.result,
                  last_error=(j.last_error or "").split("\n")[0] or None, created_at=j.created_at, finished_at=j.finished_at)


@router.post("/models/{version_id}/promote", response_model=VersionBrief)
def promote_version(version_id: uuid.UUID, ctx: Ctx = Depends(need("models:promote"))) -> VersionBrief:
    m = ctx.db.get(ModelVersion, version_id)
    if m is None:
        raise NotFound("Model version not found.")
    ctx.check_canteen(m.canteen_id)
    if m.stage not in ("challenger", "retired", "rejected"):
        raise Conflict(f"A {m.stage} model cannot be promoted.")
    promote(ctx.db, m, f"Promoted manually by {ctx.principal.name}")
    audit.record(ctx.db, ctx.principal, "model.promoted", target_type="model_version", target_id=m.id,
                 meta={"version": m.version, "algorithm": m.algorithm})
    webhooks.emit(ctx.db, ctx.tenant_id, "model.promoted", {"canteen_id": str(m.canteen_id), "version": m.version,
                                                            "algorithm": m.algorithm, "manual": True})
    ctx.db.commit()
    return VersionBrief(**_brief(m))


@router.post("/models/rollback", response_model=VersionBrief)
def rollback(body: TrainIn, ctx: Ctx = Depends(need("models:promote"))) -> VersionBrief:
    ctx.check_canteen(body.canteen_id)
    restored = monitoring.rollback(ctx.db, body.canteen_id)
    if restored is None:
        raise Conflict("There is no earlier champion to roll back to.")
    audit.record(ctx.db, ctx.principal, "model.rolled_back", target_type="model_version", target_id=restored.id)
    ctx.db.commit()
    return VersionBrief(**_brief(restored))

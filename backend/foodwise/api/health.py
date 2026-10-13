from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from foodwise.db.base import system_session

router = APIRouter(tags=["health"])


@router.get("/health/live")
def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
def ready():
    try:
        with system_session() as db:
            db.execute(text("SELECT 1"))
            queued = db.execute(text("SELECT count(*) FROM jobs WHERE status = 'queued' AND run_at < now() - interval '5 minutes'")).scalar()
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "degraded", "database": "unavailable"}, status_code=503,
                            headers={"Retry-After": "5"})
    return {"status": "ok", "database": "ok", "overdue_jobs": int(queued or 0)}


@router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

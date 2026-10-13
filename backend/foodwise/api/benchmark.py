"""Real-data benchmark: the forecasting method on the public Genpact food demand dataset.

The dataset is public and the same for every organisation, so the report is global, not tenant data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

from foodwise.api.common import JobRef
from foodwise.benchmark import genpact
from foodwise.config import get_settings
from foodwise.core import audit, jobs
from foodwise.core.deps import Ctx, need
from foodwise.core.errors import NotFound, Unprocessable

router = APIRouter(prefix="/benchmark", tags=["benchmark"])


class GenpactOut(BaseModel):
    status: str  # missing_files | not_run | ready
    data_dir: str
    missing_files: list[str]
    report: dict | None


@router.get("/genpact", response_model=GenpactOut)
def get_genpact(ctx: Ctx = Depends(need("models:read"))) -> GenpactOut:
    data_dir = get_settings().genpact_dir
    missing = genpact.missing_files(data_dir)
    report = genpact.saved_report()
    status = "ready" if report else "missing_files" if missing else "not_run"
    return GenpactOut(status=status, data_dir=str(data_dir), missing_files=missing, report=report)


@router.post("/genpact/run", response_model=JobRef, status_code=202)
def run_genpact(ctx: Ctx = Depends(need("models:train"))) -> JobRef:
    missing = genpact.missing_files(get_settings().genpact_dir)
    if missing:
        raise Unprocessable(f"Put {', '.join(missing)} in the data/genpact folder first.")
    job_id = jobs.enqueue(ctx.db, "genpact_benchmark", {}, tenant_id=ctx.tenant_id, dedupe_key="benchmark:genpact",
                          max_attempts=1)
    audit.record(ctx.db, ctx.principal, "benchmark.run_requested", target_type="benchmark", target_id=None,
                 meta={"dataset": "genpact"})
    ctx.db.commit()
    return JobRef(job_id=str(job_id))


@router.get("/genpact/clean.csv")
def clean_csv(ctx: Ctx = Depends(need("models:read"))) -> FileResponse:
    path = genpact.clean_csv_path()
    if path is None:
        raise NotFound("Run the benchmark first.")
    return FileResponse(path, media_type="text/csv", filename="genpact-clean.csv")

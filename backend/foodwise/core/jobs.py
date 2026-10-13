"""PostgreSQL-backed job queue.

Jobs are rows in `jobs`. Workers claim them with `FOR UPDATE SKIP LOCKED`, so any number of
worker processes can run safely without Redis. Delayed jobs (surplus safe-window expiry) are rows with
a future `run_at`. A partial unique index on `dedupe_key` keeps at most one queued or running
job per key (for example one forecast run per canteen per day).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import traceback
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from foodwise.db.base import system_session
from foodwise.db.models import Job

log = logging.getLogger("foodwise.jobs")

Handler = Callable[[Job], dict | None]
HANDLERS: dict[str, Handler] = {}
STALE_AFTER = timedelta(minutes=20)


class RetryLater(Exception):  # noqa: N818
    """Raise from a handler to reschedule without counting it as a failure."""

    def __init__(self, delay_s: float):
        self.delay_s = delay_s


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return deco


def enqueue(
    db: Session,
    kind: str,
    payload: dict | None = None,
    *,
    tenant_id: uuid.UUID | str | None = None,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 4,
) -> uuid.UUID:
    """Insert a job in the caller's transaction. Returns the new job id, or the id of the
    already-active job with the same dedupe key."""
    job_id = uuid.uuid4()
    stmt = (
        insert(Job)
        .values(
            id=job_id,
            tenant_id=uuid.UUID(str(tenant_id)) if tenant_id else None,
            kind=kind,
            payload=payload or {},
            run_at=run_at or datetime.now(UTC),
            dedupe_key=dedupe_key,
            max_attempts=max_attempts,
            status="queued",
        )
        .on_conflict_do_nothing(
            index_elements=["dedupe_key"],
            index_where=text("dedupe_key IS NOT NULL AND status IN ('queued', 'running')"),
        )
        .returning(Job.id)
    )
    inserted = db.execute(stmt).scalar_one_or_none()
    if inserted is None and dedupe_key:
        existing = db.execute(
            select(Job.id).where(Job.dedupe_key == dedupe_key, Job.status.in_(["queued", "running"]))
        ).scalar_one_or_none()
        if existing:
            return existing
    db.execute(text("SELECT pg_notify('foodwise_jobs', :k)"), {"k": kind})
    return inserted or job_id


def set_progress(job_id: uuid.UUID, progress: dict[str, Any]) -> None:
    with system_session() as db:
        db.execute(text("UPDATE jobs SET progress = CAST(:p AS jsonb) WHERE id = :id"), {"p": _json(progress), "id": job_id})
        db.commit()


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def _claim(worker_id: str) -> Job | None:
    with system_session() as db:
        row = db.execute(
            text(
                """
                UPDATE jobs SET status = 'running', locked_by = :w, locked_at = now(), attempts = attempts + 1
                WHERE id = (
                  SELECT id FROM jobs WHERE status = 'queued' AND run_at <= now()
                  ORDER BY run_at FOR UPDATE SKIP LOCKED LIMIT 1
                )
                RETURNING id
                """
            ),
            {"w": worker_id},
        ).scalar_one_or_none()
        db.commit()
        if row is None:
            return None
        job = db.get(Job, row)
        db.expunge(job)
        return job


def _finish(job: Job, *, result: dict | None = None, error: str | None = None, retry_in: float | None = None,
            count_attempt: bool = True) -> None:
    with system_session() as db:
        current = db.get(Job, job.id)
        if current is None:
            return
        now = datetime.now(UTC)
        if error is None and retry_in is None:
            current.status = "succeeded"
            current.result = result
            current.finished_at = now
        elif retry_in is not None and (not count_attempt or current.attempts < current.max_attempts):
            current.status = "queued"
            current.run_at = now + timedelta(seconds=retry_in)
            if not count_attempt:
                current.attempts -= 1
            current.last_error = error
            current.locked_by = None
            current.locked_at = None
        else:
            current.status = "dead"
            current.last_error = error
            current.finished_at = now
            log.error("job moved to dead-letter", extra={"job_id": str(job.id), "job_kind": job.kind})
        # Finished jobs keep who ran them and when they started, for run-time statistics.
        db.commit()


def run_one(worker_id: str) -> bool:
    """Claim and run a single job. Returns False when the queue had nothing ready."""
    job = _claim(worker_id)
    if job is None:
        return False
    fn = HANDLERS.get(job.kind)
    extra = {"job_id": str(job.id), "job_kind": job.kind}
    heartbeat(worker_id, current=job.kind)
    if fn is None:
        _finish(job, error=f"no handler for job kind {job.kind!r}", retry_in=None)
        return True
    started = datetime.now(UTC)
    try:
        result = fn(job)
    except RetryLater as wait:
        _finish(job, retry_in=wait.delay_s, count_attempt=False)
    except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job
        backoff = min(600, 10 * 2 ** max(0, job.attempts - 1))
        _finish(job, error=f"{exc}\n{traceback.format_exc(limit=8)}", retry_in=backoff)
        log.warning("job failed: %s", exc, extra=extra)
        heartbeat(worker_id, failed=1)
    else:
        _finish(job, result=result or {})
        took = (datetime.now(UTC) - started).total_seconds()
        log.info("job done in %.2fs", took, extra=extra)
        heartbeat(worker_id, done=1)
    return True


def heartbeat(worker_id: str, *, current: str | None = None, done: int = 0, failed: int = 0) -> None:
    """Workers check in so the admin console can tell a stalled queue from an idle one."""
    from sqlalchemy.dialects.postgresql import insert

    from foodwise.db.models import WorkerHeartbeat

    now = datetime.now(UTC)
    t = WorkerHeartbeat.__table__
    try:
        with system_session() as db:
            stmt = insert(t).values(worker_id=worker_id[:80], started_at=now, last_seen_at=now, jobs_done=done,
                                    jobs_failed=failed, current_job=current)
            db.execute(stmt.on_conflict_do_update(index_elements=["worker_id"], set_={
                "last_seen_at": now, "jobs_done": t.c.jobs_done + done, "jobs_failed": t.c.jobs_failed + failed,
                "current_job": current,
            }))
            db.commit()
    except Exception:  # noqa: BLE001 - never let bookkeeping stop a worker
        log.debug("heartbeat failed", exc_info=True)


def recover_stale() -> int:
    """Requeue jobs whose worker died mid-run (idempotent handlers make this safe)."""
    with system_session() as db:
        n = db.execute(
            text(
                "UPDATE jobs SET status = 'queued', locked_by = NULL, locked_at = NULL "
                "WHERE status = 'running' AND locked_at < now() - make_interval(secs => :s)"
            ),
            {"s": STALE_AFTER.total_seconds()},
        ).rowcount
        db.commit()
        return n or 0


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident() % 10000}"


def drain(max_jobs: int = 1000) -> int:
    """Run every ready job now (tests and the seed script)."""
    wid = worker_id()
    n = 0
    while n < max_jobs and run_one(wid):
        n += 1
    return n

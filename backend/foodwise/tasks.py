"""Job handlers. Each is idempotent: re-running a job after a crash produces the same end state."""

from __future__ import annotations

import logging
import socket
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, select, update

from foodwise.core import audit, jobs, notify
from foodwise.core.jobs import handler
from foodwise.core.storage import storage
from foodwise.core.timeutil import local_today, tz
from foodwise.db.base import system_session, tenant_session
from foodwise.db.models import (
    Canteen,
    Dish,
    Feedback,
    Job,
    ModelVersion,
    ServiceCheck,
    Tenant,
    User,
    WebhookDelivery,
    WebVital,
)
from foodwise.ml import monitoring, registry, serving, training
from foodwise.services import dish_images, incidents, webhooks

log = logging.getLogger("foodwise.tasks")


def _tenant_canteen(db, canteen_id: str) -> tuple[Tenant, Canteen]:
    canteen = db.get(Canteen, uuid.UUID(canteen_id))
    if canteen is None:
        raise ValueError("canteen not found")
    return db.get(Tenant, canteen.tenant_id), canteen


@handler("train_canteen")
def train_job(job: Job) -> dict:
    canteen_id = uuid.UUID(job.payload["canteen_id"])
    result = training.train_canteen(
        job.tenant_id, canteen_id, trigger=job.payload.get("trigger", "manual"),
        progress=lambda label, frac: jobs.set_progress(job.id, {"label": label, "fraction": frac}),
    )
    with tenant_session(job.tenant_id) as db:
        tenant = db.get(Tenant, job.tenant_id)
        if result.get("stage") == "champion":
            webhooks.emit(db, tenant.id, "model.promoted", {"canteen_id": str(canteen_id), **result})
            today = local_today(tenant.timezone)
            for day in (today, today + timedelta(days=1)):
                jobs.enqueue(db, "generate_forecasts", {"canteen_id": str(canteen_id), "date": day.isoformat()},
                             tenant_id=tenant.id, dedupe_key=f"forecast:{canteen_id}:{day}")
        audit.record(db, None, "model.trained", tenant_id=tenant.id, target_type="model_version",
                     target_id=result.get("model_version_id"), meta=result)
        db.commit()
    return result


@handler("generate_forecasts")
def forecast_job(job: Job) -> dict:
    with tenant_session(job.tenant_id) as db:
        tenant, canteen = _tenant_canteen(db, job.payload["canteen_id"])
        day = date.fromisoformat(job.payload["date"])
        saved = serving.generate(db, tenant, canteen, day)
        webhooks.emit(db, tenant.id, "forecast.generated", {
            "canteen_id": str(canteen.id), "date": day.isoformat(), "dishes": len(saved),
            "kind": saved[0].kind if saved else None,
        })
        if job.payload.get("digest") and saved:
            _send_digest(db, tenant, canteen, day, saved)
        db.commit()
        return {"forecasts": len(saved), "kind": saved[0].kind if saved else None}


def _send_digest(db, tenant: Tenant, canteen: Canteen, day: date, saved) -> None:
    managers = db.execute(
        select(User).where(User.role == "organization", User.is_active.is_(True))
    ).scalars().all()
    lines = []
    for meal in [m["key"] for m in canteen.meals]:
        rows = [f for f in saved if f.meal == meal]
        if rows:
            lines.append(f"{meal.capitalize()}: {len(rows)} dishes")
    body = f"Tomorrow's prep plan for {canteen.name} ({day:%a %d %b}) is ready to approve. " + "; ".join(lines)
    for m in managers:
        if m.canteen_ids and canteen.id not in m.canteen_ids:
            continue
        notify.send(db, tenant_id=tenant.id, recipient=notify.Recipient(label=m.name, email=m.email),
                    subject=f"Prep plan for {canteen.name}, {day:%d %b}", body=body,
                    link=f"/plan?canteen={canteen.id}&date={day.isoformat()}", channels=("email",),
                    related=("canteen", str(canteen.id)))


@handler("surplus_expiry")
def surplus_expiry_job(job: Job) -> dict:
    """At the end of a surplus's safe window."""
    with tenant_session(job.tenant_id) as db:
        outcome = incidents.expire(db, uuid.UUID(job.payload["incident_id"]))
        db.commit()
        return {"outcome": outcome}


@handler("find_dish_image")
def dish_image_job(job: Job) -> dict:
    """Find a picture for a dish on Wikimedia Commons (network errors retry with backoff)."""
    with tenant_session(job.tenant_id) as db:
        dish = db.get(Dish, uuid.UUID(job.payload["dish_id"]))
        if dish is None:
            return {"skipped": "dish deleted"}
        force = bool(job.payload.get("force"))
        missing = dish.image_key is not None and not storage().exists(dish.image_key)
        if dish.image_status == "removed" or (dish.image_source == "upload" and not missing) or (
                dish.image_status == "found" and not force and not missing):
            return {"skipped": dish.image_status}
        if dish.image_source == "upload" and missing:
            dish.image_key, dish.image_source, dish.image_credit = None, None, None  # the kitchen's photo was lost
        outcome = dish_images.auto_find(dish)
        db.commit()
        return {"outcome": outcome, "credit": dish.image_credit}


@handler("warm_places")
def warm_places_job(job: Job) -> dict:
    from foodwise.domain.settings import effective_settings
    from foodwise.services import places

    with tenant_session(job.tenant_id) as db:
        tenant, canteen = _tenant_canteen(db, job.payload["canteen_id"])
        radius = float(effective_settings(tenant.settings)["places"]["radius_km"])
        lat, lon = canteen.lat, canteen.lon
    return places.warm(lat, lon, radius)


@handler("verify_pictures")
def verify_pictures_job(job: Job) -> dict:
    """Queue a picture search for dishes still without one, or whose file is missing from this
    worker's storage (a new volume, a restored database)."""
    queued = 0
    with system_session() as db:
        for d in db.execute(select(Dish).where(Dish.image_status.in_(["pending", "found", "failed"]))).scalars():
            missing = d.image_status == "found" and (d.image_key is None or not storage().exists(d.image_key))
            if d.image_status != "found" or missing:
                jobs.enqueue(db, "find_dish_image", {"dish_id": str(d.id), "force": missing}, tenant_id=d.tenant_id,
                             dedupe_key=f"dish-image:{d.id}")
                queued += 1
        db.commit()
    return {"queued": queued}


@handler("deliver_webhook")
def webhook_job(job: Job) -> dict:
    with tenant_session(job.tenant_id) as db:
        delivery = db.get(WebhookDelivery, uuid.UUID(job.payload["delivery_id"]))
        if delivery is None or delivery.status == "delivered":
            return {"skipped": True}
        try:
            code = webhooks.deliver(db, delivery)
        finally:
            db.commit()
        return {"status": code}


@handler("monitor_canteen")
def monitor_job(job: Job) -> dict:
    with tenant_session(job.tenant_id) as db:
        tenant, canteen = _tenant_canteen(db, job.payload["canteen_id"])
        today = local_today(tenant.timezone)
        shadow = monitoring.evaluate_shadow(db, canteen.id, today)
        champ = registry.champion(db, canteen.id)
        result = monitoring.evaluate_canteen(db, canteen.id, today)
        if champ is not None:
            champ.monitoring = result
            if result["status"] == "degraded" and champ.stage == "champion":
                champ.stage = "degraded"
            if result["status"] in ("degraded", "drift"):
                jobs.enqueue(db, "train_canteen", {"canteen_id": str(canteen.id), "trigger": f"monitoring: {result['status']}"},
                             tenant_id=tenant.id, dedupe_key=f"train:{canteen.id}")
        if shadow.get("decision") == "promoted":
            webhooks.emit(db, tenant.id, "model.promoted", {"canteen_id": str(canteen.id), **shadow})
        db.commit()
        return {"monitoring": result.get("status"), "shadow": shadow.get("decision")}


@handler("demo_tick")
def demo_job(job: Job) -> dict:
    """Continue the simulated college for days that passed since it was seeded."""
    from foodwise.sim.live import advance_demo

    return advance_demo(job.tenant_id)


@handler("purge_feedback_text")
def purge_job(job: Job) -> dict:
    """Raw comment text expires (default 90 days); ratings and aggregates remain."""
    with system_session() as db:
        n = db.execute(
            update(Feedback).where(Feedback.text_expires_at < datetime.now(UTC), Feedback.text.is_not(None))
            .values(text=None)
        ).rowcount
        # Operations telemetry is kept two weeks (web vitals) and a month (external calls).
        vitals = db.execute(delete(WebVital).where(WebVital.at < datetime.now(UTC) - timedelta(days=14))).rowcount
        checks = db.execute(delete(ServiceCheck).where(ServiceCheck.hour < datetime.now(UTC) - timedelta(days=31))).rowcount
        db.commit()
        return {"purged": n or 0, "web_vitals": vitals or 0, "service_checks": checks or 0}


# --------------------------------------------------------------------------- scheduler


def _ran(db, key: str) -> bool:
    return db.execute(select(Job.id).where(Job.dedupe_key == key).limit(1)).first() is not None


def schedule_tick(now: datetime | None = None) -> int:
    """Enqueue time-based work. Safe to call from several workers: keys are unique per period."""
    now = now or datetime.now(UTC)
    enqueued = 0
    with system_session() as db:
        canteens = db.execute(
            select(Canteen, Tenant).join(Tenant, Tenant.id == Canteen.tenant_id)
            .where(Canteen.is_active.is_(True), Tenant.status == "active")
        ).all()
        for canteen, tenant in canteens:
            local = now.astimezone(tz(tenant.timezone))
            today = local.date()
            tomorrow = today + timedelta(days=1)
            if local.time() >= canteen.cutoff_time:
                key = f"cutoff:{canteen.id}:{tomorrow}"
                if not _ran(db, key):
                    jobs.enqueue(db, "generate_forecasts", {"canteen_id": str(canteen.id), "date": tomorrow.isoformat(),
                                                            "digest": True},
                                 tenant_id=tenant.id, dedupe_key=key)
                    enqueued += 1
            key = f"places:{canteen.id}:{today}"
            if not _ran(db, key):
                jobs.enqueue(db, "warm_places", {"canteen_id": str(canteen.id)}, tenant_id=tenant.id, dedupe_key=key,
                             max_attempts=2)
                enqueued += 1
            if local.hour >= 2:
                key = f"monitor:{canteen.id}:{today}"
                if not _ran(db, key):
                    jobs.enqueue(db, "monitor_canteen", {"canteen_id": str(canteen.id)}, tenant_id=tenant.id, dedupe_key=key)
                    enqueued += 1
                champ = db.execute(
                    select(ModelVersion.created_at).where(ModelVersion.canteen_id == canteen.id)
                    .order_by(ModelVersion.created_at.desc()).limit(1)
                ).scalar()
                every = int((tenant.settings or {}).get("models", {}).get("retrain_every_days", 7))
                if champ is None or now - champ > timedelta(days=every):
                    key = f"retrain:{canteen.id}:{today}"
                    if not _ran(db, key):
                        jobs.enqueue(db, "train_canteen", {"canteen_id": str(canteen.id), "trigger": "scheduled"},
                                     tenant_id=tenant.id, dedupe_key=key)
                        enqueued += 1
        demo_tenants = db.execute(select(Tenant).where(Tenant.is_demo.is_(True))).scalars().all()
        for tenant in demo_tenants:
            if (tenant.settings or {}).get("demo"):
                key = f"demo:{tenant.id}:{now.astimezone(tz(tenant.timezone)).date()}"
                if not _ran(db, key):
                    jobs.enqueue(db, "demo_tick", {}, tenant_id=tenant.id, dedupe_key=key)
                    enqueued += 1
        key = f"purge:{now.date()}"
        if not _ran(db, key):
            jobs.enqueue(db, "purge_feedback_text", {}, dedupe_key=key)
        # Per host: each storage location checks its own picture files once a day.
        key = f"pictures:{socket.gethostname()}:{now.date()}"
        if not _ran(db, key):
            jobs.enqueue(db, "verify_pictures", {}, dedupe_key=key, max_attempts=2)
        db.commit()
    return enqueued


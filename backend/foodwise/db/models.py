"""ORM models. Tables with a `tenant_id` column are protected by row-level security
(see migrations). `refresh_tokens`, `place_cache`, `service_checks`, `worker_heartbeats` and
`web_vitals` are global: they hold no organisation records."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from foodwise.db.base import Base, TimestampMixin, new_id

UUIDCol = UUID(as_uuid=True)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUIDCol, primary_key=True, default=new_id)


def _tenant_fk(nullable: bool = False) -> Mapped[Any]:
    return mapped_column(UUIDCol, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=nullable, index=True)


# --------------------------------------------------------------------------- tenancy


class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    service_mode: Mapped[str] = mapped_column(String(20), default="fixed_thali")
    data_sharing: Mapped[str] = mapped_column(String(20), default="none")
    feedback_mode: Mapped[str] = mapped_column(String(20), default="anonymous")
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID | None] = _tenant_fk(nullable=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(32))  # organization | admin
    canteen_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUIDCol), default=list)
    password_hash: Mapped[str] = mapped_column(String(255))
    language: Mapped[str] = mapped_column(String(8), default="en")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_seed: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(TimestampMixin, Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    user_agent: Mapped[str | None] = mapped_column(String(300))


# --------------------------------------------------------------------------- places and menus


class Campus(TimestampMixin, Base):
    __tablename__ = "campuses"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)


class Canteen(TimestampMixin, Base):
    __tablename__ = "canteens"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    campus_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("campuses.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20), default="mess")  # mess | canteen
    service_mode: Mapped[str] = mapped_column(String(20), default="fixed_thali")
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    pickup_point: Mapped[str | None] = mapped_column(Text)
    cutoff_time: Mapped[time] = mapped_column(Time, default=time(21, 30))
    meals: Mapped[list[dict]] = mapped_column(JSONB, default=list)  # [{key,label,start,end}]
    expected_diners: Mapped[int] = mapped_column(Integer, default=500)
    costs: Mapped[dict | None] = mapped_column(JSONB)  # per-canteen override of tenant costs
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Dish(TimestampMixin, Base):
    __tablename__ = "dishes"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(120))
    name_hi: Mapped[str | None] = mapped_column(String(120))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    course: Mapped[str] = mapped_column(String(20))
    diet: Mapped[str] = mapped_column(String(12), default="veg")
    cuisine: Mapped[str | None] = mapped_column(String(40))
    unit: Mapped[str] = mapped_column(String(12), default="plates")
    portion_kg: Mapped[float] = mapped_column(Float, default=0.3)
    cost_per_unit: Mapped[float] = mapped_column(Float, default=30.0)
    food_category: Mapped[str] = mapped_column(String(30), default="curry_gravy")
    allergens: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Picture: found on Wikimedia Commons, or uploaded by the kitchen.
    image_key: Mapped[str | None] = mapped_column(String(400))
    image_source: Mapped[str | None] = mapped_column(String(20))  # wikimedia | upload
    image_status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|found|none|removed|failed
    image_credit: Mapped[str | None] = mapped_column(Text)
    image_license: Mapped[str | None] = mapped_column(String(80))
    image_source_url: Mapped[str | None] = mapped_column(Text)
    image_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MenuEntry(TimestampMixin, Base):
    __tablename__ = "menu_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "canteen_id", "service_date", "meal", "dish_id"),
        Index("ix_menu_lookup", "tenant_id", "canteen_id", "service_date"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    canteen_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("canteens.id", ondelete="CASCADE"))
    service_date: Mapped[date] = mapped_column(Date)
    meal: Mapped[str] = mapped_column(String(20))
    dish_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("dishes.id", ondelete="CASCADE"))


class CalendarDay(TimestampMixin, Base):
    """Academic calendar, events and disruptions. canteen_id NULL means campus-wide."""

    __tablename__ = "calendar_days"
    __table_args__ = (Index("ix_calendar_lookup", "tenant_id", "day"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    canteen_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("canteens.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(20))  # exam|holiday|vacation|fest|event|disruption|term_start
    label: Mapped[str] = mapped_column(String(200))
    meal: Mapped[str | None] = mapped_column(String(20))
    expected_attendance: Mapped[int | None] = mapped_column(Integer)
    disruption: Mapped[str | None] = mapped_column(String(30))  # event_cancelled|exam_moved|power_cut|holiday_declared


class WeatherDay(TimestampMixin, Base):
    __tablename__ = "weather_days"
    __table_args__ = (UniqueConstraint("tenant_id", "canteen_id", "day"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    canteen_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("canteens.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    temp_c: Mapped[float] = mapped_column(Float)
    rain_prob: Mapped[float] = mapped_column(Float)
    condition: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20), default="simulated")


# --------------------------------------------------------------------------- logging


class ServiceLog(TimestampMixin, Base):
    __tablename__ = "service_logs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "canteen_id", "service_date", "meal", "dish_id"),
        Index("ix_logs_lookup", "tenant_id", "canteen_id", "service_date"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    canteen_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("canteens.id", ondelete="CASCADE"))
    service_date: Mapped[date] = mapped_column(Date)
    meal: Mapped[str] = mapped_column(String(20))
    dish_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("dishes.id", ondelete="CASCADE"))
    unit: Mapped[str] = mapped_column(String(12))
    prepared_qty: Mapped[float] = mapped_column(Float)
    served_qty: Mapped[float] = mapped_column(Float)
    unserved_qty: Mapped[float] = mapped_column(Float, default=0)
    plate_waste_qty: Mapped[float] = mapped_column(Float, default=0)
    spoilage_qty: Mapped[float] = mapped_column(Float, default=0)
    footfall: Mapped[int | None] = mapped_column(Integer)
    sold_out: Mapped[bool] = mapped_column(Boolean, default=False)
    cooked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(20), default="kitchen")
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    quality_flags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LiveSession(TimestampMixin, Base):
    """Intraday tracker: remaining stock checkpoints during one service."""

    __tablename__ = "live_sessions"
    __table_args__ = (UniqueConstraint("tenant_id", "canteen_id", "service_date", "meal"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    canteen_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("canteens.id", ondelete="CASCADE"))
    service_date: Mapped[date] = mapped_column(Date)
    meal: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prepared: Mapped[dict] = mapped_column(JSONB, default=dict)  # dish_id -> qty
    checkpoints: Mapped[list[dict]] = mapped_column(JSONB, default=list)  # [{at, remaining:{dish_id: qty}}]
    warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportJob(TimestampMixin, Base):
    __tablename__ = "import_jobs"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(20), default="uploaded")  # uploaded|validated|committed|failed
    headers: Mapped[list[str]] = mapped_column(JSONB, default=list)
    preview: Mapped[list[list[str]]] = mapped_column(JSONB, default=list)
    mapping: Mapped[dict] = mapped_column(JSONB, default=dict)
    dish_matches: Mapped[dict] = mapped_column(JSONB, default=dict)
    canteen_matches: Mapped[dict] = mapped_column(JSONB, default=dict)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    committed_rows: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- ML


class ModelVersion(TimestampMixin, Base):
    __tablename__ = "model_versions"
    __table_args__ = (Index("ix_models_canteen_stage", "tenant_id", "canteen_id", "stage"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    canteen_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("canteens.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    algorithm: Mapped[str] = mapped_column(String(40))
    framing: Mapped[str] = mapped_column(String(20))
    stage: Mapped[str] = mapped_column(String(20))  # challenger|champion|retired|rejected|degraded
    tier: Mapped[int] = mapped_column(SmallInteger)
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
    leaderboard: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    gates: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    backtest: Mapped[dict] = mapped_column(JSONB, default=dict)
    feature_list: Mapped[list[str]] = mapped_column(JSONB, default=list)
    feature_hash: Mapped[str] = mapped_column(String(64))
    data_window: Mapped[dict] = mapped_column(JSONB, default=dict)
    library_versions: Mapped[dict] = mapped_column(JSONB, default=dict)
    feedback_ablation: Mapped[dict | None] = mapped_column(JSONB)
    seed: Mapped[int] = mapped_column(Integer, default=42)
    artifact_uri: Mapped[str] = mapped_column(String(400))
    training_seconds: Mapped[float] = mapped_column(Float, default=0)
    shadow: Mapped[dict | None] = mapped_column(JSONB)
    monitoring: Mapped[dict | None] = mapped_column(JSONB)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Forecast(TimestampMixin, Base):
    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "canteen_id", "service_date", "meal", "dish_id", "kind"),
        Index("ix_forecasts_lookup", "tenant_id", "canteen_id", "service_date"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    canteen_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("canteens.id", ondelete="CASCADE"))
    service_date: Mapped[date] = mapped_column(Date)
    meal: Mapped[str] = mapped_column(String(20))
    dish_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("dishes.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(12), default="model")  # model|baseline|prior|shadow
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol, ForeignKey("model_versions.id"))
    algorithm: Mapped[str] = mapped_column(String(40))
    unit: Mapped[str] = mapped_column(String(12))
    levels: Mapped[list[float]] = mapped_column(JSONB)
    quantiles: Mapped[list[float]] = mapped_column(JSONB)
    p10: Mapped[float] = mapped_column(Float)
    p50: Mapped[float] = mapped_column(Float)
    p90: Mapped[float] = mapped_column(Float)
    critical_ratio: Mapped[float] = mapped_column(Float)
    recommended_qty: Mapped[float] = mapped_column(Float)
    drivers: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed|approved|overridden
    override_qty: Mapped[float | None] = mapped_column(Float)
    override_reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changed_after_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- students


class Feedback(TimestampMixin, Base):
    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint("tenant_id", "canteen_id", "service_date", "meal", "dish_id", "device_hash"),
        Index("ix_feedback_dish", "tenant_id", "dish_id", "service_date"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    canteen_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("canteens.id", ondelete="CASCADE"))
    service_date: Mapped[date] = mapped_column(Date)
    meal: Mapped[str] = mapped_column(String(20))
    dish_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("dishes.id", ondelete="CASCADE"))
    rating: Mapped[int] = mapped_column(SmallInteger)  # 1..5 (disliked=1, okay=3, liked=5, or stars)
    aspects: Mapped[dict] = mapped_column(JSONB, default=dict)  # {taste: -1|1, ...}
    portion: Mapped[str | None] = mapped_column(String(12))  # too_much|right|too_little
    text: Mapped[str | None] = mapped_column(Text)
    text_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    language: Mapped[str | None] = mapped_column(String(8))
    sentiment: Mapped[float | None] = mapped_column(Float)
    text_aspects: Mapped[dict] = mapped_column(JSONB, default=dict)
    device_hash: Mapped[str] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    spam_score: Mapped[float] = mapped_column(Float, default=0)
    weight: Mapped[float] = mapped_column(Float, default=1.0)


class Vote(TimestampMixin, Base):
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("tenant_id", "canteen_id", "week_start", "device_hash"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    canteen_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("canteens.id", ondelete="CASCADE"))
    week_start: Mapped[date] = mapped_column(Date)
    dish_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("dishes.id", ondelete="CASCADE"))
    device_hash: Mapped[str] = mapped_column(String(64))


class Rsvp(TimestampMixin, Base):
    __tablename__ = "rsvps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "canteen_id", "service_date", "meal", "device_hash"),
        Index("ix_rsvp_lookup", "tenant_id", "canteen_id", "service_date"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    canteen_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("canteens.id", ondelete="CASCADE"))
    service_date: Mapped[date] = mapped_column(Date)
    meal: Mapped[str] = mapped_column(String(20))
    device_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10))  # eating|skipping
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# --------------------------------------------------------------------------- surplus rescue


class SurplusIncident(TimestampMixin, Base):
    __tablename__ = "surplus_incidents"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    canteen_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("canteens.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20))
    trigger: Mapped[str] = mapped_column(String(20))
    items: Mapped[list[dict]] = mapped_column(JSONB)
    total_portions: Mapped[float] = mapped_column(Float)
    total_kg: Mapped[float] = mapped_column(Float)
    safe_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    route: Mapped[str] = mapped_column(String(12), default="people")  # people|divert
    threshold: Mapped[dict] = mapped_column(JSONB, default=dict)
    pickup_point: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    impact: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    first_agreed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IncidentEvent(TimestampMixin, Base):
    __tablename__ = "incident_events"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("surplus_incidents.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
    actor: Mapped[str | None] = mapped_column(String(120))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class Donation(TimestampMixin, Base):
    """Food given (or agreed to be given) to one place. The place is a snapshot: nearby places
    come from OpenStreetMap at the moment of the rescue, they are not accounts in FoodWise."""

    __tablename__ = "donations"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUIDCol, ForeignKey("surplus_incidents.id", ondelete="CASCADE"), index=True
    )
    place_ref: Mapped[str] = mapped_column(String(80), index=True)  # osm:node/123 | manual:<slug> | demo:<slug>
    place_name: Mapped[str] = mapped_column(String(200))
    place_kind: Mapped[str] = mapped_column(String(30))
    place_lat: Mapped[float | None] = mapped_column(Float)
    place_lon: Mapped[float | None] = mapped_column(Float)
    place_phone: Mapped[str | None] = mapped_column(String(40))
    place_address: Mapped[str | None] = mapped_column(Text)
    distance_km: Mapped[float | None] = mapped_column(Float)
    portions: Mapped[float] = mapped_column(Float)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="agreed")  # agreed | handed_over | fell_through
    contact_method: Mapped[str] = mapped_column(String(16), default="call")  # call|whatsapp|in_person|other
    contact_name: Mapped[str | None] = mapped_column(String(120))
    notes: Mapped[str | None] = mapped_column(Text)
    handed_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)


class PlaceCache(Base):
    """OpenStreetMap search results by rounded area. Public data only; no organisation is stored."""

    __tablename__ = "place_cache"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    results: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- platform plumbing


class Job(TimestampMixin, Base):
    """PostgreSQL-backed job queue (claimed with FOR UPDATE SKIP LOCKED)."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_ready", "status", "run_at"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID | None] = _tenant_fk(nullable=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="queued")  # queued|running|succeeded|failed|dead
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSONB)
    progress: Mapped[dict | None] = mapped_column(JSONB)
    locked_by: Mapped[str | None] = mapped_column(String(80))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceCheck(Base):
    """Calls to external services, aggregated per hour (written by the API and the workers)."""

    __tablename__ = "service_checks"
    __table_args__ = (UniqueConstraint("service", "hour"),)
    id: Mapped[uuid.UUID] = _pk()
    service: Mapped[str] = mapped_column(String(40))
    hour: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    calls: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    total_ms: Mapped[float] = mapped_column(Float, default=0)
    max_ms: Mapped[float] = mapped_column(Float, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    jobs_done: Mapped[int] = mapped_column(Integer, default=0)
    jobs_failed: Mapped[int] = mapped_column(Integer, default=0)
    current_job: Mapped[str | None] = mapped_column(String(80))


class WebVital(Base):
    """Real-user performance samples from the web app (Core Web Vitals). No user or tenant id."""

    __tablename__ = "web_vitals"
    __table_args__ = (Index("ix_web_vitals_at", "at"),)
    id: Mapped[uuid.UUID] = _pk()
    metric: Mapped[str] = mapped_column(String(8))  # LCP | INP | CLS | FCP | TTFB
    value: Mapped[float] = mapped_column(Float)
    rating: Mapped[str] = mapped_column(String(20))
    route: Mapped[str] = mapped_column(String(120))
    connection: Mapped[str | None] = mapped_column(String(12))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """Append-only: a trigger rejects UPDATE and DELETE."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_tenant_at", "tenant_id", "at"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID | None] = _tenant_fk(nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUIDCol)
    actor_label: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(80))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OutboxMessage(TimestampMixin, Base):
    """Every notification FoodWise sends (or would send, in development)."""

    __tablename__ = "outbox_messages"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID | None] = _tenant_fk(nullable=True)
    channel: Mapped[str] = mapped_column(String(12))
    to_address: Mapped[str] = mapped_column(String(254))
    recipient_label: Mapped[str | None] = mapped_column(String(200))
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="queued")
    provider: Mapped[str] = mapped_column(String(30), default="outbox")
    error: Mapped[str | None] = mapped_column(Text)
    related_type: Mapped[str | None] = mapped_column(String(30))
    related_id: Mapped[str | None] = mapped_column(String(80))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Webhook(TimestampMixin, Base):
    __tablename__ = "webhooks"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    url: Mapped[str] = mapped_column(String(500))
    secret: Mapped[str] = mapped_column(String(128))
    events: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class WebhookDelivery(TimestampMixin, Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    webhook_id: Mapped[uuid.UUID] = mapped_column(UUIDCol, ForeignKey("webhooks.id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    response_code: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


# Tables whose rows belong to exactly one tenant. The migration attaches RLS policies to these.
TENANT_TABLES = [
    "users",
    "campuses",
    "canteens",
    "dishes",
    "menu_entries",
    "calendar_days",
    "weather_days",
    "service_logs",
    "live_sessions",
    "import_jobs",
    "model_versions",
    "forecasts",
    "feedback",
    "votes",
    "rsvps",
    "surplus_incidents",
    "incident_events",
    "donations",
    "jobs",
    "audit_logs",
    "outbox_messages",
    "webhooks",
    "webhook_deliveries",
]

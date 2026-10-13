"""Engine, sessions and the tenant context that drives PostgreSQL row-level security.

Every tenant-owned table has a policy of the form

    tenant_id = current_setting('app.tenant_id')::uuid  OR  current_setting('app.bypass_rls') = 'on'

and FORCE ROW LEVEL SECURITY, so even the table owner is filtered. The settings are
transaction-local; `_apply_tenant_context` re-applies them at the start of every
transaction a session opens, so a session can never "forget" its tenant after a commit.
A session with neither a tenant nor bypass sees no tenant rows at all (fails closed).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import DateTime, MetaData, create_engine, event, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from foodwise.config import get_settings

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def new_id() -> uuid.UUID:
    return uuid.uuid4()


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            future=True,
        )
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=True)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (tests switch databases)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@event.listens_for(Session, "after_begin")
def _apply_tenant_context(session: Session, transaction, connection) -> None:
    tenant_id = session.info.get("tenant_id")
    bypass = bool(session.info.get("bypass_rls"))
    connection.exec_driver_sql(
        "SELECT set_config('app.tenant_id', %s, true), set_config('app.bypass_rls', %s, true)",
        (str(tenant_id) if tenant_id else "", "on" if bypass else "off"),
    )


@contextmanager
def tenant_session(tenant_id: uuid.UUID | str | None) -> Iterator[Session]:
    """A session that can only see one tenant's rows (plus global tables)."""
    with session_factory()() as session:
        session.info["tenant_id"] = str(tenant_id) if tenant_id else None
        yield session


@contextmanager
def system_session() -> Iterator[Session]:
    """A session that bypasses RLS. Only for the worker, platform admin and token-verified flows."""
    with session_factory()() as session:
        session.info["bypass_rls"] = True
        yield session

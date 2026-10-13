"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from prometheus_client import Counter, Histogram
from sqlalchemy import select

from foodwise import tasks  # noqa: F401  (registers job handlers for inline mode)
from foodwise.api import (
    auth,
    catalog,
    feedback,
    forecasts,
    health,
    impact,
    imports,
    incidents,
    live,
    logs,
    media,
    models,
    ops,
    places,
    public,
    tenant,
    webhooks,
)
from foodwise.config import get_settings
from foodwise.core.errors import install_error_handlers
from foodwise.core.events import broadcaster
from foodwise.core.logging import configure_logging, request_id_var, tenant_id_var
from foodwise.core.telemetry import http as http_telemetry
from foodwise.db.base import system_session
from foodwise.db.models import User

log = logging.getLogger("foodwise.api")
API_PREFIX = "/api/v1"

REQUESTS = Counter("foodwise_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("foodwise_http_request_seconds", "HTTP latency", ["method", "route"],
                    buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4))

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(self), camera=(self), microphone=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def _refuse_seed_accounts_in_production() -> None:
    if get_settings().env != "production":
        return
    with system_session() as db:
        if db.execute(select(User.id).where(User.is_seed.is_(True)).limit(1)).first():
            raise RuntimeError("Seeded demo accounts exist. Remove them before starting in production.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _refuse_seed_accounts_in_production()
    broadcaster.start(asyncio.get_running_loop())
    stop_worker = None
    if settings.inline_worker:
        from foodwise.worker import start_background

        stop_worker = start_background()
        log.info("inline worker running inside the API process")
    yield
    broadcaster.stop()
    if stop_worker is not None:
        stop_worker.set()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="FoodWise API",
        version="1.0.0",
        description=(
            "Demand forecasting, student feedback and surplus rescue for college canteens and hostel messes. "
            "All tenant data is scoped by the caller's token and enforced by PostgreSQL row-level security."
        ),
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    install_error_handlers(app)
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key", "X-Device-ID"],
    )

    @app.middleware("http")
    async def _observe(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request_id_var.set(rid)
        tenant_id_var.set(None)
        start = time.perf_counter()
        if int(request.headers.get("content-length") or 0) > 12 * 1024 * 1024:
            from fastapi.responses import JSONResponse

            return JSONResponse({"error": {"code": "too_large", "message": "Request body is larger than 12 MB."}},
                                status_code=413)
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        # Routers are included lazily, so a matched route's path lacks the "/api/v1" prefix it is
        # mounted under. Report the full pattern so dashboards match the URLs people see.
        if path != "unmatched" and request.url.path.startswith(API_PREFIX) and not path.startswith(API_PREFIX):
            path = API_PREFIX + path
        REQUESTS.labels(request.method, path, response.status_code).inc()
        LATENCY.labels(request.method, path).observe(elapsed)
        if path.startswith("/api/"):
            http_telemetry.record(request.method, path, response.status_code, elapsed * 1000, rid)
        response.headers["X-Request-ID"] = rid
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if path not in ("/health/live", "/health/ready", "/metrics"):
            log.info("request", extra={"method": request.method, "path": request.url.path,
                                       "status": response.status_code, "duration_ms": round(elapsed * 1000, 1)})
        return response

    api = API_PREFIX
    for module in (auth, tenant, catalog, logs, imports, forecasts, models, feedback, incidents, places, impact,
                   live, webhooks, ops):
        app.include_router(module.router, prefix=api)
    app.include_router(ops.telemetry_router, prefix=api)
    app.include_router(public.router, prefix=api)
    app.include_router(media.router, prefix=api)
    app.include_router(health.router)
    return app


app = create_app()

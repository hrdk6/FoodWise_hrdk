"""Operational telemetry for the admin console.

* HTTP traffic: per-minute buckets kept in the API process for 24 hours (count, client and
  server errors, a latency histogram per route), plus the most recent errors and slow
  requests. Prometheus at /metrics exposes the same counters for a real monitoring stack.
* External services (OpenStreetMap, Wikimedia, Open-Meteo): every call is counted per hour in
  `service_checks`, so calls made by the API and by background workers are both visible.

Nothing here records who made a request or which organisation it was for.
"""

from __future__ import annotations

import bisect
import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

log = logging.getLogger("foodwise.telemetry")

BUCKETS_MS = [5, 10, 25, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000, 30000]
KEEP_MINUTES = 24 * 60
SLOW_MS = 800
STREAM_SUFFIX = "/events"  # long-lived server-sent event streams are counted, not timed


@dataclass
class RouteStats:
    count: int = 0
    client_errors: int = 0
    server_errors: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0
    hist: list[int] = field(default_factory=lambda: [0] * (len(BUCKETS_MS) + 1))

    def add(self, status: int, ms: float | None) -> None:
        self.count += 1
        if 400 <= status < 500:
            self.client_errors += 1
        elif status >= 500:
            self.server_errors += 1
        if ms is not None:
            self.total_ms += ms
            self.max_ms = max(self.max_ms, ms)
            self.hist[bisect.bisect_left(BUCKETS_MS, ms)] += 1

    def merge(self, other: RouteStats) -> None:
        self.count += other.count
        self.client_errors += other.client_errors
        self.server_errors += other.server_errors
        self.total_ms += other.total_ms
        self.max_ms = max(self.max_ms, other.max_ms)
        self.hist = [a + b for a, b in zip(self.hist, other.hist, strict=True)]

    @property
    def timed(self) -> int:
        return sum(self.hist)

    def quantile(self, q: float) -> float | None:
        n = self.timed
        if n == 0:
            return None
        target, seen = q * n, 0
        for i, c in enumerate(self.hist):
            if c and seen + c >= target:
                lo = BUCKETS_MS[i - 1] if i > 0 else 0
                hi = BUCKETS_MS[i] if i < len(BUCKETS_MS) else max(self.max_ms, lo)
                return round(lo + (hi - lo) * (target - seen) / c, 1)
            seen += c
        return round(self.max_ms, 1)


class HttpTelemetry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._minutes: dict[int, dict[str, RouteStats]] = {}
        self.errors: deque[dict] = deque(maxlen=100)
        self.slow: deque[dict] = deque(maxlen=60)
        self.started_at = datetime.now(UTC)

    def record(self, method: str, route: str, status: int, ms: float, request_id: str, code: str | None = None) -> None:
        minute = int(time.time() // 60)
        key = f"{method} {route}"
        timed = None if route.endswith(STREAM_SUFFIX) else ms
        with self._lock:
            bucket = self._minutes.setdefault(minute, {})
            bucket.setdefault(key, RouteStats()).add(status, timed)
            if len(self._minutes) > KEEP_MINUTES:
                for old in sorted(self._minutes)[: len(self._minutes) - KEEP_MINUTES]:
                    del self._minutes[old]
        at = datetime.now(UTC).isoformat()
        if status >= 500:
            self.errors.appendleft({"at": at, "route": key, "status": status, "code": code, "request_id": request_id,
                                    "ms": round(ms, 1)})
        if timed is not None and timed >= SLOW_MS:
            self.slow.appendleft({"at": at, "route": key, "status": status, "ms": round(ms, 1), "request_id": request_id})

    def window(self, minutes: int) -> tuple[list[dict], dict[str, RouteStats], RouteStats]:
        """Per-minute series, per-route totals and the overall total for the last `minutes`."""
        now = int(time.time() // 60)
        with self._lock:
            snap = {m: dict(b) for m, b in self._minutes.items() if m > now - minutes}
        series, routes, total = [], {}, RouteStats()
        for m in range(now - minutes + 1, now + 1):
            minute_total = RouteStats()
            for key, st in snap.get(m, {}).items():
                minute_total.merge(st)
                routes.setdefault(key, RouteStats()).merge(st)
            total.merge(minute_total)
            series.append({
                "at": datetime.fromtimestamp(m * 60, UTC).isoformat(), "requests": minute_total.count,
                "server_errors": minute_total.server_errors, "client_errors": minute_total.client_errors,
                "p50_ms": minute_total.quantile(0.5), "p95_ms": minute_total.quantile(0.95),
            })
        return series, routes, total


http = HttpTelemetry()


# --------------------------------------------------------------------------- external services


def record_external(service: str, ok: bool, ms: float, error: str | None = None) -> None:
    """Count one call to an external service. Never raises: telemetry must not break a request."""
    from foodwise.db.base import system_session
    from foodwise.db.models import ServiceCheck

    now = datetime.now(UTC)
    hour = now.replace(minute=0, second=0, microsecond=0)
    try:
        with system_session() as db:
            t = ServiceCheck.__table__
            stmt = insert(t).values(
                id=uuid.uuid4(), service=service, hour=hour, calls=1, failures=0 if ok else 1,
                total_ms=ms, max_ms=ms, last_error=None if ok else (error or "error")[:500],
                last_error_at=None if ok else now, last_ok_at=now if ok else None,
            )
            ex = stmt.excluded
            db.execute(stmt.on_conflict_do_update(
                index_elements=["service", "hour"],
                set_={
                    "calls": t.c.calls + 1,
                    "failures": t.c.failures + (0 if ok else 1),
                    "total_ms": t.c.total_ms + ms,
                    "max_ms": func.greatest(t.c.max_ms, ms),
                    "last_error": func.coalesce(ex.last_error, t.c.last_error),
                    "last_error_at": func.coalesce(ex.last_error_at, t.c.last_error_at),
                    "last_ok_at": func.coalesce(ex.last_ok_at, t.c.last_ok_at),
                },
            ))
            db.commit()
    except Exception:  # noqa: BLE001
        log.debug("could not record external call", exc_info=True)


@contextmanager
def external(service: str) -> Iterator[None]:
    """`with external("overpass"): ...` times the block and records success or the error."""
    start = time.perf_counter()
    try:
        yield
    except Exception as exc:
        record_external(service, False, (time.perf_counter() - start) * 1000, f"{type(exc).__name__}: {exc}")
        raise
    record_external(service, True, (time.perf_counter() - start) * 1000)

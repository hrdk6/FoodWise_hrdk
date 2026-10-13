"""Sliding-window rate limiter for public and login endpoints.

In-process by design: it protects each API replica. Behind a load balancer, pair it with
edge rate limiting at the reverse proxy for a global ceiling.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from foodwise.core.errors import TooManyRequests


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: float) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= limit:
                retry = max(1, int(window_s - (now - q[0])))
                raise TooManyRequests(
                    f"Too many requests. Try again in {retry} seconds.", details={"retry_after": retry}
                )
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()

"""`foodwise worker`: runs queued jobs and the scheduler. Run as many replicas as you like."""

from __future__ import annotations

import logging
import signal
import threading
import time

from foodwise import tasks  # noqa: F401  (registers job handlers)
from foodwise.core import jobs

log = logging.getLogger("foodwise.worker")

SCHEDULE_EVERY_S = 30
RECOVER_EVERY_S = 300
HEARTBEAT_EVERY_S = 20


def loop(stop: threading.Event, worker_id: str | None = None) -> None:
    wid = worker_id or jobs.worker_id()
    last_schedule = last_recover = last_beat = 0.0
    log.info("worker %s started", wid)
    while not stop.is_set():
        now = time.monotonic()
        try:
            if now - last_beat > HEARTBEAT_EVERY_S:
                jobs.heartbeat(wid)
                last_beat = now
            if now - last_recover > RECOVER_EVERY_S:
                if n := jobs.recover_stale():
                    log.warning("requeued %d stale jobs", n)
                last_recover = now
            if now - last_schedule > SCHEDULE_EVERY_S:
                tasks.schedule_tick()
                last_schedule = now
            if not jobs.run_one(wid):
                stop.wait(1.0)
        except Exception:  # noqa: BLE001 - the loop must survive database restarts
            log.exception("worker loop error; retrying shortly")
            stop.wait(5.0)
    log.info("worker %s stopped", wid)


def run() -> None:
    stop = threading.Event()

    def _stop(*_):
        stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    loop(stop)


def start_background() -> threading.Event:
    """Inline mode: the API process also runs a worker thread (FOODWISE_INLINE_WORKER=true)."""
    stop = threading.Event()
    threading.Thread(target=loop, args=(stop,), name="inline-worker", daemon=True).start()
    return stop

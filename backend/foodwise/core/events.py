"""Live updates via PostgreSQL LISTEN/NOTIFY.

Writers call `publish()` inside their transaction; PostgreSQL delivers the notification on
commit to every API replica. Each replica runs one listener thread that wakes the matching
server-sent-event streams. Streams always re-read state from the database, so a missed
notification only delays an update (by the poll interval) and never loses it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import defaultdict

import psycopg
from sqlalchemy import text
from sqlalchemy.orm import Session

from foodwise.config import get_settings

log = logging.getLogger("foodwise.events")
CHANNEL = "foodwise_events"


def publish(db: Session, topic: str, data: dict | None = None) -> None:
    payload = json.dumps({"topic": topic, **(data or {})}, default=str)
    db.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": payload[:7900]})


class Broadcaster:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._thread = threading.Thread(target=self._listen, name="pg-listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def subscribe(self, topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        with self._lock:
            self._subs[topic].add(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs[topic].discard(q)

    def _dispatch(self, topic: str, payload: dict) -> None:
        with self._lock:
            queues = list(self._subs.get(topic, ()))
        for q in queues:
            if not q.full():
                q.put_nowait(payload)

    def _listen(self) -> None:
        dsn = get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")
        while not self._stop.is_set():
            try:
                with psycopg.connect(dsn, autocommit=True) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    log.info("listening for live events")
                    while not self._stop.is_set():
                        for note in conn.notifies(timeout=2.0):
                            try:
                                payload = json.loads(note.payload)
                            except ValueError:
                                continue
                            topic = payload.get("topic")
                            if topic and self._loop:
                                self._loop.call_soon_threadsafe(self._dispatch, topic, payload)
            except psycopg.Error as exc:
                log.warning("event listener reconnecting: %s", exc)
                self._stop.wait(3.0)


broadcaster = Broadcaster()

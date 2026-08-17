from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass

from .config import settings

log = logging.getLogger("scout.watchdog")


@dataclass
class EventLoopWatchdog:
    """Independent liveness guard for the asyncio event loop.

    Docker's restart policy only acts when the process exits. A Python process
    can stay alive while the event loop is starved, which is exactly the
    failure mode this guard is designed to recover from. The monitor lives in a
    daemon thread and hard-exits only after the async heartbeat has been stale
    beyond the configured threshold and startup grace period.
    """

    stale_seconds: float = float(settings.event_loop_watchdog_seconds)
    grace_seconds: float = float(settings.event_loop_watchdog_grace_seconds)

    def __post_init__(self) -> None:
        now = time.monotonic()
        self._started_at = now
        self._last_beat = now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.max_lag_seconds = 0.0
        self.recoveries = 0

    def beat(self) -> None:
        now = time.monotonic()
        lag = max(0.0, now - self._last_beat)
        self.max_lag_seconds = max(self.max_lag_seconds, lag)
        self._last_beat = now

    async def heartbeat(self) -> None:
        while True:
            self.beat()
            await asyncio.sleep(1.0)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._monitor, name="scout-event-loop-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, float | int | bool]:
        now = time.monotonic()
        return {
            "enabled": self.stale_seconds > 0,
            "heartbeat_age_seconds": round(max(0.0, now - self._last_beat), 3),
            "max_lag_seconds": round(self.max_lag_seconds, 3),
            "threshold_seconds": self.stale_seconds,
            "recoveries": self.recoveries,
        }

    def _monitor(self) -> None:
        while not self._stop.wait(2.0):
            now = time.monotonic()
            if now - self._started_at < self.grace_seconds:
                continue
            age = now - self._last_beat
            if age <= self.stale_seconds:
                continue
            self.recoveries += 1
            # os._exit is deliberate: graceful asyncio shutdown cannot be
            # trusted when the event loop itself is wedged. Docker's
            # restart: unless-stopped will bring Scout back cleanly.
            try:
                os.write(2, f"SCOUT WATCHDOG: event loop stalled for {age:.1f}s; forcing process restart\n".encode())
            finally:
                os._exit(70)

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import settings

log = logging.getLogger("scout.hybrid")


@dataclass
class HybridObservation:
    ticker: str
    source: str
    detected_at: float
    stage: str


@dataclass
class HybridMemory:
    """Small in-memory correlation layer for Rust/Python candidate provenance.

    Detection logic remains owned by the individual engines. This object only
    answers whether both engines have observed the same ticker within the
    configured merge window and whether a Rust awakening is redundant with a
    very recent Python notification.
    """

    merge_window_seconds: float = 45.0
    dedupe_seconds: float = 20.0
    recent: dict[str, dict[str, HybridObservation]] = field(default_factory=dict)
    episode_gap_seconds: float = 900.0
    _episode_state: dict[str, tuple[str, int, float]] = field(default_factory=dict)

    def observe(self, ticker: str, source: str, detected_at: float, stage: str) -> list[str]:
        ticker = ticker.upper()
        source = source.lower()
        slot = self.recent.setdefault(ticker, {})
        slot[source] = HybridObservation(ticker, source, float(detected_at), stage)
        cutoff = float(detected_at) - self.merge_window_seconds
        for key, observation in list(slot.items()):
            if observation.detected_at < cutoff:
                slot.pop(key, None)
        return sorted(slot)

    def recent_other(self, ticker: str, source: str, detected_at: float) -> HybridObservation | None:
        slot = self.recent.get(ticker.upper(), {})
        other_source = "python" if source.lower() == "rust" else "rust"
        observation = slot.get(other_source)
        if observation and abs(float(detected_at) - observation.detected_at) <= self.merge_window_seconds:
            return observation
        return None

    def rust_notification_is_duplicate(self, ticker: str, detected_at: float) -> bool:
        observation = self.recent.get(ticker.upper(), {}).get("python")
        return bool(observation and 0 <= float(detected_at) - observation.detected_at <= self.dedupe_seconds)

    def episode_key(self, ticker: str, session_key: str, detected_at: float) -> str:
        ticker = ticker.upper()
        previous = self._episode_state.get(ticker)
        if previous is None or previous[0] != session_key or float(detected_at) - previous[2] > self.episode_gap_seconds:
            sequence = 0 if previous is None or previous[0] != session_key else previous[1] + 1
        else:
            sequence = previous[1]
        self._episode_state[ticker] = (session_key, sequence, float(detected_at))
        return f"{ticker}:{session_key}:{sequence}"

    def restore_episode(self, ticker: str, session_key: str, sequence: int, detected_at: float) -> None:
        self._episode_state[ticker.upper()] = (session_key, max(0, int(sequence)), float(detected_at))


CandidateHandler = Callable[[dict[str, Any]], Awaitable[None]]


class RustPerceptionBridge:
    """Long-lived JSONL bridge to the validated Rust perception core.

    Market trades are queued without blocking the Python websocket loop. The
    Rust process owns its own rolling state and emits only state-transition
    candidates. If it is unavailable, Python Scout remains operational and the
    health endpoint reports the degraded hybrid state.
    """

    def __init__(self, candidate_handler: CandidateHandler):
        self.candidate_handler = candidate_handler
        self.binary = Path(settings.rust_perception_binary)
        self.process_args: list[str] = ["--stream"]
        self.enabled = bool(settings.hybrid_enabled)
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=settings.rust_bridge_queue_max)
        self.process: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task] = []
        self._sequence = itertools.count(1)
        self.started_at: float | None = None
        self.last_candidate_at: float | None = None
        self.last_submit_at: float | None = None
        self.last_error: str | None = None
        self.restarts = 0
        self.submitted = 0
        self.dropped = 0
        self.candidates = 0
        self.written = 0
        self.writer_batches = 0
        self.max_queue_depth = 0
        self._last_quote_submit_at: dict[str, float] = {}

    async def start(self) -> None:
        if not self.enabled or self._tasks:
            return
        self.started_at = time.time()
        self._tasks = [
            asyncio.create_task(self._supervisor(), name="scout-rust-perception-supervisor"),
        ]

    async def stop(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._stop_process()

    def submit_trade(self, *, symbol: str, ts: float, price: float, size: float, feed: str) -> bool:
        return self._submit_event(
            event_type="trade", symbol=symbol, ts=ts, feed=feed,
            payload={"price": float(price), "size": max(0.0, float(size))},
        )

    def submit_quote(
        self, *, symbol: str, ts: float, bid_price: float, ask_price: float,
        bid_size: float, ask_size: float, feed: str,
    ) -> bool:
        """Forward SIP quote context without involving Python's detector hot path."""
        normalized_symbol = symbol.upper()
        previous = self._last_quote_submit_at.get(normalized_symbol, 0.0)
        if ts - previous < settings.rust_quote_min_interval_ms / 1000.0:
            return False
        midpoint = (bid_price + ask_price) / 2.0 if bid_price > 0 and ask_price > 0 else max(bid_price, ask_price)
        if midpoint <= 0:
            return False
        self._last_quote_submit_at[normalized_symbol] = ts
        return self._submit_event(
            event_type="quote", symbol=normalized_symbol, ts=ts, feed=feed,
            payload={
                "price": midpoint, "size": 0.0, "bid_price": max(0.0, bid_price),
                "ask_price": max(0.0, ask_price), "bid_size": max(0.0, bid_size),
                "ask_size": max(0.0, ask_size),
            },
        )

    def _submit_event(self, *, event_type: str, symbol: str, ts: float, feed: str, payload: dict[str, float]) -> bool:
        if not self.enabled:
            return False
        event = {
            "schema": "scout.market-event.v1",
            "event_type": event_type,
            "symbol": symbol.upper(),
            "source_ts": float(ts),
            "received_ts": time.time(),
            "sequence": next(self._sequence),
            "feed": str(feed).lower(),
            "payload": payload,
        }
        try:
            self.queue.put_nowait(event)
            self.submitted += 1
            self.last_submit_at = time.time()
            self.max_queue_depth = max(self.max_queue_depth, self.queue.qsize())
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            # Logging every dropped trade during an outage would create its own
            # failure mode. The counters are exposed through /status instead.
            return False

    async def _stop_process(self) -> None:
        process, self.process = self.process, None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    async def _supervisor(self) -> None:
        backoff = 1.0
        while True:
            try:
                if not self.binary.exists():
                    self.last_error = f"Rust perception binary not found: {self.binary}"
                    await asyncio.sleep(min(30.0, backoff))
                    backoff = min(30.0, backoff * 2.0)
                    continue
                self.process = await asyncio.create_subprocess_exec(
                    str(self.binary),
                    *self.process_args,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self.last_error = None
                if self.restarts:
                    log.warning("Rust perception bridge restarted (%d)", self.restarts)
                writer = asyncio.create_task(self._writer(self.process), name="scout-rust-writer")
                reader = asyncio.create_task(self._reader(self.process), name="scout-rust-reader")
                stderr = asyncio.create_task(self._stderr(self.process), name="scout-rust-stderr")
                returncode = await self.process.wait()
                for task in (writer, reader, stderr):
                    task.cancel()
                await asyncio.gather(writer, reader, stderr, return_exceptions=True)
                self.last_error = f"Rust perception process exited with code {returncode}"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                log.exception("Rust perception bridge failed")
            finally:
                self.process = None
            self.restarts += 1
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 2.0)

    async def _writer(self, process: asyncio.subprocess.Process) -> None:
        """Drain queued market events to Rust in ordered micro-batches.

        The JSONL contract is unchanged: Rust still receives one market-event
        envelope per line, in original queue order. The optimization is at the
        pipe boundary: one write/drain per small batch instead of one
        write/drain syscall pair per trade. Under SIP burst load this prevents
        Python's asyncio transport overhead from becoming the effective Rust
        throughput ceiling.
        """
        assert process.stdin is not None
        batch_max = max(1, int(settings.rust_bridge_batch_max))
        batch_bytes = max(4096, int(settings.rust_bridge_batch_bytes))
        while True:
            first = await self.queue.get()
            events = [first]
            encoded = [json.dumps(first, separators=(",", ":")).encode("utf-8") + b"\n"]
            size = len(encoded[0])
            try:
                while len(events) < batch_max and size < batch_bytes:
                    try:
                        event = self.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    body = json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n"
                    events.append(event)
                    encoded.append(body)
                    size += len(body)

                process.stdin.write(b"".join(encoded))
                await process.stdin.drain()
                self.written += len(events)
                self.writer_batches += 1
            finally:
                for _ in events:
                    self.queue.task_done()

    async def _reader(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        while True:
            line = await process.stdout.readline()
            if not line:
                return
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                self.candidates += 1
                self.last_candidate_at = time.time()
                await self.candidate_handler(payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Invalid Rust candidate payload: %r", line[:500])

    async def _stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            log.warning("rust-perception: %s", line.decode("utf-8", "replace").rstrip())

    def status(self) -> dict[str, Any]:
        running = bool(self.process and self.process.returncode is None)
        queue_depth = self.queue.qsize()
        queue_capacity = max(1, int(settings.rust_bridge_queue_max))
        queue_utilization = queue_depth / queue_capacity
        if queue_utilization >= 0.95:
            backpressure = "saturated"
        elif queue_utilization >= 0.75:
            backpressure = "degraded"
        else:
            backpressure = "healthy"
        return {
            "enabled": self.enabled,
            "running": running,
            "binary": str(self.binary),
            "queue_depth": queue_depth,
            "queue_capacity": queue_capacity,
            "queue_utilization": round(queue_utilization, 4),
            "backpressure": backpressure,
            "submitted": self.submitted,
            "written": self.written,
            "writer_batches": self.writer_batches,
            "writer_avg_batch": round(self.written / self.writer_batches, 2) if self.writer_batches else 0.0,
            "max_queue_depth": self.max_queue_depth,
            "dropped": self.dropped,
            "candidates": self.candidates,
            "restarts": self.restarts,
            "started_at": self.started_at,
            "last_submit_at": self.last_submit_at,
            "last_candidate_at": self.last_candidate_at,
            "last_error": self.last_error,
        }

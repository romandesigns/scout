from __future__ import annotations

import asyncio
import logging
import itertools
from collections.abc import Awaitable, Callable

from .charts import render_detection_chart
from .config import settings
from .db import Store
from .models import Bucket, Finding
from .events import EventHub
from .notifiers import channel_rate_limited, notification_allowed, notification_allowed_any_platform, notification_phase, send_ntfy, send_ntfy_chart, send_resend_email, send_web_push_all

log = logging.getLogger("scout.dispatch")


class Dispatcher:
    def __init__(self, store: Store, events: EventHub | None = None):
        self.store = store
        self.events = events
        self.snapshot_provider: Callable[[str], tuple[list[Bucket], Bucket | None] | None] | None = None
        self.finding_listener: Callable[[int, Finding], None] | None = None
        self.trade_listener: Callable[[int, Finding], Awaitable[None]] | None = None
        self._sequence = itertools.count()
        self._ntfy_queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=settings.notification_queue_max)
        self._email_queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=settings.notification_queue_max)
        self._webpush_queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=settings.notification_queue_max)
        self._workers_started = False
        self._pending_ntfy: dict[str, tuple[int, Finding, dict]] = {}
        self._pending_ntfy_tasks: dict[str, asyncio.Task] = {}
        self._pending_email: dict[str, tuple[int, Finding, dict]] = {}
        self._pending_email_tasks: dict[str, asyncio.Task] = {}
        self._notified_episode_phases: set[tuple[str, str, str, str]] = set()

    def _claim_episode_phase(self, channel: str, f: Finding, prefs: dict) -> bool:
        """Allow at most one setup and one confirmation per symbol episode.

        Special events such as halts remain event-driven. The preference existed
        previously but was not enforced anywhere in the delivery path.
        """
        phase = notification_phase(f)
        if not prefs.get("only_stage_escalations", True) or phase not in {"setup", "confirmed"}:
            return True
        episode = f.hybrid_key or f"{f.ticker.upper()}:{int(f.episode_id)}"
        key = (channel, f.ticker.upper(), episode, phase)
        if key in self._notified_episode_phases:
            return False
        self._notified_episode_phases.add(key)
        return True

    @staticmethod
    def _stage_priority(stage: str) -> int:
        return {
            "ACTIVITY_WATCH": 0, "REVERSAL_WATCH": 0, "FIRST_LEG_WATCH": 0, "PRE_IGNITION": 0,
            "AWAKENING": 2, "FIRST_LEG": 3, "EARLY": 2, "STAIRCASE": 2, "EMA_RECLAIM": 3,
            "SURGE": 3, "VWAP_RECLAIM": 3, "BREAKOUT": 4,
            "REARM": 5, "IGNITION": 6, "CATALYST": 7, "CATALYST_WATCH": 7, "CATALYST_ACTIVE": 9,
            "RESUME": 8, "HALT": 9,
            "HALT_WATCH": 7, "HALT_PRESSURE": 8,
        }.get(stage, 1)

    def _ensure_workers(self) -> None:
        if self._workers_started:
            return
        self._workers_started = True
        asyncio.create_task(self._notification_worker("ntfy"), name="scout-ntfy-worker")
        asyncio.create_task(self._notification_worker("email"), name="scout-email-worker")
        asyncio.create_task(self._notification_worker("webpush"), name="scout-webpush-worker")

    async def _queue(self, channel: str, finding_id: int, f: Finding, prefs: dict) -> None:
        if channel == "ntfy" and channel_rate_limited("ntfy"):
            await asyncio.to_thread(self.store.record_delivery, finding_id, channel, "suppressed", "provider circuit breaker active")
            return
        queue = self._ntfy_queue if channel == "ntfy" else self._webpush_queue if channel == "webpush" else self._email_queue
        # Stage urgency must dominate evidence score. A score-10 watch can no
        # longer crowd out a FIRST_LEG, ignition, catalyst, or halt.
        item = (-self._stage_priority(f.stage), -int(f.hybrid_score or 0), -int(f.score), next(self._sequence), finding_id, f, prefs)
        try:
            queue.put_nowait(item)
            await asyncio.to_thread(self.store.record_delivery, finding_id, channel, "queued")
        except asyncio.QueueFull:
            log.error("%s notification queue full; finding %s %s remains persisted", channel, f.ticker, f.stage)
            await asyncio.to_thread(self.store.record_delivery, finding_id, channel, "queue_failed", "notification queue full")

    async def _notification_worker(self, channel: str) -> None:
        queue = self._ntfy_queue if channel == "ntfy" else self._webpush_queue if channel == "webpush" else self._email_queue
        while True:
            _, _, _, _, finding_id, f, prefs = await queue.get()
            try:
                await asyncio.to_thread(self.store.record_delivery, finding_id, channel, "sending")
                if channel == "webpush":
                    await asyncio.to_thread(send_web_push_all, self.store, f, prefs)
                else:
                    fn = send_ntfy if channel == "ntfy" else send_resend_email
                    await asyncio.to_thread(fn, f, prefs)
                await asyncio.to_thread(self.store.record_delivery, finding_id, channel, "provider_accepted")
            except Exception as exc:
                await asyncio.to_thread(self.store.record_delivery, finding_id, channel, "failed", str(exc))
                log.exception("%s worker failed for finding %s %s %s", channel, finding_id, f.ticker, f.stage)
                if channel == "webpush" and settings.ntfy_topic:
                    await asyncio.to_thread(self.store.record_delivery, finding_id, "ntfy", "fallback_queued", "Web Push failed")
                    await self._queue("ntfy", finding_id, f, prefs)
            finally:
                queue.task_done()

    @staticmethod
    def _merge_notification_context(preferred: Finding, other: Finding) -> Finding:
        preferred.hybrid_sources = sorted(set((preferred.hybrid_sources or [preferred.engine_source]) + (other.hybrid_sources or [other.engine_source])))
        preferred.hybrid_score = max(preferred.hybrid_score, other.hybrid_score)
        if len(preferred.hybrid_sources) > 1:
            preferred.hybrid_score = min(100, preferred.hybrid_score + 10)
            preferred.notification_reason = "dual-engine confirmation with lifecycle priority"
        preferred.signals = list(dict.fromkeys([*(preferred.signals or []), *(other.signals or [])]))
        for evidence in other.evidence or []:
            if evidence not in preferred.evidence:
                preferred.evidence.append(evidence)
        return preferred

    async def _flush_consolidated_ntfy(self, ticker: str) -> None:
        pending = self._pending_ntfy.get(ticker)
        delay = settings.first_leg_notification_consolidation_seconds if pending and pending[1].stage == "FIRST_LEG" else settings.notification_consolidation_seconds
        await asyncio.sleep(delay)
        pending = self._pending_ntfy.pop(ticker, None)
        self._pending_ntfy_tasks.pop(ticker, None)
        if pending:
            finding_id, finding, prefs = pending
            await self._queue("ntfy", finding_id, finding, prefs)

    async def _queue_consolidated_ntfy(self, finding_id: int, f: Finding, prefs: dict) -> None:
        if settings.notification_consolidation_seconds <= 0 or f.stage in {"HALT", "RESUME", "CATALYST", "CATALYST_WATCH", "CATALYST_ACTIVE"}:
            await self._queue("ntfy", finding_id, f, prefs)
            return
        ticker = f.ticker.upper()
        current = self._pending_ntfy.get(ticker)
        if current is None:
            self._pending_ntfy[ticker] = (finding_id, f, prefs)
        elif self._stage_priority(f.stage) >= self._stage_priority(current[1].stage):
            self._pending_ntfy[ticker] = (finding_id, self._merge_notification_context(f, current[1]), prefs)
        else:
            current_id, current_finding, current_prefs = current
            self._pending_ntfy[ticker] = (current_id, self._merge_notification_context(current_finding, f), current_prefs)
        if ticker not in self._pending_ntfy_tasks:
            self._pending_ntfy_tasks[ticker] = asyncio.create_task(
                self._flush_consolidated_ntfy(ticker), name=f"scout-ntfy-consolidate-{ticker}"
            )

    async def _flush_consolidated_email(self, ticker: str) -> None:
        await asyncio.sleep(settings.notification_consolidation_seconds)
        pending = self._pending_email.pop(ticker, None)
        self._pending_email_tasks.pop(ticker, None)
        if pending:
            finding_id, finding, prefs = pending
            await self._queue("email", finding_id, finding, prefs)

    async def _queue_consolidated_email(self, finding_id: int, f: Finding, prefs: dict) -> None:
        if settings.notification_consolidation_seconds <= 0 or f.stage in {"HALT", "RESUME", "CATALYST", "CATALYST_WATCH", "CATALYST_ACTIVE"}:
            await self._queue("email", finding_id, f, prefs)
            return
        ticker = f.ticker.upper()
        current = self._pending_email.get(ticker)
        if current is None:
            self._pending_email[ticker] = (finding_id, f, prefs)
        elif self._stage_priority(f.stage) >= self._stage_priority(current[1].stage):
            self._pending_email[ticker] = (finding_id, self._merge_notification_context(f, current[1]), prefs)
        else:
            current_id, current_finding, current_prefs = current
            self._pending_email[ticker] = (current_id, self._merge_notification_context(current_finding, f), current_prefs)
        if ticker not in self._pending_email_tasks:
            self._pending_email_tasks[ticker] = asyncio.create_task(
                self._flush_consolidated_email(ticker), name=f"scout-email-consolidate-{ticker}"
            )

    def notification_queue_status(self) -> dict[str, int]:
        return {
            "webpush": self._webpush_queue.qsize(),
            "ntfy": self._ntfy_queue.qsize(),
            "resend": self._email_queue.qsize(),
            "pending_ntfy_consolidations": len(self._pending_ntfy),
            "pending_email_consolidations": len(self._pending_email),
        }

    def set_snapshot_provider(self, provider: Callable[[str], tuple[list[Bucket], Bucket | None] | None]) -> None:
        self.snapshot_provider = provider

    def set_finding_listener(self, listener: Callable[[int, Finding], None]) -> None:
        self.finding_listener = listener

    def set_trade_listener(self, listener: Callable[[int, Finding], Awaitable[None]]) -> None:
        self.trade_listener = listener

    async def emit(self, f: Finding, buckets: list[Bucket] | None = None, current: Bucket | None = None) -> int:
        # Persist + push first. Rendering/email must never block the first alert.
        finding_id = await asyncio.to_thread(self.store.save_finding, f)
        f.finding_id = finding_id
        if self.finding_listener:
            try:
                self.finding_listener(finding_id, f)
            except Exception:
                log.exception("finding listener failed for %s %s", f.ticker, f.stage)
        if self.trade_listener:
            asyncio.create_task(self.trade_listener(finding_id, f), name=f"scout-paper-trade-{f.ticker}")

        self._ensure_workers()
        prefs = await asyncio.to_thread(self.store.get_notification_preferences)
        # Silent/off/watch findings are persisted and streamed to the UI but
        # never occupy delivery queues.
        # Web Push can reach subscribers on any platform (it filters per-subscriber
        # internally in send_web_push_all), so its entry gate must not use the
        # android-specific check -- only ntfy is intentionally the mobile-only
        # fallback channel ("Mobile / ntfy" in Settings) and is correctly gated
        # by the android platform toggle specifically.
        if notification_allowed_any_platform(f, prefs) and self._claim_episode_phase("webpush", f, prefs) and settings.vapid_public_key and settings.vapid_private_key and await asyncio.to_thread(self.store.web_push_subscription_count) > 0:
            await self._queue("webpush", finding_id, f, prefs)
        elif notification_allowed(f, prefs, "android") and self._claim_episode_phase("ntfy", f, prefs):
            await self._queue_consolidated_ntfy(finding_id, f, prefs)
        else:
            await asyncio.to_thread(self.store.record_delivery, finding_id, "ntfy", "not_eligible")

        if self.events:
            row = await asyncio.to_thread(self.store.get_finding, finding_id)
            self.events.publish("finding", row or {
                "id": finding_id,
                "ticker": f.ticker,
                "stage": f.stage,
                "detected_at": int(f.detected_at),
                "price": f.price,
                "score": f.score,
                "signals": list(f.signals),
            })

        if buckets is None and self.snapshot_provider:
            snap = self.snapshot_provider(f.ticker)
            if snap:
                buckets, current = snap

        asyncio.create_task(self._enrich_and_email(finding_id, f, buckets or [], current, prefs))
        return finding_id

    async def _enrich_and_email(self, finding_id: int, f: Finding, buckets: list[Bucket], current: Bucket | None, prefs: dict) -> None:
        try:
            if buckets or current:
                path = await asyncio.to_thread(render_detection_chart, f, buckets, current)
                f.chart_path = path
                await asyncio.to_thread(self.store.update_chart_path, finding_id, path)
                if self.events:
                    self.events.publish("chart", {
                        "id": finding_id,
                        "ticker": f.ticker,
                        "stage": f.stage,
                        "chart_url": f"/charts/{path.rsplit('/', 1)[-1]}",
                    })
                await asyncio.to_thread(send_ntfy_chart, f, prefs)
            if notification_allowed(f, prefs, "email") and self._claim_episode_phase("email", f, prefs):
                await self._queue_consolidated_email(finding_id, f, prefs)
            else:
                await asyncio.to_thread(self.store.record_delivery, finding_id, "email", "not_eligible")
        except Exception:
            log.exception("finding enrichment/email failed for %s %s", f.ticker, f.stage)

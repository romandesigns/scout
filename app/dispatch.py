from __future__ import annotations

import asyncio
import logging
import itertools
import time
from collections.abc import Awaitable, Callable

from .charts import render_detection_chart
from .config import settings
from .db import Store
from .models import Bucket, Finding
from .events import EventHub
from .imminent_gate import score_finding as score_imminent_finding
from .notifiers import channel_rate_limited, notification_allowed, notification_allowed_any_platform, notification_ineligibility_reason, notification_phase, send_ntfy, send_ntfy_chart, send_resend_email, send_web_push_all
from .opportunity import opportunity_class
from .significance_tier import classify_tier, would_notify as preview_would_notify

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
        self._prefs_cache: dict | None = None
        self._prefs_cached_at = 0.0
        self._subscription_count_cache = 0
        self._subscription_count_cached_at = 0.0
        self._dispatch_queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=settings.dispatch_queue_max)
        self._dispatch_workers_started = False
        self._dispatch_ticker_locks: dict[str, asyncio.Lock] = {}
        self._dispatch_ticker_pending: dict[str, int] = {}
        self._dispatch_ticker_priority: dict[str, int] = {}
        self._dispatch_dropped = 0
        self._dispatch_shed_low_priority = 0

    async def _notification_preferences(self) -> dict:
        now = time.monotonic()
        if self._prefs_cache is None or now - self._prefs_cached_at >= settings.notification_preferences_cache_seconds:
            self._prefs_cache = await asyncio.to_thread(self.store.get_notification_preferences)
            self._prefs_cached_at = now
        return self._prefs_cache

    async def _webpush_subscription_count(self) -> int:
        now = time.monotonic()
        if now - self._subscription_count_cached_at >= settings.webpush_subscription_cache_seconds:
            self._subscription_count_cache = await asyncio.to_thread(self.store.web_push_subscription_count)
            self._subscription_count_cached_at = now
        return self._subscription_count_cache

    @staticmethod
    def _stale_reason(f: Finding, now: float | None = None) -> str | None:
        age = max(0.0, float(now if now is not None else time.time()) - float(f.detected_at))
        special = {"CATALYST", "CATALYST_WATCH", "CATALYST_ACTIVE", "HALT", "RESUME", "HALT_WATCH", "HALT_PRESSURE"}
        maximum = settings.notification_special_max_candidate_age_seconds if f.stage in special else settings.notification_max_candidate_age_seconds
        return f"stale_candidate age={age:.1f}s max={maximum:.1f}s" if age > maximum else None

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

    def _ensure_dispatch_workers(self) -> None:
        if self._dispatch_workers_started:
            return
        self._dispatch_workers_started = True
        for index in range(settings.dispatch_worker_count):
            asyncio.create_task(self._dispatch_worker(), name=f"scout-dispatch-worker-{index}")

    def submit(self, f: Finding, buckets: list[Bucket] | None = None, current: Bucket | None = None) -> asyncio.Future:
        """Queue persistence/delivery without blocking the market websocket.

        Workers run concurrently across symbols while a per-ticker lock preserves the
        lifecycle order for findings belonging to the same ticker.
        """
        self._ensure_dispatch_workers()
        loop = asyncio.get_running_loop()
        result = loop.create_future()
        stage_priority = self._stage_priority(f.stage)
        priority = -stage_priority
        ticker = f.ticker.upper()
        # Preserve global urgency without allowing a later lifecycle event to
        # overtake an earlier event for the same ticker. Notification queues
        # apply their own urgency after persistence has established order.
        queue_priority = self._dispatch_ticker_priority.get(ticker, priority)
        self._dispatch_ticker_priority.setdefault(ticker, queue_priority)
        self._dispatch_ticker_pending[ticker] = self._dispatch_ticker_pending.get(ticker, 0) + 1
        item = (queue_priority, next(self._sequence), f, buckets, current, result)
        low_priority_limit = int(settings.dispatch_queue_max * settings.dispatch_low_priority_max_utilization)
        if stage_priority == 0 and self._dispatch_queue.qsize() >= low_priority_limit:
            self._finish_ticker_dispatch(ticker)
            self._dispatch_shed_low_priority += 1
            result.set_exception(RuntimeError(f"dispatch backpressure shed {f.ticker} {f.stage}"))
            return result
        try:
            self._dispatch_queue.put_nowait(item)
        except asyncio.QueueFull:
            self._finish_ticker_dispatch(ticker)
            self._dispatch_dropped += 1
            result.set_exception(RuntimeError(f"dispatch queue full; dropped {f.ticker} {f.stage}"))
            log.error("dispatch queue full; dropped %s %s before persistence", f.ticker, f.stage)
        return result

    def _finish_ticker_dispatch(self, ticker: str) -> None:
        remaining = self._dispatch_ticker_pending.get(ticker, 1) - 1
        if remaining > 0:
            self._dispatch_ticker_pending[ticker] = remaining
            return
        self._dispatch_ticker_pending.pop(ticker, None)
        self._dispatch_ticker_priority.pop(ticker, None)

    async def _dispatch_worker(self) -> None:
        while True:
            _, _, finding, buckets, current, result = await self._dispatch_queue.get()
            lock = self._dispatch_ticker_locks.setdefault(finding.ticker.upper(), asyncio.Lock())
            try:
                async with lock:
                    finding_id = await self.emit(finding, buckets, current)
                if not result.cancelled():
                    result.set_result(finding_id)
            except Exception as exc:
                if not result.cancelled():
                    result.set_exception(exc)
                log.exception("background dispatch failed for %s %s", finding.ticker, finding.stage)
            finally:
                self._finish_ticker_dispatch(finding.ticker.upper())
                self._dispatch_queue.task_done()

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
            await asyncio.to_thread(self.store.record_pipeline_trace, finding_id, "notification_queued", None, channel)
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
                await asyncio.to_thread(self.store.record_pipeline_trace, finding_id, "provider_accepted", None, channel)
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
        if (
            settings.notification_consolidation_seconds <= 0
            or f.stage in {"HALT", "RESUME", "CATALYST", "CATALYST_WATCH", "CATALYST_ACTIVE"}
            or (f.actionable_rank == "A" and not f.shadow_mode)
        ):
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
            "dispatch": self._dispatch_queue.qsize(),
            "dispatch_dropped": self._dispatch_dropped,
            "dispatch_shed_low_priority": self._dispatch_shed_low_priority,
        }

    def set_snapshot_provider(self, provider: Callable[[str], tuple[list[Bucket], Bucket | None] | None]) -> None:
        self.snapshot_provider = provider

    def set_finding_listener(self, listener: Callable[[int, Finding], None]) -> None:
        self.finding_listener = listener

    def set_trade_listener(self, listener: Callable[[int, Finding], Awaitable[None]]) -> None:
        self.trade_listener = listener

    async def emit(self, f: Finding, buckets: list[Bucket] | None = None, current: Bucket | None = None) -> int:
        # Persist + push first. Rendering/email must never block the first alert.
        dispatch_started = time.time()
        trace = dict(f.trace_timestamps or {})
        trace.setdefault("source_received", float(f.detected_at))
        trace.setdefault("normalized", trace["source_received"])
        trace.setdefault("dispatch_started", dispatch_started)
        trace.setdefault("candidate_created", dispatch_started)
        f.candidate_profile = dict(f.candidate_profile or {})
        f.candidate_profile["opportunity_class"] = opportunity_class(f)
        # Advisory-only significance tiering (JUNS/WEN chart-review framework,
        # IMPLEMENTATION_DECISIONS.md 2026-08-22): never gates delivery, only
        # labels the detection for Scout Development chart review.
        f.candidate_profile["significance_tier"] = classify_tier(f)
        if settings.imminent_gate_model_path:
            f.candidate_profile["imminent_move_gate"] = await asyncio.to_thread(
                score_imminent_finding, f, settings.imminent_gate_model_path,
            )
        if f.stage not in {"CATALYST", "CATALYST_WATCH", "CATALYST_ACTIVE", "HALT", "HALT_WATCH", "HALT_PRESSURE", "RESUME"}:
            f.candidate_profile["edge_validation"] = await asyncio.to_thread(self.store.paper_edge_validation, f)
        # Preview of Scout's real notification gate (excluding the human's own
        # preference toggles), computed after edge_validation so it reflects
        # the same inputs the live gate will see. Advisory only; the actual
        # gate below (notification_allowed*) is unaffected by this value.
        f.candidate_profile["would_notify_preview"] = preview_would_notify(f)
        finding_id = await asyncio.to_thread(self.store.save_finding, f)
        f.finding_id = finding_id
        if f.catalyst_headline:
            trace.setdefault("catalyst_associated", trace["candidate_created"])
        if f.actionable_rank == "A" and not f.shadow_mode:
            trace.setdefault("actionable_promoted", trace["candidate_created"])
        f.trace_timestamps = trace
        await asyncio.to_thread(
            self.store.record_pipeline_traces,
            finding_id,
            [(stage, event_at, None, None) for stage, event_at in sorted(trace.items(), key=lambda item: item[1])],
        )
        if self.finding_listener:
            try:
                self.finding_listener(finding_id, f)
            except Exception:
                log.exception("finding listener failed for %s %s", f.ticker, f.stage)
        if self.trade_listener:
            asyncio.create_task(self.trade_listener(finding_id, f), name=f"scout-paper-trade-{f.ticker}")

        self._ensure_workers()
        prefs = await self._notification_preferences()
        stale_reason = self._stale_reason(f)
        # Silent/off/watch findings are persisted and streamed to the UI but
        # never occupy delivery queues.
        # Web Push can reach subscribers on any platform (it filters per-subscriber
        # internally in send_web_push_all), so its entry gate must not use the
        # android-specific check -- only ntfy is intentionally the mobile-only
        # fallback channel ("Mobile / ntfy" in Settings) and is correctly gated
        # by the android platform toggle specifically.
        if not stale_reason and notification_allowed_any_platform(f, prefs) and self._claim_episode_phase("webpush", f, prefs) and settings.vapid_public_key and settings.vapid_private_key and await self._webpush_subscription_count() > 0:
            await self._queue("webpush", finding_id, f, prefs)
        elif not stale_reason and notification_allowed(f, prefs, "android") and settings.ntfy_topic and self._claim_episode_phase("ntfy", f, prefs):
            await self._queue_consolidated_ntfy(finding_id, f, prefs)
        else:
            eligible_reason = notification_ineligibility_reason(f, prefs, "android")
            reason = stale_reason or eligible_reason or ("ntfy_not_configured" if not settings.ntfy_topic else "episode_already_notified")
            status = "not_configured" if reason == "ntfy_not_configured" else "not_eligible"
            await asyncio.to_thread(self.store.record_delivery, finding_id, "ntfy", status, reason)

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
            stale_reason = self._stale_reason(f)
            email_configured = bool(settings.resend_api_key and settings.resend_from and settings.resend_to)
            if not stale_reason and email_configured and notification_allowed(f, prefs, "email") and self._claim_episode_phase("email", f, prefs):
                await self._queue_consolidated_email(finding_id, f, prefs)
            else:
                eligible_reason = notification_ineligibility_reason(f, prefs, "email")
                reason = stale_reason or eligible_reason or ("email_not_configured" if not email_configured else "episode_already_notified")
                status = "not_configured" if reason == "email_not_configured" else "not_eligible"
                await asyncio.to_thread(self.store.record_delivery, finding_id, "email", status, reason)
        except Exception:
            log.exception("finding enrichment/email failed for %s %s", f.ticker, f.stage)

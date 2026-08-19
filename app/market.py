from __future__ import annotations

import asyncio
import json
import logging
import math
import statistics
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import orjson
import requests
import websockets

from .config import settings
from .db import Store
from .dispatch import Dispatcher
from .indicators import ema, pct_change, median_positive
from .models import Bucket, Finding, SymbolState
from .events import EventHub
from .hybrid import HybridMemory, RustPerceptionBridge

log = logging.getLogger("scout.market")
ET = ZoneInfo(settings.timezone)
ALLOWED_EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}


def trading_session_key(ts: float) -> str:
    """Return the U.S. equity trade date for a timestamp.

    Alpaca's overnight session starts at 8 PM ET and belongs to the next
    trading day, so 9 PM Monday and 10 AM Tuesday share Tuesday's session key.
    """
    local = datetime.fromtimestamp(ts, ET)
    trade_date = local.date() + timedelta(days=1) if local.hour >= 20 else local.date()
    return trade_date.isoformat()


def _headers() -> dict[str, str]:
    return {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}


def _chunks(seq: list[str], size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def is_late_promotion_risk(m: dict) -> bool:
    """Return True when a candidate is already too extended for a fresh alert.

    This deliberately shares the same boundary used by promotion tracing so
    diagnostics and promotion policy cannot drift apart.
    """
    base_extension = float(m.get("base_extension_pct") or 0.0)
    extension = float(m.get("extension") or 0.0)
    return bool(base_extension > 0.75 or extension > 2.0)


def should_suppress_late_fresh_promotion(stage: str, m: dict) -> bool:
    """Block chase-prone fresh expansion alerts while preserving re-entry paths."""
    return stage in {"SURGE", "BREAKOUT", "IGNITION", "HALT_PRESSURE"} and is_late_promotion_risk(m)



def evaluate_breakout_continuation_quality(m: dict) -> dict:
    """Require a BREAKOUT to still be fresh on the immediate 5-second tape.

    v6.6.8 production outcome data showed BREAKOUT dominated the actionable
    cohort but had negative 5m/15m expectancy. The strongest offline separator
    available in the captured cohort was immediate persistence: the 5s move
    should not be weaker than the older 15s/30s move.
    """
    enabled = bool(settings.breakout_continuation_gate_enabled)
    change5 = float(m.get("change5") or 0.0)
    change15 = float(m.get("change15") or 0.0)
    change30 = float(m.get("change30") or 0.0)
    older = max(0.0, change15, change30)
    persistence_ratio = (change5 / older) if older > 0 else (999.0 if change5 > 0 else 0.0)
    blockers = []
    if enabled:
        if change5 < settings.breakout_min_change_5s_pct:
            blockers.append("fresh_5s_continuation")
        if older > 0 and persistence_ratio < settings.breakout_min_persistence_ratio:
            blockers.append("breakout_deceleration")
    return {
        "ready": not blockers,
        "enabled": enabled,
        "change5_pct": change5,
        "change15_pct": change15,
        "change30_pct": change30,
        "persistence_ratio": persistence_ratio,
        "blockers": blockers,
    }


def evaluate_reentry_safety(stage: str, m: dict) -> dict:
    """Damage-control gate for REARM/RECLAIM alerts.

    Re-entry stages remain structurally distinct from fresh breakouts, but the
    production audit exposed severe adverse excursions in REARM/VWAP/EMA reclaim.
    Reject re-entry alerts that are already late-risk or fading immediately.
    """
    stage = str(stage or "").upper()
    enabled = bool(settings.reentry_safety_gate_enabled)
    change5 = float(m.get("change5") or 0.0)
    late_risk = is_late_promotion_risk(m)
    blockers = []
    if enabled and stage in {"REARM", "VWAP_RECLAIM", "EMA_RECLAIM"}:
        if late_risk:
            blockers.append("late_risk")
        if change5 < settings.reentry_min_change_5s_pct:
            blockers.append("fresh_5s_continuation")
    return {
        "ready": not blockers,
        "enabled": enabled,
        "stage": stage,
        "change5_pct": change5,
        "late_risk": late_risk,
        "blockers": blockers,
    }


def evaluate_late_stage_continuation_quality(stage: str, m: dict) -> dict:
    """Validate that a late-stage fresh alert still has immediate continuation.

    v6.6.7 production audits showed the EARLY/BREAKOUT cohorts were relatively
    stable while the tiny IGNITION/HALT_PRESSURE cohorts had materially worse
    adverse excursion. This guard does not change EARLY/BREAKOUT qualification.
    It only prevents a fresh later-stage promotion from relying on stale 15s/30s
    momentum after the last 5s impulse has already faded.
    """
    enabled = bool(settings.late_stage_continuation_gate_enabled)
    stage = str(stage or "").upper()
    change5 = float(m.get("change5") or 0.0)
    change15 = float(m.get("change15") or 0.0)
    late_risk = is_late_promotion_risk(m)

    if not enabled or stage not in {"IGNITION", "HALT_PRESSURE"}:
        return {
            "ready": True, "enabled": enabled, "stage": stage,
            "change5_pct": change5, "change15_pct": change15,
            "late_risk": late_risk, "blockers": [],
        }

    blockers: list[str] = []
    if late_risk:
        blockers.append("late_risk")
    if stage == "IGNITION":
        if change5 < settings.ignition_min_change_5s_pct:
            blockers.append("fresh_5s_continuation")
    else:
        if change5 < settings.halt_pressure_min_change_5s_pct:
            blockers.append("fresh_5s_continuation")
        if change15 < settings.halt_pressure_min_change_15s_pct:
            blockers.append("fresh_15s_continuation")

    return {
        "ready": not blockers,
        "enabled": enabled,
        "stage": stage,
        "change5_pct": change5,
        "change15_pct": change15,
        "late_risk": late_risk,
        "blockers": blockers,
    }


def build_promotion_trace(
    m: dict,
    *,
    relative_activity: bool,
    fast_single_bucket: bool,
    regular_participation: bool,
    sudden_impulse: bool,
    bearish_short: bool,
    structural_failure: bool,
    structure_ok: bool,
    quality_actionable: bool,
    first_leg_candidate: bool,
    candidate_age_seconds: float | None = None,
) -> dict:
    """Return an auditable snapshot of the gates between awareness and promotion.

    This function is deliberately observational. It mirrors the existing gates
    without changing thresholds or promotion decisions.
    """
    participation_ok = bool(regular_participation or fast_single_bucket or m.get("staircase"))
    activity_ok = bool(relative_activity or fast_single_bucket or m.get("staircase"))
    impulse_ok = bool(sudden_impulse or m.get("staircase"))
    gates = {
        "full_warmup": bool(m.get("full_warmup")),
        "relative_activity": activity_ok,
        "participation": participation_ok,
        "fresh_impulse": impulse_ok,
        "not_bearish_short": not bearish_short,
        "no_structural_failure": not structural_failure,
        "structure_ok": bool(structure_ok),
        "quality_clean": str(m.get("quality_label") or "") == "CLEAN",
        "bullish_confirmed": bool(m.get("bullish_confirmed")),
        "quality_actionable": bool(quality_actionable),
        "first_leg_candidate": bool(first_leg_candidate),
        "first_leg_release": bool(m.get("first_leg_release")),
    }
    blockers = [name for name, passed in gates.items() if not passed]
    base_extension = float(m.get("base_extension_pct") or 0.0)
    extension = float(m.get("extension") or 0.0)
    trace = {
        "gates": gates,
        "blockers": blockers,
        "next_blocker": blockers[0] if blockers else None,
        "candidate_age_seconds": round(float(candidate_age_seconds or 0.0), 3),
        "quality_label": m.get("quality_label"),
        "quality_score": int(m.get("quality_score") or 0),
        "rejection_reasons": list(m.get("rejection_reasons") or []),
        "score": int(m.get("score") or 0),
        "base_extension_pct": base_extension,
        "extension_pct": extension,
        "trigger_distance_pct": None,
        "late_risk": is_late_promotion_risk(m),
        "fresh_velocity_pct": max(
            float(m.get("change3") or 0.0), float(m.get("change5") or 0.0),
            float(m.get("change15") or 0.0), float(m.get("change30") or 0.0),
        ),
    }
    trigger = m.get("micro_resistance")
    price = float(m.get("price") or 0.0)
    if trigger is not None and price > 0:
        trace["trigger_distance_pct"] = round((float(trigger) - price) / price * 100.0, 4)
    return trace



def evaluate_early_continuation_quality(
    *,
    first_leg_candidate: bool,
    relative_activity: bool,
    quality_score: int,
    velocity_pct: float,
    acceleration_pct: float,
) -> dict:
    """Conservative continuation-quality filter for the EARLY_SIGNAL fast path.

    Production forward-outcome audits showed that many weak fast-path releases had
    neither relative activity nor first-leg context and were already decelerating.
    We preserve those context-backed signals, plus genuinely reaccelerating impulse
    cases, without changing confirmed BREAKOUT/IGNITION or re-entry paths.
    """
    contextual = bool(first_leg_candidate or relative_activity)
    impulse_reacceleration = bool(
        velocity_pct >= settings.early_signal_continuation_min_velocity_pct
        and acceleration_pct >= settings.early_signal_continuation_min_accel_pct
    )
    pristine_reacceleration = bool(
        quality_score >= 100
        and velocity_pct >= settings.early_signal_pristine_min_velocity_pct
        and acceleration_pct > 0.0
    )
    enabled = bool(settings.early_signal_continuation_gate_enabled)
    ready = (not enabled) or contextual or impulse_reacceleration or pristine_reacceleration
    return {
        "ready": ready,
        "enabled": enabled,
        "contextual": contextual,
        "first_leg_candidate": bool(first_leg_candidate),
        "relative_activity": bool(relative_activity),
        "impulse_reacceleration": impulse_reacceleration,
        "pristine_reacceleration": pristine_reacceleration,
        "quality_score": int(quality_score),
        "velocity_pct": float(velocity_pct),
        "acceleration_pct": float(acceleration_pct),
        "blockers": [] if ready else ["continuation_quality"],
    }



def should_allow_fresh_early_actionable(actionable_rank: str | None) -> bool:
    """Return whether a fresh EARLY event may become actionable in v6.6.7."""
    if not settings.early_actionable_require_rank_a:
        return True
    return str(actionable_rank or "").upper() == "A"


def evaluate_early_signal(
    m: dict,
    *,
    first_leg_candidate: bool,
    quality_actionable: bool,
    participation_ok: bool,
    structure_ok: bool,
    bullish_confirmed: bool,
    bearish_short: bool,
    structural_failure: bool,
    relative_activity: bool,
    trigger_distance_pct: float | None,
    candidate_age_seconds: float,
) -> dict:
    """Score an early heads-up without bypassing hard safety gates."""
    velocity = max(
        float(m.get("change3") or 0.0),
        float(m.get("change5") or 0.0),
        float(m.get("change15") or 0.0),
    )
    accel = float(m.get("change5") or 0.0) - float(m.get("change15") or 0.0)
    quality_score = int(m.get("quality_score") or 0)
    extension = float(m.get("base_extension_pct") or 0.0)
    continuation_quality = evaluate_early_continuation_quality(
        first_leg_candidate=first_leg_candidate,
        relative_activity=relative_activity,
        quality_score=quality_score,
        velocity_pct=velocity,
        acceleration_pct=accel,
    )
    evidence = {
        "first_leg_candidate": bool(first_leg_candidate),
        "full_warmup": bool(m.get("full_warmup")),
        "relative_activity": bool(relative_activity),
        "velocity": velocity >= settings.early_signal_min_velocity_pct,
        "acceleration": accel >= settings.early_signal_min_accel_pct,
        "quality_score": quality_score >= settings.early_signal_min_quality_score,
        "quality_actionable": bool(quality_actionable),
        "participation": bool(participation_ok),
        "structure_ok": bool(structure_ok),
        "bullish_confirmed": bool(bullish_confirmed),
    }
    score = sum(1 for passed in evidence.values() if passed)
    hard = {
        "enabled": bool(settings.early_signal_enabled),
        "quality_actionable": bool(quality_actionable),
        "participation": bool(participation_ok),
        "structure_ok": bool(structure_ok),
        "bullish_confirmed": bool(bullish_confirmed),
        "not_bearish_short": not bool(bearish_short),
        "no_structural_failure": not bool(structural_failure),
        # v6.6.6: fast-path release must have continuation evidence beyond the
        # baseline CLEAN/bullish checks. This is intentionally EARLY_SIGNAL-only.
        "continuation_quality": bool(continuation_quality["ready"]),
        # v6.6.4: EARLY_SIGNAL cannot override Scout's canonical late-risk rule.
        "not_late_risk": not is_late_promotion_risk(m),
        "not_extended": extension <= settings.early_signal_max_extension_pct,
        "near_trigger": bool(
            trigger_distance_pct is not None
            and settings.early_signal_min_trigger_distance_pct <= float(trigger_distance_pct) <= settings.early_signal_max_trigger_distance_pct
        ),
        "fresh_candidate": candidate_age_seconds <= settings.early_signal_max_candidate_age_seconds,
    }
    hard_blockers = [name for name, passed in hard.items() if not passed]
    return {
        "ready": not hard_blockers and score >= settings.early_signal_min_evidence_score,
        "score": score,
        "min_score": settings.early_signal_min_evidence_score,
        "evidence": evidence,
        "hard_blockers": hard_blockers,
        "continuation_quality": continuation_quality,
        "velocity_pct": velocity,
        "acceleration_pct": accel,
        "extension_pct": extension,
        "trigger_distance_pct": trigger_distance_pct,
        "candidate_age_seconds": round(float(candidate_age_seconds), 3),
    }


def evaluate_early_release(
    m: dict,
    *,
    first_leg_candidate: bool,
    quality_actionable: bool,
    participation_ok: bool,
    trigger_distance_pct: float | None,
    candidate_age_seconds: float,
) -> dict:
    """Evaluate the v6.6.1 early-notification release without weakening quality.

    The legacy FIRST_LEG path remains intact. This path only advances the first
    actionable notification for candidates that are already CLEAN + bullish,
    structurally valid first-leg candidates with real participation and a
    positive fresh velocity pulse, while they are still close to the base/trigger.
    """
    fresh_velocity = max(
        float(m.get("change3") or 0.0),
        float(m.get("change5") or 0.0),
        float(m.get("change15") or 0.0),
        float(m.get("change30") or 0.0),
    )
    base_extension = float(m.get("base_extension_pct") or 0.0)
    quality_score = int(m.get("quality_score") or 0)

    checks = {
        "enabled": bool(settings.early_release_enabled),
        "first_leg_candidate": bool(first_leg_candidate),
        "quality_actionable": bool(quality_actionable),
        "participation": bool(participation_ok),
        "full_warmup": bool(m.get("full_warmup")),
        "quality_score": quality_score >= settings.early_release_min_quality_score,
        "fresh_velocity": fresh_velocity >= settings.early_release_min_fresh_velocity_pct,
        "base_not_extended": base_extension <= settings.early_release_max_base_extension_pct,
        "near_trigger": bool(
            trigger_distance_pct is not None
            and settings.early_release_min_trigger_distance_pct
            <= float(trigger_distance_pct)
            <= settings.early_release_max_trigger_distance_pct
        ),
        "fresh_candidate": candidate_age_seconds <= settings.early_release_max_candidate_age_seconds,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    return {
        "ready": not blockers,
        "checks": checks,
        "blockers": blockers,
        "fresh_velocity_pct": fresh_velocity,
        "base_extension_pct": base_extension,
        "trigger_distance_pct": trigger_distance_pct,
        "candidate_age_seconds": round(float(candidate_age_seconds), 3),
    }


class Universe:
    def __init__(self):
        self.symbols: set[str] = set()
        self.min_price = settings.universe_min_price
        self.max_price = settings.universe_max_price
        self.metadata: dict[str, dict] = {}

    def refresh_sync(self) -> set[str]:
        if settings.wildcard_market_stream:
            self.symbols = {"*"}
            return self.symbols
        r = requests.get(
            f"{settings.alpaca_trading_base}/v2/assets",
            params={"status": "active", "asset_class": "us_equity"},
            headers=_headers(), timeout=20,
        )
        r.raise_for_status()
        assets = r.json()
        symbols = [
            str(a.get("symbol", "")).upper()
            for a in assets
            if a.get("tradable") and str(a.get("exchange", "")).upper() in ALLOWED_EXCHANGES and a.get("symbol")
        ]
        symbols = symbols[:settings.universe_max_symbols]

        selected: set[str] = set()
        self.metadata = {}
        for chunk in _chunks(symbols, settings.universe_batch_size):
            rr = requests.get(
                f"{settings.alpaca_data_base}/v2/stocks/snapshots",
                params={"symbols": ",".join(chunk), "feed": settings.alpaca_feed},
                headers=_headers(), timeout=20,
            )
            rr.raise_for_status()
            payload = rr.json()
            # Alpaca's multi-symbol snapshots response is normally keyed
            # directly by symbol. Accept a wrapped shape as well so Scout is
            # resilient to gateways/SDK adapters that add a `snapshots` key.
            snapshots = payload.get("snapshots", payload) if isinstance(payload, dict) else {}
            for symbol, snapshot in snapshots.items():
                try:
                    latest = snapshot.get("latestTrade") or {}
                    daily = snapshot.get("dailyBar") or {}
                    previous = snapshot.get("prevDailyBar") or {}
                    px = float(latest.get("p") or daily.get("c") or 0)
                except Exception:
                    continue
                if self.min_price <= px <= self.max_price:
                    ticker = symbol.upper()
                    selected.add(ticker)
                    previous_close = float(previous.get("c") or 0) or None
                    self.metadata[ticker] = {
                        "previous_close": previous_close,
                        "day_volume": float(daily.get("v") or 0),
                        "day_high": float(daily.get("h") or 0) or None,
                        "day_low": float(daily.get("l") or 0) or None,
                    }
        self.symbols = selected
        return set(selected)


class MarketWatcher:
    def __init__(self, store: Store, dispatcher: Dispatcher, events: EventHub | None = None):
        self.store = store
        self.dispatcher = dispatcher
        self.events = events
        self.universe = Universe()
        self.states: dict[str, SymbolState] = {}
        self.subscribed: set[str] = set()
        self.overnight_subscribed: set[str] = set()
        self.ws = None
        self.overnight_ws = None
        self._desired: set[str] = set()
        self._universe_ready = asyncio.Event()
        self.halts: dict[str, dict] = {}
        self.outcome_trackers: dict[str, dict[int, dict]] = {}
        scanner = self.store.get_scanner_settings()
        self.min_price = float(scanner["min_price"])
        self.max_price = float(scanner["max_price"])
        self.universe.min_price = self.min_price
        self.universe.max_price = self.max_price
        stored_profile = self.store.get_notification_preferences().get("market_quality_profile", settings.quality_profile)
        self.quality_profile = stored_profile if stored_profile in {"strict", "balanced", "permissive"} else "balanced"
        self.rust_bridge: RustPerceptionBridge | None = None
        self.hybrid_memory = HybridMemory(
            settings.hybrid_merge_window_seconds, settings.hybrid_dedupe_seconds,
            episode_gap_seconds=settings.hybrid_episode_gap_seconds,
        )
        self._restored_symbols: set[str] = set()
        self.feed_health: dict[str, dict[str, object]] = {
            "sip": {"connected": False, "connections": 0, "disconnects": 0, "last_connected_at": None, "last_disconnected_at": None, "last_error": None},
            "boats": {"connected": False, "connections": 0, "disconnects": 0, "last_connected_at": None, "last_disconnected_at": None, "last_error": None},
        }
        self._reconcile_locks = {"sip": asyncio.Lock(), "boats": asyncio.Lock()}
        self.reconcile_status: dict[str, dict[str, object]] = {
            "sip": {"in_progress": False, "last_started_at": None, "last_completed_at": None, "last_error": None},
            "boats": {"in_progress": False, "last_started_at": None, "last_completed_at": None, "last_error": None},
        }
        self.last_market_event_at: float | None = None
        self.last_market_event_by_feed: dict[str, float | None] = {"sip": None, "boats": None}
        self.runtime_watchdog = None

    def set_quality_profile(self, profile: str) -> None:
        normalized = str(profile).lower()
        if normalized in {"strict", "balanced", "permissive"}:
            self.quality_profile = normalized

    def set_rust_bridge(self, bridge: RustPerceptionBridge | None) -> None:
        self.rust_bridge = bridge

    @staticmethod
    def _stage_rank(stage: str) -> int:
        return {
            "PRE_IGNITION": 0, "AWAKENING": 1, "FIRST_LEG": 1, "EARLY": 2, "STAIRCASE": 2,
            "SURGE": 3, "EMA_RECLAIM": 3, "VWAP_RECLAIM": 3, "BREAKOUT": 4,
            "IGNITION": 5, "REARM": 5, "HALT_PRESSURE": 7,
        }.get(str(stage).upper(), 0)

    async def _restore_state_from_store(self, state: SymbolState, ts: float) -> None:
        symbol = state.symbol.upper()
        if symbol in self._restored_symbols:
            return
        self._restored_symbols.add(symbol)
        rows = await asyncio.to_thread(self.store.latest_findings_by_ticker, [symbol])
        latest = rows.get(symbol)
        if not latest or trading_session_key(float(latest.get("detected_at") or 0)) != trading_session_key(ts):
            return
        stage = str(latest.get("stage") or "")
        detected_at = float(latest.get("detected_at") or 0)
        state.episode_id = int(latest.get("episode_id") or 0)
        state.last_alert_at = detected_at
        state.last_stage_rank = self._stage_rank(stage)
        if stage:
            state.last_stage_alert_at[stage] = detected_at
        hybrid_key = str(latest.get("hybrid_key") or "")
        if hybrid_key:
            try:
                sequence = int(hybrid_key.rsplit(":", 1)[1])
                self.hybrid_memory.restore_episode(symbol, trading_session_key(detected_at), sequence, detected_at)
            except (TypeError, ValueError):
                pass
        if stage in {"PRE_IGNITION", "AWAKENING"}:
            state.pre_ignition_finding_id = int(latest.get("id") or 0) or None
        if stage in {"BREAKOUT", "IGNITION", "REARM"}:
            state.continuation_peak = float(latest.get("price") or 0) or None
            state.continuation_started_at = detected_at
        if stage in {"EMA_RECLAIM", "VWAP_RECLAIM", "REARM"}:
            state.reversal_phase = "REARM" if stage == "REARM" else "RECLAIM"
            state.reversal_low = latest.get("reversal_low")
            state.reversal_started_at = detected_at
        log.info("restored %s lifecycle stage=%s episode=%s from persisted state", symbol, stage, state.episode_id)

    def _hybrid_key(self, state: SymbolState, ts: float) -> str:
        return self.hybrid_memory.episode_key(state.symbol, trading_session_key(ts), ts)

    def _decorate_hybrid(self, finding: Finding, source: str) -> None:
        sources = self.hybrid_memory.observe(finding.ticker, source, finding.detected_at, finding.stage)
        finding.engine_source = source
        finding.hybrid_sources = sources
        finding.hybrid_score = min(100, int(finding.quality_score) + (15 if len(sources) > 1 else 0))
        if not finding.hybrid_key:
            state = self.states.get(finding.ticker.upper())
            finding.hybrid_key = self._hybrid_key(state, finding.detected_at) if state else f"{finding.ticker}:{trading_session_key(finding.detected_at)}:0"
        if len(sources) > 1:
            finding.evidence.append("Rust + Python candidate agreement")
            finding.notification_reason = "dual-engine confirmation"
        elif source == "rust":
            finding.notification_reason = "Rust primary perception detected dormant-to-active transition"
        else:
            if finding.stage == "REARM" or "FIRST_PULLBACK" in (finding.signals or []):
                finding.notification_reason = "controlled pullback held and bullish momentum reaccelerated"
            elif finding.stage in {"FIRST_LEG", "EARLY"}:
                finding.notification_reason = "Python specialist confirmed an early bullish release"
            elif finding.stage in {"BREAKOUT", "SURGE", "IGNITION"}:
                finding.notification_reason = "bullish lifecycle escalated into confirmed expansion"
            else:
                finding.notification_reason = finding.notification_reason or "Python specialist intelligence"

    async def handle_rust_candidate(self, candidate: dict) -> None:
        symbol = str(candidate.get("ticker") or "").upper()
        if not symbol:
            return
        state = self.states.get(symbol)
        if not state:
            return
        detected_at = float(candidate.get("detected_at") or time.time())
        metrics = self._metrics(state, detected_at)
        if not metrics or not (self.min_price <= float(metrics["price"]) <= self.max_price):
            return
        recipe_score = int(candidate.get("recipe_score") or 0)
        actionable = bool(
            metrics.get("full_warmup")
            and metrics.get("quality_label") == "CLEAN"
            and recipe_score >= 7
            and float(metrics.get("vol15") or 0) >= settings.hybrid_awakening_min_vol_ratio
            and (float(metrics.get("change15") or 0) >= settings.hybrid_awakening_min_change_15s_pct or float(metrics.get("change5") or 0) > 0)
        )
        # 2026-08-19 experiment #4 (default off): a genuinely new tier, not a loosened
        # threshold. Trust Rust's own high-confidence recipe score (it fires far earlier
        # than Python's quality gate can confirm -- Milestone 009/010) even when the full
        # "CLEAN" quality bar isn't cleared yet, as long as quality isn't actively bad
        # (not ILLIQUID/CHOPPY/STALE) and there's real, fresh price movement backing it.
        # See MILESTONES/2026-08-19-* for the backtest result before this is recommended.
        if not actionable and settings.experiment_rust_fast_confirm:
            actionable = bool(
                metrics.get("full_warmup")
                and str(metrics.get("quality_label") or "") not in {"ILLIQUID", "CHOPPY"}
                and recipe_score >= 8
                and float(metrics.get("vol15") or 0) >= settings.hybrid_awakening_min_vol_ratio
                and float(metrics.get("change15") or 0) >= settings.hybrid_awakening_min_change_15s_pct
            )
        duplicate = self.hybrid_memory.rust_notification_is_duplicate(symbol, detected_at)
        stage = "AWAKENING" if actionable and not duplicate else "PRE_IGNITION"
        catalyst = self.store.recent_catalyst(symbol)
        evidence = [
            "Rust primary perception qualified an early structural transition",
            *[str(x) for x in candidate.get("recipe_present") or []][:6],
        ]
        signals = [stage]
        if catalyst:
            signals.append("CATALYST")
        finding = Finding(
            ticker=symbol, stage=stage, detected_at=detected_at, price=float(metrics["price"]),
            score=min(10, max(recipe_score, int(metrics.get("score") or 0))),
            vol_ratio_15s=float(metrics.get("vol15") or 0), vol_ratio_30s=float(metrics.get("vol30") or 0),
            change_60s_pct=float(metrics.get("change60") or 0), extension_pct=float(metrics.get("extension") or 0),
            ema9=metrics.get("ema9"), ema21=metrics.get("ema21"), ema9_slope=metrics.get("ema9_slope"),
            vwap=metrics.get("vwap"), above_vwap=bool(metrics.get("above_vwap")), quiet_break=bool(metrics.get("quiet_break")),
            evidence=evidence, change_3s_pct=metrics.get("change3"), change_5s_pct=metrics.get("change5"),
            change_10s_pct=metrics.get("change10"), change_15s_pct=metrics.get("change15"), change_30s_pct=metrics.get("change30"),
            accel_15s_pp=metrics.get("accel15_pp"), dollar_volume_15s=metrics.get("dollar15"), dollar_volume_30s=metrics.get("dollar30"),
            trades_15s=metrics.get("trades15"), trades_30s=metrics.get("trades30"), breakout_level=metrics.get("breakout_level"),
            breakout_window=metrics.get("breakout_window"), signals=signals, quality_label=str(metrics.get("quality_label") or "DEVELOPING"),
            quality_score=int(metrics.get("quality_score") or 0), actionable_rank=("A" if actionable and catalyst else "B" if actionable else "C"),
            rejection_reasons=list(metrics.get("rejection_reasons") or []), directional_efficiency=metrics.get("directional_efficiency"),
            active_bucket_ratio=metrics.get("active_bucket_ratio"), direction_reversals=metrics.get("direction_reversals"), previous_close=metrics.get("previous_close"),
            gap_pct=metrics.get("gap_pct"), day_volume=metrics.get("day_volume"), projected_session_volume=metrics.get("projected_session_volume"),
            volume_rate_per_minute=metrics.get("volume_rate_per_minute"), candidate_profile=dict(metrics.get("candidate_profile") or {}),
            episode_id=state.episode_id, detection_timeframe_seconds=settings.bucket_seconds, formation_start_at=detected_at - 300.0,
            formation_end_at=detected_at, formation_low=metrics.get("base_low"), formation_high=metrics.get("base_high"),
            trigger_level=float(metrics.get("micro_resistance") or 0) or None, invalidation_level=metrics.get("base_low"),
            urgency=("EARLY" if actionable else "WATCH"), engine_version=settings.app_version, lifecycle_phase=("AWAKENING" if actionable else "ARMED"),
            shadow_mode=not actionable or duplicate, recipe_score=recipe_score,
            recipe_present=[str(x) for x in candidate.get("recipe_present") or []], recipe_missing=[str(x) for x in candidate.get("recipe_missing") or []],
            trigger_distance_pct=float(candidate.get("trigger_distance_pct") or 0),
            base_extension_at_detection_pct=float(candidate.get("base_extension_pct") or 0),
            timeliness_label="EARLY", precursor_finding_id=state.pre_ignition_finding_id,
            hybrid_key=self._hybrid_key(state, detected_at),
        )
        if catalyst:
            finding.catalyst_headline, finding.catalyst_category, finding.catalyst_score, finding.catalyst_url, _ = catalyst
            finding.candidate_profile["catalyst"] = min(100, int((finding.catalyst_score or 0) * 20))
        self._decorate_hybrid(finding, "rust")
        snap = self.snapshot(symbol)
        buckets, current = snap if snap else ([], None)
        finding_id = await self.dispatcher.emit(finding, buckets, current)
        if state.pre_ignition_finding_id is None:
            state.pre_ignition_finding_id = finding_id
        log.info("%s %s $%.4f rust_recipe=%d actionable=%s duplicate=%s", stage, symbol, finding.price, recipe_score, actionable, duplicate)

    async def apply_scanner_range(self, minimum: float, maximum: float) -> dict[str, float]:
        value = await asyncio.to_thread(self.store.set_scanner_settings, minimum, maximum)
        self.min_price = float(value["min_price"])
        self.max_price = float(value["max_price"])
        self.universe.min_price = self.min_price
        self.universe.max_price = self.max_price
        selected = await asyncio.to_thread(self.universe.refresh_sync)
        self._desired = selected
        self._universe_ready.set()
        if self.ws:
            await self._reconcile(self.ws, self.subscribed, "SIP")
        if self.overnight_ws:
            await self._reconcile(self.overnight_ws, self.overnight_subscribed, "BOATS")
        return value

    def snapshot(self, ticker: str):
        s = self.states.get(ticker.upper())
        if not s:
            return None
        buckets = [Bucket(b.start_ts, b.open, b.high, b.low, b.close, b.volume, b.trades) for b in s.buckets]
        cur = None
        if s.current:
            b = s.current
            cur = Bucket(b.start_ts, b.open, b.high, b.low, b.close, b.volume, b.trades)
        return buckets, cur

    def register_finding(self, finding_id: int, finding: Finding) -> None:
        if finding.price <= 0:
            return
        session = trading_session_key(finding.detected_at)
        self.outcome_trackers.setdefault(finding.ticker.upper(), {})[int(finding_id)] = {
            "id": int(finding_id),
            "detected_at": float(finding.detected_at),
            "price": float(finding.price),
            "session": session,
            "max_1m_pct": 0.0,
            "max_5m_pct": 0.0,
            "max_15m_pct": 0.0,
            "max_session_pct": 0.0,
            "time_to_peak_seconds": None,
            "last_persist": 0.0,
        }

    def _update_outcomes(self, symbol: str, ts: float, price: float) -> None:
        trackers = self.outcome_trackers.get(symbol)
        if not trackers:
            return
        session = trading_session_key(ts)
        stale: list[int] = []
        for finding_id, tracker in list(trackers.items()):
            if tracker["session"] != session:
                stale.append(finding_id)
                continue
            elapsed = max(0.0, ts - tracker["detected_at"])
            move = pct_change(tracker["price"], price)
            if tracker["max_session_pct"] is None or move > tracker["max_session_pct"]:
                tracker["max_session_pct"] = move
                tracker["time_to_peak_seconds"] = elapsed
            if elapsed <= 60 and (tracker["max_1m_pct"] is None or move > tracker["max_1m_pct"]):
                tracker["max_1m_pct"] = move
            if elapsed <= 300 and (tracker["max_5m_pct"] is None or move > tracker["max_5m_pct"]):
                tracker["max_5m_pct"] = move
            if elapsed <= 900 and (tracker["max_15m_pct"] is None or move > tracker["max_15m_pct"]):
                tracker["max_15m_pct"] = move
            if ts - tracker["last_persist"] >= 15 or elapsed >= 900:
                tracker["last_persist"] = ts
                self.store.upsert_outcome(
                    finding_id, tracker["max_1m_pct"], tracker["max_5m_pct"], tracker["max_15m_pct"],
                    tracker["max_session_pct"], tracker["time_to_peak_seconds"],
                )
        for finding_id in stale:
            tracker = trackers.pop(finding_id, None)
            if tracker:
                self.store.upsert_outcome(
                    finding_id, tracker["max_1m_pct"], tracker["max_5m_pct"], tracker["max_15m_pct"],
                    tracker["max_session_pct"], tracker["time_to_peak_seconds"],
                )
        if not trackers:
            self.outcome_trackers.pop(symbol, None)

    def snapshot_payload(self, ticker: str) -> dict | None:
        ticker = ticker.upper()
        snap = self.snapshot(ticker)
        state = self.states.get(ticker)
        if not snap or not state:
            return None
        buckets, current = snap
        rows = buckets[-120:] + ([current] if current else [])
        metrics = self._metrics(state, time.time()) if state.current else None
        return {
            "ticker": ticker,
            "session_date": state.session_date,
            "session_first_price": state.session_first_price,
            "buckets": [
                {
                    "start_ts": b.start_ts, "open": b.open, "high": b.high, "low": b.low,
                    "close": b.close, "volume": b.volume, "trades": b.trades,
                } for b in rows if b is not None
            ],
            "metrics": metrics or {},
            "halt": self.halts.get(ticker),
            "source": "live",
            "as_of": time.time(),
        }

    def historical_snapshot_sync(self, ticker: str, center_ts: float, bucket_seconds: int = 15) -> dict:
        """Load detection-centered candles when live memory cannot cover an event.

        Alpaca historical trades preserve Scout's native 15-second candles. If a
        very active symbol exceeds the trade page, minute bars remain a reliable
        closed-session fallback instead of returning an empty chart.
        """
        ticker = ticker.upper()
        bucket_seconds = max(15, min(300, int(bucket_seconds)))
        start = datetime.fromtimestamp(center_ts - 20 * 60, timezone.utc)
        end = datetime.fromtimestamp(center_ts + 20 * 60, timezone.utc)
        iso = lambda value: value.isoformat().replace("+00:00", "Z")
        params = {
            "start": iso(start), "end": iso(end), "feed": settings.alpaca_feed,
            "limit": 10000, "sort": "asc",
        }
        trades: list[dict] = []
        next_page_token: str | None = None
        page_count = 0
        while True:
            request_params = dict(params)
            if next_page_token:
                request_params["page_token"] = next_page_token
            response = requests.get(
                f"{settings.alpaca_data_base}/v2/stocks/{ticker}/trades",
                params=request_params, headers=_headers(), timeout=25,
            )
            response.raise_for_status()
            page = response.json()
            trades.extend(page.get("trades", []))
            page_count += 1
            next_page_token = page.get("next_page_token")
            if not next_page_token:
                break
        grouped: dict[int, Bucket] = {}
        for trade in trades:
            try:
                ts = datetime.fromisoformat(str(trade["t"]).replace("Z", "+00:00")).timestamp()
                price, size = float(trade["p"]), float(trade.get("s") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            start_ts = int(ts // bucket_seconds) * bucket_seconds
            row = grouped.get(start_ts)
            if row is None:
                grouped[start_ts] = Bucket(start_ts, price, price, price, price, size, 1)
            else:
                row.high = max(row.high, price)
                row.low = min(row.low, price)
                row.close = price
                row.volume += max(0, size)
                row.trades += 1

        source = "historical-trades"
        rows = sorted(grouped.values(), key=lambda row: row.start_ts)
        if len(rows) < 2:
            bar_response = requests.get(
                f"{settings.alpaca_data_base}/v2/stocks/{ticker}/bars",
                params={**params, "timeframe": "1Min"}, headers=_headers(), timeout=25,
            )
            bar_response.raise_for_status()
            rows = []
            for bar in bar_response.json().get("bars", []):
                try:
                    ts = datetime.fromisoformat(str(bar["t"]).replace("Z", "+00:00")).timestamp()
                    rows.append(Bucket(ts, float(bar["o"]), float(bar["h"]), float(bar["l"]), float(bar["c"]), float(bar.get("v") or 0), int(bar.get("n") or 0)))
                except (KeyError, TypeError, ValueError):
                    continue
            source = "historical-bars"
        if not rows:
            raise LookupError(f"no historical market data around {ticker} detection")
        return {
            "ticker": ticker,
            "session_date": trading_session_key(center_ts),
            "session_first_price": rows[0].open,
            "buckets": [
                {"start_ts": row.start_ts, "open": row.open, "high": row.high, "low": row.low,
                 "close": row.close, "volume": row.volume, "trades": row.trades}
                for row in rows
            ],
            "metrics": {}, "halt": self.halts.get(ticker), "source": source, "as_of": time.time(),
            "historical_complete": not bool(next_page_token), "historical_pages": page_count,
            "historical_trade_count": len(trades),
        }

    def diagnostics(self, ticker: str) -> dict:
        ticker = ticker.upper()
        state = self.states.get(ticker)
        if not state or not state.current:
            return {"ticker": ticker, "available": False, "reasons": ["symbol is not warm in the live state cache"]}
        m = self._metrics(state, time.time())
        if not m:
            return {"ticker": ticker, "available": False, "reasons": ["insufficient warmup buckets"]}
        gates = {
            "price_range": self.min_price <= m["price"] <= self.max_price,
            "participation_30s": m["dollar30"] >= settings.min_30s_dollar_volume and m["trades30"] >= settings.min_30s_trades,
            "relative_activity": m["vol15"] >= settings.vol_ratio_trigger or m["vol30"] >= settings.vol_ratio_trigger,
            "early_velocity": m["change15"] >= settings.early_min_change_15s_pct or m["change30"] >= settings.early_min_change_30s_pct,
            "surge": bool(m.get("surge")),
            "breakout": bool(m.get("breakout")),
            "staircase": bool(m.get("staircase")),
            "ema_rising": bool(m.get("ema_up")),
            "above_vwap": bool(m.get("above_vwap")),
            "quiet_break": bool(m.get("quiet_break")),
            "market_quality_clean": m.get("quality_label") == "CLEAN",
            "bullish_confirmed": bool(m.get("bullish_confirmed")),
        }
        return {"ticker": ticker, "available": True, "metrics": m, "gates": gates, "rejection_reasons": m.get("rejection_reasons", [])}

    def make_catalyst_finding(self, ticker: str, headline: str, category: str, catalyst_score: int, url: str, detected_at: float) -> tuple[Finding, list[Bucket], Bucket | None]:
        ticker = ticker.upper()
        s = self.states.get(ticker)
        m = self._metrics(s, detected_at) if s else None
        price = (m or {}).get("price", s.current.close if s and s.current else 0.0)
        reaction_active = bool(
            m and m.get("quality_label") == "CLEAN" and m.get("above_vwap")
            and (float(m.get("change15") or 0) >= .7 or float(m.get("change60") or 0) >= 2.0)
            and (int(m.get("trades30") or 0) >= 12 or float(m.get("dollar30") or 0) >= 5000)
        )
        catalyst_stage = "CATALYST_ACTIVE" if reaction_active else "CATALYST_WATCH"
        f = Finding(
            ticker=ticker, stage=catalyst_stage, detected_at=detected_at, price=float(price or 0.0),
            score=max(1, min(10, catalyst_score * 2)),
            vol_ratio_15s=float((m or {}).get("vol15", 0.0)), vol_ratio_30s=float((m or {}).get("vol30", 0.0)),
            change_60s_pct=float((m or {}).get("change60", 0.0)), extension_pct=float((m or {}).get("extension", 0.0)),
            ema9=(m or {}).get("ema9"), ema21=(m or {}).get("ema21"), ema9_slope=(m or {}).get("ema9_slope"),
            vwap=(m or {}).get("vwap"), above_vwap=bool((m or {}).get("above_vwap", False)),
            quiet_break=bool((m or {}).get("quiet_break", False)),
            evidence=list((m or {}).get("evidence", [])) + [f"bullish catalyst: {category}"],
            catalyst_headline=headline, catalyst_category=category, catalyst_score=catalyst_score, catalyst_url=url,
            change_3s_pct=(m or {}).get("change3"), change_5s_pct=(m or {}).get("change5"),
            change_10s_pct=(m or {}).get("change10"), change_15s_pct=(m or {}).get("change15"),
            change_30s_pct=(m or {}).get("change30"), accel_15s_pp=(m or {}).get("accel15_pp"),
            dollar_volume_15s=(m or {}).get("dollar15"), dollar_volume_30s=(m or {}).get("dollar30"),
            trades_15s=(m or {}).get("trades15"), trades_30s=(m or {}).get("trades30"),
            breakout_level=(m or {}).get("breakout_level"), breakout_window=(m or {}).get("breakout_window"),
            signals=[catalyst_stage, "CATALYST"], urgency="NOW" if reaction_active else "WATCH",
            quality_label=(m or {}).get("quality_label", "DEVELOPING"), quality_score=int((m or {}).get("quality_score", 0)),
            actionable_rank=(m or {}).get("actionable_rank", "C"), rejection_reasons=list((m or {}).get("rejection_reasons", [])),
            directional_efficiency=(m or {}).get("directional_efficiency"), active_bucket_ratio=(m or {}).get("active_bucket_ratio"),
            direction_reversals=(m or {}).get("direction_reversals"), previous_close=(m or {}).get("previous_close"),
            gap_pct=(m or {}).get("gap_pct"), day_volume=(m or {}).get("day_volume"), projected_session_volume=(m or {}).get("projected_session_volume"),
            volume_rate_per_minute=(m or {}).get("volume_rate_per_minute"), candidate_profile={**dict((m or {}).get("candidate_profile", {})), "catalyst": min(100, catalyst_score * 20)},
        )
        snap = self.snapshot(ticker)
        buckets, current = snap if snap else ([], None)
        return f, buckets, current

    def twenty_four_hour_rows(self, limit: int = 200) -> list[dict]:
        """Return BOATS-verified stocks through the same Scout opportunity pipeline.

        This is an observability/category surface, not a second detector. BOATS
        trades already enter _handle_trade -> _metrics -> _maybe_emit and the
        Rust primary bridge exactly like SIP trades. The panel therefore reports
        symbols whose 24H eligibility has been empirically verified by a BOATS
        print during the current trading session, with their current Scout
        quality/actionability metrics and latest finding.
        """
        if not settings.twenty_four_hour_panel_enabled:
            return []
        now = time.time()
        current_session = trading_session_key(now)
        recent_cutoff = now - float(settings.twenty_four_hour_recent_seconds)
        candidates: list[tuple[SymbolState, dict]] = []
        for state in self.states.values():
            if state.boats_session_date != current_session or state.last_boats_trade_at <= 0:
                continue
            # Preserve the verified 24H identity through the regular session,
            # but discard stale symbols after a configurable maximum age.
            if state.last_boats_trade_at < recent_cutoff:
                continue
            metrics = self._metrics(state, now) if state.current else None
            if not metrics:
                continue
            candidates.append((state, metrics))

        tickers = [state.symbol for state, _ in candidates]
        latest = self.store.latest_findings_by_ticker(tickers)
        rank_weight = {"A": 3, "B": 2, "C": 1}
        stage_weight = {
            "HALT_PRESSURE": 12, "EARLY": 11, "FIRST_LEG": 10, "BREAKOUT": 9,
            "SURGE": 8, "IGNITION": 7, "REARM": 6, "VWAP_RECLAIM": 5,
            "EMA_RECLAIM": 5, "AWAKENING": 4, "PRE_IGNITION": 3,
            "ACTIVITY_WATCH": 2, "REVERSAL_WATCH": 1,
        }
        rows: list[dict] = []
        for state, metrics in candidates:
            finding = latest.get(state.symbol)
            # Never attach yesterday's finding to today's 24H row.
            if finding and trading_session_key(float(finding.get("detected_at") or 0)) != current_session:
                finding = None
            row = {
                "ticker": state.symbol,
                "price": float(state.current.close) if state.current else None,
                "last_feed": state.last_market_feed or None,
                "last_trade_at": state.last_market_trade_at or None,
                "last_boats_trade_at": state.last_boats_trade_at,
                "session_date": state.session_date,
                "verified_24h": True,
                "stage": (finding or {}).get("stage") or "SCANNING",
                "actionable_rank": metrics.get("actionable_rank", "C"),
                "quality_label": metrics.get("quality_label", "DEVELOPING"),
                "quality_score": int(metrics.get("quality_score") or 0),
                "ross_match": bool(metrics.get("ross_match", False)),
                "ross_score": int(metrics.get("ross_score") or 0),
                "change_5s_pct": metrics.get("change5"),
                "change_15s_pct": metrics.get("change15"),
                "change_30s_pct": metrics.get("change30"),
                "vol_ratio_15s": metrics.get("vol15"),
                "dollar_volume_15s": metrics.get("dollar15"),
                "trades_15s": metrics.get("trades15"),
                "extension_pct": metrics.get("extension"),
                "trigger_distance_pct": metrics.get("trigger_distance_pct"),
                "rejection_reasons": list(metrics.get("rejection_reasons", [])),
                "latest_finding": finding,
            }
            rows.append(row)

        rows.sort(key=lambda row: (
            rank_weight.get(str(row.get("actionable_rank") or "C"), 0),
            stage_weight.get(str(row.get("stage") or ""), 0),
            int(row.get("quality_score") or 0),
            float(row.get("change_5s_pct") or 0.0),
        ), reverse=True)
        return rows[:max(1, min(500, int(limit)))]

    def top_movers_sync(self, top: int = 20) -> dict:
        top = max(1, min(50, int(top)))
        r = requests.get(
            f"{settings.alpaca_data_base}/v1beta1/screener/stocks/movers",
            params={"top": top},
            headers=_headers(),
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json()
        gainers = payload.get("gainers", []) if isinstance(payload, dict) else []
        tickers = [str(x.get("symbol", "")).upper() for x in gainers if x.get("symbol")]
        scout = self.store.latest_findings_by_ticker(tickers)
        for row in gainers:
            symbol = str(row.get("symbol", "")).upper()
            if symbol in scout:
                row["scout"] = scout[symbol]
        return {"gainers": gainers, "losers": payload.get("losers", []) if isinstance(payload, dict) else []}

    def current_halts(self) -> list[dict]:
        return sorted(
            (dict(value) for value in self.halts.values() if value.get("is_halted")),
            key=lambda x: x.get("event_at", 0),
            reverse=True,
        )

    async def _handle_status(self, msg: dict) -> None:
        symbol = str(msg.get("S", "")).upper()
        if not symbol:
            return
        status_code = str(msg.get("sc", ""))
        status_message = str(msg.get("sm", ""))
        reason_code = str(msg.get("rc", ""))
        reason_message = str(msg.get("rm", ""))
        raw_ts = msg.get("t")
        try:
            event_ts = int(datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).timestamp())
        except Exception:
            event_ts = int(time.time())

        description = f"{status_message} {reason_message}".lower()
        is_halted = (
            status_code in {"H", "2"}
            or "halt" in description
            or "pause" in description
        )
        is_resume = (
            status_code == "3"
            or "resume" in description
            or "resumption" in description
            or "quotation to resume" in description
        )
        if is_resume:
            is_halted = False

        previous = self.halts.get(symbol)
        row = {
            "ticker": symbol,
            "status_code": status_code,
            "status_message": status_message,
            "reason_code": reason_code,
            "reason_message": reason_message,
            "event_at": event_ts,
            "is_halted": is_halted,
        }
        self.halts[symbol] = row
        await asyncio.to_thread(
            self.store.save_market_status,
            symbol, status_code, status_message, reason_code, reason_message, event_ts, is_halted,
        )
        transition = previous is None or bool(previous.get("is_halted")) != is_halted
        if self.events:
            self.events.publish("halt" if is_halted else "resume", row)

        if transition and (is_halted or is_resume):
            state = self.states.get(symbol)
            metrics = self._metrics(state, float(event_ts)) if state else None
            price = float((metrics or {}).get("price", state.current.close if state and state.current else 0.0) or 0.0)
            finding = Finding(
                ticker=symbol,
                stage="HALT" if is_halted else "RESUME",
                detected_at=float(event_ts),
                price=price,
                score=10 if is_halted else 7,
                vol_ratio_15s=float((metrics or {}).get("vol15", 0.0)),
                vol_ratio_30s=float((metrics or {}).get("vol30", 0.0)),
                change_60s_pct=float((metrics or {}).get("change60", 0.0)),
                extension_pct=float((metrics or {}).get("extension", 0.0)),
                ema9=(metrics or {}).get("ema9"),
                ema21=(metrics or {}).get("ema21"),
                ema9_slope=(metrics or {}).get("ema9_slope"),
                vwap=(metrics or {}).get("vwap"),
                above_vwap=bool((metrics or {}).get("above_vwap", False)),
                quiet_break=bool((metrics or {}).get("quiet_break", False)),
                evidence=[
                    f"market status: {status_message or status_code}",
                    f"reason: {reason_message or reason_code}",
                ],
            )
            snap = self.snapshot(symbol)
            buckets, current = snap if snap else ([], None)
            await self.dispatcher.emit(finding, buckets, current)

        log.info(
            "Market status %s %s code=%s reason=%s",
            symbol, "HALTED" if is_halted else "RESUMED/STATUS", status_code, reason_code,
        )

    async def universe_loop(self):
        while True:
            try:
                selected = await asyncio.to_thread(self.universe.refresh_sync)
                self._desired = selected
                self._universe_ready.set()
                log.info("Universe refreshed: %d symbols%s", len(selected), " (wildcard)" if "*" in selected else "")
                if self.ws:
                    await self._reconcile(self.ws, self.subscribed, "SIP")
                if self.overnight_ws:
                    await self._reconcile(self.overnight_ws, self.overnight_subscribed, "BOATS")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Universe refresh failed")
            await asyncio.sleep(settings.universe_refresh_seconds)

    async def _reconcile(self, ws, subscribed: set[str], label: str):
        if not ws:
            return
        key = "boats" if str(label).upper() == "BOATS" else "sip"
        lock = self._reconcile_locks[key]
        status = self.reconcile_status[key]
        async with lock:
            status["in_progress"] = True
            status["last_started_at"] = int(time.time())
            status["last_error"] = None
            try:
                desired = set(self._desired)
                async def send(payload: dict) -> None:
                    await asyncio.wait_for(
                        ws.send(json.dumps(payload)),
                        timeout=settings.reconcile_send_timeout_seconds,
                    )
                if "*" in desired:
                    if subscribed != {"*"}:
                        await send({"action": "subscribe", "trades": ["*"]})
                        subscribed.clear()
                        subscribed.add("*")
                    return
                add = sorted(desired - subscribed)
                remove = sorted(subscribed - desired)
                # Chunk and bound every send. Reconnect and universe-refresh
                # reconciliation can no longer overlap indefinitely.
                for chunk in _chunks(remove, 1000):
                    await send({"action": "unsubscribe", "trades": chunk})
                for chunk in _chunks(add, 1000):
                    await send({"action": "subscribe", "trades": chunk})
                subscribed.difference_update(remove)
                subscribed.update(add)
                if add or remove:
                    log.info("%s subscriptions updated: +%d -%d total=%d", label, len(add), len(remove), len(subscribed))
            except websockets.exceptions.ConnectionClosed as exc:
                # The shared websocket (self.ws / self.overnight_ws) can die between
                # universe_loop reading it and this send actually going out -- observed
                # 2026-08-19 as frequent Alpaca SIP "keepalive ping timeout" disconnects,
                # roughly once a minute during a network-unstable stretch. This is
                # expected and self-healing: _stream()'s own reconnect loop replaces
                # self.ws/self.overnight_ws independently, and the next universe_loop
                # cycle (settings.universe_refresh_seconds later) reconciles cleanly
                # against the fresh connection. Previously this propagated all the way up
                # to universe_loop's `except Exception: log.exception("Universe refresh
                # failed")`, logging a full ERROR-level stack trace on every single
                # occurrence -- misleading (the universe refresh itself had already
                # succeeded; only the reconcile send failed) and pure noise once the SIP
                # connection is flapping. Log it quietly instead and let the loop retry.
                status["last_error"] = f"{label} websocket closed mid-reconcile (will retry next cycle): {exc}"
                log.info("%s reconcile skipped: websocket closed mid-send, will retry next cycle", label)
            except Exception as exc:
                status["last_error"] = str(exc)
                raise
            finally:
                status["in_progress"] = False
                status["last_completed_at"] = int(time.time())

    def _metrics(self, s: SymbolState, now: float) -> dict | None:
        if not s.current or len(s.buckets) < 4:
            return None

        closed = list(s.buckets)
        full_warmup = len(closed) >= settings.warmup_buckets
        baseline_rows = closed[-settings.baseline_buckets:]
        positive_volumes = [b.volume for b in baseline_rows if b.volume > 0]
        active_median = statistics.median(positive_volumes) if positive_volumes else 0.0
        baseline = max(settings.baseline_volume_floor, active_median)

        current_vol = s.current.volume
        prev = closed[-1] if closed else None
        prev_vol = prev.volume if prev else 0.0
        vol15 = current_vol / baseline
        vol30 = (current_vol + prev_vol) / max(1.0, baseline * 2.0)

        price = s.current.close
        trades15 = s.current.trades
        trades30 = trades15 + (prev.trades if prev else 0)
        dollar15 = current_vol * price
        dollar30 = dollar15 + ((prev.volume * prev.close) if prev else 0.0)
        active_buckets30 = int(s.current.trades > 0) + int(bool(prev and prev.trades > 0))

        def price_ago(seconds: int, minimum_coverage: int):
            target = now - seconds
            candidate = None
            for ts, px in s.price_points:
                if ts <= target:
                    candidate = px
                else:
                    break
            if candidate is not None:
                return candidate
            if s.price_points:
                first_ts, first_px = s.price_points[0]
                if now - first_ts >= minimum_coverage:
                    return first_px
            return None

        price3 = price_ago(3, 2)
        price5 = price_ago(5, 4)
        price10 = price_ago(10, 8)
        price15 = price_ago(15, 12)
        price30 = price_ago(30, 25)
        price60 = price_ago(60, 50)

        change3 = pct_change(price3, price) if price3 else 0.0
        change5 = pct_change(price5, price) if price5 else 0.0
        change10 = pct_change(price10, price) if price10 else 0.0
        change15 = pct_change(price15, price) if price15 else 0.0
        change30 = pct_change(price30, price) if price30 else 0.0
        change60 = pct_change(price60, price) if price60 else 0.0

        prior15_change = pct_change(price30, price15) if price30 is not None and price15 is not None else None
        accel15_pp = change15 - prior15_change if prior15_change is not None else None
        price_accelerating = bool(
            accel15_pp is not None
            and accel15_pp >= settings.price_acceleration_min_pp
            and change15 > 0
        )

        closes = [b.close for b in closed[-40:]] + [price]
        e9 = ema(closes, 9)
        e21 = ema(closes, 21)
        prior_e9 = ema(closes[:-1], 9) if len(closes) > 1 else e9
        e9_gap_pct = pct_change(e21, e9) if e9 is not None and e21 is not None else 0.0
        e9_slope_pct = pct_change(prior_e9, e9) if e9 is not None and prior_e9 is not None else 0.0
        ema_up = e9_slope_pct >= settings.ema_slope_tolerance_pct
        ema_bull = e9_gap_pct >= settings.ema_gap_tolerance_pct
        ema_bear = e9_gap_pct <= -settings.ema_gap_tolerance_pct

        quiet = closed[-8:] if len(closed) >= 8 else closed
        quiet_base = statistics.median([b.close for b in quiet]) if quiet else price
        quiet_high = max((b.high for b in quiet), default=price)
        extension = pct_change(quiet_base, price)
        quiet_break = price >= quiet_high * 1.005

        vwap = s.session_pv / s.session_volume if s.session_volume > 0 else None
        vwap_gap_pct = pct_change(vwap, price) if vwap else None
        above_vwap = bool(vwap is not None and price >= vwap)
        near_vwap = bool(vwap is None or (vwap_gap_pct is not None and vwap_gap_pct >= -settings.vwap_tolerance_pct))

        # Staircase: gradual 1-2 minute participation + higher lows.
        stair_rows = closed[-(settings.staircase_window_buckets - 1):] + [s.current]
        active_stair = [b for b in stair_rows if b.trades > 0]
        stair_change = 0.0
        stair_up_ratio = 0.0
        stair_higher_low_ratio = 0.0
        stair_dollar = 0.0
        stair_trades = 0
        if active_stair:
            stair_change = pct_change(active_stair[0].close, price)
            stair_dollar = sum(b.volume * b.close for b in active_stair)
            stair_trades = sum(b.trades for b in active_stair)
        if len(active_stair) >= 2:
            pairs = list(zip(active_stair, active_stair[1:]))
            up_steps = sum(1 for a, b in pairs if pct_change(a.close, b.close) >= 0.02)
            higher_lows = sum(1 for a, b in pairs if pct_change(a.low, b.low) >= -0.05)
            stair_up_ratio = up_steps / len(pairs)
            stair_higher_low_ratio = higher_lows / len(pairs)
        staircase = bool(
            full_warmup
            and len(active_stair) >= settings.staircase_min_active_buckets
            and stair_change >= settings.staircase_min_change_pct
            and stair_up_ratio >= settings.staircase_min_up_step_ratio
            and stair_higher_low_ratio >= settings.staircase_min_higher_low_ratio
            and stair_dollar >= settings.staircase_min_dollar_volume
            and stair_trades >= settings.staircase_min_trades
            and ema_up
        )

        # Immediate-surge sensor. Velocity is trade-by-trade rather than waiting
        # for a completed 15-second candle.
        surge_velocity = (
            change3 >= settings.surge_min_change_3s_pct
            or change5 >= settings.surge_min_change_5s_pct
            or change10 >= settings.surge_min_change_10s_pct
            or change15 >= settings.surge_min_change_15s_pct
        )
        surge = bool(
            surge_velocity
            and vol15 >= settings.surge_min_vol_ratio
            and dollar15 >= settings.surge_min_dollar_15s
            and trades15 >= settings.surge_min_trades_15s
            and change3 > -0.10
            and change5 > -0.10
        )

        # Structural breakout levels are based only on completed buckets.
        resistance_levels: dict[str, float] = {}
        broken_levels: list[tuple[str, float, float]] = []
        for label, count in (("1m", 4), ("3m", 12), ("5m", 20)):
            if len(closed) < count:
                continue
            level = max(b.high for b in closed[-count:])
            resistance_levels[label] = level
            penetration = pct_change(level, price)
            if penetration >= settings.breakout_min_penetration_pct:
                broken_levels.append((label, level, penetration))
        breakout_window = broken_levels[-1][0] if broken_levels else None
        breakout_level = broken_levels[-1][1] if broken_levels else None
        breakout_penetration_pct = broken_levels[-1][2] if broken_levels else 0.0
        breakout = bool(
            broken_levels
            and vol30 >= settings.breakout_min_vol_ratio
            and dollar30 >= settings.breakout_min_dollar_30s
            and trades30 >= settings.breakout_min_trades_30s
            and (ema_up or ema_bull or above_vwap or price_accelerating)
            and change15 > -0.05
        )

        volume_accelerating = current_vol > prev_vol * 1.25 if prev_vol > 0 else current_vol > baseline * 2

        # Market-quality gate: distinguish continuous, directional participation
        # from isolated prints, empty buckets, gap noise, and alternating chop.
        quality_rows = (closed + [s.current])[-settings.quality_window_buckets:]
        active_rows = [b for b in quality_rows if b.trades > 0]
        active_bucket_ratio = len(active_rows) / max(1, len(quality_rows))
        row_moves = [pct_change(a.close, b.close) for a, b in zip(active_rows, active_rows[1:])]
        path_move = sum(abs(move) for move in row_moves)
        directional_efficiency = abs(pct_change(active_rows[0].close, active_rows[-1].close)) / path_move if len(active_rows) >= 2 and path_move > 0 else 0.0
        directions = [1 if move > 0.02 else -1 if move < -0.02 else 0 for move in row_moves]
        directions = [direction for direction in directions if direction]
        direction_reversals = sum(1 for a, b in zip(directions, directions[1:]) if a != b)
        max_gap_pct = max((abs(pct_change(a.close, b.open)) for a, b in zip(quality_rows, quality_rows[1:])), default=0.0)
        wick_ratios = []
        for row in active_rows:
            body = max(abs(row.close - row.open), max(row.close, 0.01) * 0.0002)
            wick_ratios.append(max(0.0, (row.high - row.low - abs(row.close - row.open)) / body))
        median_wick_ratio = statistics.median(wick_ratios) if wick_ratios else 0.0
        latest_trade_age = max(0.0, now - s.price_points[-1][0]) if s.price_points else float("inf")

        profile = self.quality_profile
        profile_factor = {"strict": 1.25, "balanced": 1.0, "permissive": 0.70}[profile]
        min_active_ratio = min(0.90, settings.quality_min_active_ratio * profile_factor)
        min_trades30 = max(2, round(settings.quality_min_trades_30s * profile_factor))
        min_dollar30 = settings.quality_min_dollar_30s * profile_factor
        min_efficiency = min(0.80, settings.quality_min_directional_efficiency * profile_factor)
        max_reversals = max(2, round(settings.quality_max_direction_reversals / profile_factor))
        max_gap = settings.quality_max_gap_pct / profile_factor
        max_wick = settings.quality_max_wick_ratio / profile_factor
        impulse_quality = bool(
            trades15 >= settings.quality_impulse_min_trades_15s
            and dollar15 >= settings.quality_impulse_min_dollar_15s
            and (change5 >= settings.surge_min_change_5s_pct or change15 >= settings.early_min_change_15s_pct)
        )

        # --- 2026-08-19 experiments: alternative shapes for the participation bar.
        # Default (all flags off) preserves the exact original static bar below. Each flag
        # is independently composable (2026-08-19 combined-test follow-up): #3 sets the
        # starting base (unified loose bar vs. the normal strict one), then #1 and #2 each
        # propose their own fractional reduction off whichever base is active. Reductions
        # are combined by taking the larger single justification, not compounded/multiplied
        # together -- stacking two "50% off" justifications into a ~75%-off bar would not be
        # a considered design, just an accident of implementation order.
        # See MILESTONES/2026-08-19-* for the backtest results behind each one, alone and combined.
        base_min_trades30 = min_trades30
        base_min_dollar30 = min_dollar30
        if settings.experiment_unified_participation_gate:
            # #3: stop independently re-tuning three copies of "is there enough
            # participation" (this quality-layer bar, the separate `regular_participation`
            # gate, and reversal_participation's own copy). Use the single, already-existing
            # looser bar (MIN_30S_DOLLAR_VOLUME/MIN_30S_TRADES) as the one source of truth.
            base_min_trades30 = max(2, settings.min_30s_trades)
            base_min_dollar30 = settings.min_30s_dollar_volume

        reduction = 0.0
        if settings.experiment_adaptive_participation_bar:
            # #1: scale the bar down per corroborating signal already present, instead of
            # lowering it uniformly for every candidate regardless of context.
            corroboration = 0
            if change5 >= 0.5 or change15 >= 0.5:
                corroboration += 1
            if ema_up or ema_bull:
                corroboration += 1
            if above_vwap:
                corroboration += 1
            reduction = max(reduction, min(settings.experiment_adaptive_bar_max_reduction_pct, corroboration * 0.15))
        if settings.experiment_time_decay_participation_bar:
            # #2: the bar starts at its normal strictness and relaxes over a fixed window
            # IF the trend is still holding (not reversing) -- targets the proven reason
            # bigger moves succeed more often: they simply last long enough for a static
            # bar to catch up. Smaller/faster moves never get that runway.
            # relative_activity/fast_single_bucket are computed later in _maybe_emit, not
            # available in this method -- reuse the same formulas locally rather than
            # reaching across methods for state that doesn't exist here yet.
            relative_activity_here = vol15 >= settings.vol_ratio_trigger or vol30 >= settings.vol_ratio_trigger
            fast_single_bucket_here = (
                vol15 >= settings.fast_single_bucket_vol_ratio
                and change15 >= settings.fast_single_bucket_change_15s_pct
                and dollar15 >= settings.fast_single_bucket_dollar_volume
                and trades15 >= settings.fast_single_bucket_trades
            )
            activity_now = bool(relative_activity_here or fast_single_bucket_here or impulse_quality)
            if activity_now and not s.activity_age_at:
                s.activity_age_at = now
            elif not activity_now:
                s.activity_age_at = 0.0
            trend_holding = change15 >= -0.10 and change30 >= -0.15
            if s.activity_age_at and trend_holding:
                age = max(0.0, now - s.activity_age_at)
                decay = min(settings.experiment_time_decay_max_reduction_pct, age / settings.experiment_time_decay_window_seconds * settings.experiment_time_decay_max_reduction_pct)
                reduction = max(reduction, decay)

        effective_min_trades30 = max(2, round(base_min_trades30 * (1.0 - reduction)))
        effective_min_dollar30 = base_min_dollar30 * (1.0 - reduction)

        rejection_reasons: list[str] = []
        illiquid = trades30 < effective_min_trades30 or dollar30 < effective_min_dollar30
        if illiquid and not impulse_quality:
            rejection_reasons.append("LOW PARTICIPATION")
        if active_bucket_ratio < min_active_ratio and not impulse_quality:
            rejection_reasons.append("SPARSE PRINTS")
        if directional_efficiency < min_efficiency and len(active_rows) >= 4:
            rejection_reasons.append("CHOPPY PATH")
        if direction_reversals > max_reversals:
            rejection_reasons.append("EXCESS REVERSALS")
        if max_gap_pct > max_gap:
            rejection_reasons.append("GAP NOISE")
        if median_wick_ratio > max_wick:
            rejection_reasons.append("WICK NOISE")
        if latest_trade_age > settings.quality_max_stale_seconds:
            rejection_reasons.append("STALE TRADES")

        bullish_confirmations = sum((
            bool(ema_up), bool(ema_bull), bool(above_vwap),
            change15 >= settings.early_min_change_15s_pct,
            change30 >= settings.early_min_change_30s_pct,
            directional_efficiency >= min_efficiency,
        ))
        bullish_confirmed = bool(bullish_confirmations >= 3 and not (ema_bear and not above_vwap) and change15 > -0.05)
        if not bullish_confirmed:
            rejection_reasons.append("BULLISH STRUCTURE UNCONFIRMED")

        quality_score = 100
        quality_score -= 24 if "LOW PARTICIPATION" in rejection_reasons else 0
        quality_score -= 18 if "SPARSE PRINTS" in rejection_reasons else 0
        quality_score -= 22 if "CHOPPY PATH" in rejection_reasons else 0
        quality_score -= 12 if "EXCESS REVERSALS" in rejection_reasons else 0
        quality_score -= 15 if "GAP NOISE" in rejection_reasons else 0
        quality_score -= 12 if "WICK NOISE" in rejection_reasons else 0
        quality_score -= 30 if "STALE TRADES" in rejection_reasons else 0
        quality_score -= 18 if "BULLISH STRUCTURE UNCONFIRMED" in rejection_reasons else 0
        quality_score = max(0, min(100, quality_score))
        if "STALE TRADES" in rejection_reasons or "LOW PARTICIPATION" in rejection_reasons:
            quality_label = "ILLIQUID"
        elif any(reason in rejection_reasons for reason in ("CHOPPY PATH", "EXCESS REVERSALS", "GAP NOISE", "WICK NOISE")):
            quality_label = "CHOPPY"
        elif bullish_confirmed and quality_score >= 70:
            quality_label = "CLEAN"
        else:
            quality_label = "DEVELOPING"
        actionable_rank = "B" if quality_label == "CLEAN" else "C"

        score = 0
        evidence: list[str] = []
        if vol15 >= settings.vol_ratio_trigger:
            score += 3
            evidence.append(f"15s volume {vol15:.1f}× baseline")
        if vol15 >= settings.vol_ratio_trigger * 2:
            score += 1
            evidence.append("extreme volume anomaly")
        if vol30 >= settings.vol_ratio_trigger:
            score += 2
            evidence.append(f"30s volume {vol30:.1f}× baseline")
        if change15 >= settings.early_min_change_15s_pct:
            score += 2
            evidence.append(f"15s price +{change15:.2f}%")
        if change30 >= settings.early_min_change_30s_pct:
            score += 2
            evidence.append(f"30s price +{change30:.2f}%")
        if change60 >= settings.price_60s_trigger_pct:
            score += 1
            evidence.append(f"60s context +{change60:.2f}%")
        if price_accelerating:
            score += 1
            evidence.append(f"price acceleration +{accel15_pp:.2f}pp vs prior 15s")
        if surge:
            score += 3
            evidence.append(f"immediate surge: 3s {change3:+.2f}% / 5s {change5:+.2f}% / 10s {change10:+.2f}%")
        if breakout:
            score += 3
            evidence.append(f"{breakout_window} resistance ${breakout_level:.4f} broken by {breakout_penetration_pct:.2f}%")
        if staircase:
            score += 3
            evidence.append(f"staircase +{stair_change:.2f}% up={stair_up_ratio:.0%} higher-lows={stair_higher_low_ratio:.0%}")
        if quiet_break:
            score += 2
            evidence.append("quiet range broken")
        if ema_up:
            score += 1
            evidence.append("EMA9 slope rising")
        if ema_bull:
            score += 1
            evidence.append("EMA9 > EMA21")
        if above_vwap:
            score += 1
            evidence.append("price > session VWAP")
        if volume_accelerating:
            score += 1
            evidence.append("volume accelerating")
        if dollar30 >= settings.min_30s_dollar_volume and trades30 >= settings.min_30s_trades:
            evidence.append(f"30s participation ${dollar30:,.0f} across {trades30} trades")
        if quality_label == "CLEAN" and score >= settings.ignition_score:
            actionable_rank = "A"

        meta = self.universe.metadata.get(s.symbol, {})
        previous_close = meta.get("previous_close")
        gap_pct = pct_change(previous_close, price) if previous_close else None
        day_volume = max(float(meta.get("day_volume") or 0), s.session_volume)
        local_now = datetime.fromtimestamp(now, ET)
        session_start = local_now.replace(hour=20, minute=0, second=0, microsecond=0)
        if local_now.hour < 20:
            session_start -= timedelta(days=1)
        elapsed_minutes = max(1.0, (local_now - session_start).total_seconds() / 60.0)
        projected_session_volume = s.session_volume / elapsed_minutes * 1440.0
        recent_minutes = max(.25, len(quality_rows) * settings.bucket_seconds / 60.0)
        volume_rate_per_minute = sum(row.volume for row in quality_rows) / recent_minutes

        # Reversal context uses a longer window than the wake-up engine. The
        # peak must precede the local low; otherwise an ordinary pullback from
        # a fresh high could be mislabeled as a reversal.
        reversal_rows = (closed + [s.current])[-settings.reversal_lookback_buckets:]
        low_rows = reversal_rows[-settings.reversal_low_window_buckets:]
        reversal_low_row = min(low_rows, key=lambda row: row.low) if low_rows else s.current
        low_index = reversal_rows.index(reversal_low_row) if reversal_low_row in reversal_rows else len(reversal_rows) - 1
        pre_low_rows = reversal_rows[:low_index] or reversal_rows[:1]
        reversal_prior_peak = max((row.high for row in pre_low_rows), default=price)
        reversal_low = float(reversal_low_row.low)
        reversal_drawdown_pct = max(0.0, -pct_change(reversal_prior_peak, reversal_low)) if reversal_prior_peak > 0 else 0.0
        reversal_bounce_pct = pct_change(reversal_low, price) if reversal_low > 0 else 0.0
        reversal_low_age_seconds = max(0.0, now - reversal_low_row.start_ts)
        ema9_reclaimed = bool(e9 is not None and price >= e9 and prev is not None and prior_e9 is not None and prev.close < prior_e9)
        ema21_reclaimed = bool(e21 is not None and price >= e21 and prev is not None and prev.close < (ema(closes[:-1], 21) or e21))
        reclaim_structure = bool(
            e9 is not None and price >= e9 and ema_up
            and (e21 is None or price >= e21 or above_vwap or near_vwap)
        )
        candidate_profile = {
            "velocity": min(100, round(max(0.0, change5) * 28 + max(0.0, change15) * 14 + max(0.0, change30) * 7)),
            "participation": min(100, round(min(vol15 / 8.0, 1.0) * 40 + min(trades30 / 30.0, 1.0) * 30 + min(dollar30 / 25000.0, 1.0) * 30)),
            "structure": min(100, bullish_confirmations * 16 + (10 if quiet_break else 0)),
            "catalyst": 0,
            "quality": quality_score,
            "supply": None,
        }

        # First-leg context is intentionally independent of the later breakout
        # engine. It looks for the transition out of compression while price is
        # still close to the structure that defines risk.
        base_rows = closed[-settings.first_leg_base_buckets:]
        base_low = min((row.low for row in base_rows), default=price)
        base_high = max((row.high for row in base_rows), default=price)
        base_mid = statistics.median([row.close for row in base_rows]) if base_rows else price
        base_range_pct = pct_change(base_low, base_high) if base_low > 0 else 999.0
        base_extension_pct = pct_change(base_mid, price) if base_mid > 0 else 999.0
        base_pairs = list(zip(base_rows, base_rows[1:]))
        higher_low_ratio = (
            sum(1 for left, right in base_pairs if right.low >= left.low * 0.998) / len(base_pairs)
            if base_pairs else 0.0
        )
        compressed = bool(
            len(base_rows) >= settings.first_leg_base_buckets
            and base_range_pct <= settings.first_leg_max_base_range_pct
        )
        orderly_base = bool(compressed or higher_low_ratio >= 0.65)
        near_base = abs(base_extension_pct) <= settings.first_leg_max_extension_pct
        micro_resistance = max((row.high for row in closed[-4:]), default=price)
        pressing_micro_resistance = price >= micro_resistance * 0.9975
        first_leg_velocity = bool(
            change3 >= settings.first_leg_min_change_3s_pct
            or change5 >= settings.first_leg_min_change_5s_pct
            or change15 >= settings.first_leg_min_change_15s_pct
        )
        first_leg_participation = bool(
            vol15 >= settings.first_leg_min_vol_ratio
            and dollar15 >= settings.first_leg_min_dollar_15s
            and trades15 >= settings.first_leg_min_trades_15s
        )
        first_leg_watch = bool(
            full_warmup and orderly_base and near_base and ema_up
            and (pressing_micro_resistance or reclaim_structure)
            and vol15 >= max(1.5, settings.first_leg_min_vol_ratio * 0.6)
        )
        # The final bearish/quality gates are applied in _maybe_emit, where the
        # short-horizon context and confirmation timer are available.
        first_leg_release = bool(first_leg_watch and first_leg_velocity and first_leg_participation)
        if reversal_drawdown_pct >= settings.reversal_min_drawdown_pct and reclaim_structure:
            leg_context = "RECLAIM_RELEASE"
        elif s.continuation_pullback_low is not None:
            leg_context = "PULLBACK_RELEASE"
        elif breakout and breakout_window == "5m":
            leg_context = "HOD_REBREAK"
        elif compressed:
            leg_context = "BASE_RELEASE"
        elif orderly_base:
            leg_context = "CONSOLIDATION_RELEASE"
        else:
            leg_context = "BASE_RELEASE"

        ross_checks = (
            self.min_price <= price <= self.max_price,
            gap_pct is None or gap_pct >= 2.0,
            vol15 >= 2.0,
            dollar30 >= 5000,
            trades30 >= 12,
            quality_label == "CLEAN",
            bool(quiet_break or first_leg_release or breakout or staircase),
            bool(ema_up or ema_bull),
        )
        ross_score = round(sum(ross_checks) / len(ross_checks) * 100)
        ross_match = bool(ross_score >= 75 and quality_label == "CLEAN")

        # Evidence score, not a probability. LULD applies only during the
        # regular session; the exchange feed remains authoritative for an
        # actual Limit State or pause.
        regular_session = (local_now.hour, local_now.minute) >= (9, 30) and local_now.hour < 16
        halt_pressure_score = 0
        if regular_session and quality_label == "CLEAN":
            halt_pressure_score = min(100, round(
                min(max(change5, 0) / 2.0, 1.0) * 24
                + min(max(change15, 0) / 4.0, 1.0) * 20
                + min(vol15 / 8.0, 1.0) * 20
                + min(trades15 / 35.0, 1.0) * 16
                + min(dollar15 / 35000.0, 1.0) * 12
                + (8 if price_accelerating else 0)
            ))

        return {
            "full_warmup": full_warmup,
            "price": price,
            "baseline_volume": baseline,
            "vol15": vol15,
            "vol30": vol30,
            "volume15": current_vol,
            "volume30": current_vol + prev_vol,
            "trades15": trades15,
            "trades30": trades30,
            "dollar15": dollar15,
            "dollar30": dollar30,
            "active_buckets30": active_buckets30,
            "active_bucket_ratio": active_bucket_ratio,
            "directional_efficiency": directional_efficiency,
            "direction_reversals": direction_reversals,
            "max_gap_pct": max_gap_pct,
            "median_wick_ratio": median_wick_ratio,
            "latest_trade_age": latest_trade_age,
            "quality_label": quality_label,
            "quality_score": quality_score,
            "actionable_rank": actionable_rank,
            "rejection_reasons": rejection_reasons,
            "bullish_confirmed": bullish_confirmed,
            "previous_close": previous_close,
            "gap_pct": gap_pct,
            "day_volume": day_volume,
            "projected_session_volume": projected_session_volume,
            "volume_rate_per_minute": volume_rate_per_minute,
            "float_shares": None,
            "float_turnover": None,
            "candidate_profile": candidate_profile,
            "base_range_pct": base_range_pct,
            "base_extension_pct": base_extension_pct,
            "orderly_base": orderly_base,
            "near_base": near_base,
            "higher_low_ratio": higher_low_ratio,
            "micro_resistance": micro_resistance,
            "pressing_micro_resistance": pressing_micro_resistance,
            "first_leg_participation": first_leg_participation,
            "first_leg_watch": first_leg_watch,
            "first_leg_release": first_leg_release,
            "leg_context": leg_context,
            "ross_match": ross_match,
            "ross_score": ross_score,
            "base_low": base_low,
            "base_high": base_high,
            "halt_pressure_score": halt_pressure_score,
            "reversal_prior_peak": reversal_prior_peak,
            "reversal_low": reversal_low,
            "reversal_drawdown_pct": reversal_drawdown_pct,
            "reversal_bounce_pct": reversal_bounce_pct,
            "reversal_low_age_seconds": reversal_low_age_seconds,
            "ema9_reclaimed": ema9_reclaimed,
            "ema21_reclaimed": ema21_reclaimed,
            "reclaim_structure": reclaim_structure,
            "change3": change3,
            "change5": change5,
            "change10": change10,
            "change15": change15,
            "change30": change30,
            "change60": change60,
            "prior15_change": prior15_change,
            "accel15_pp": accel15_pp,
            "price_accelerating": price_accelerating,
            "volume_accelerating": volume_accelerating,
            "ema9": e9,
            "ema21": e21,
            "ema9_slope": e9 - prior_e9 if e9 is not None and prior_e9 is not None else None,
            "ema9_slope_pct": e9_slope_pct,
            "ema_gap_pct": e9_gap_pct,
            "ema_up": ema_up,
            "ema_bull": ema_bull,
            "ema_bear": ema_bear,
            "vwap": vwap,
            "vwap_gap_pct": vwap_gap_pct,
            "above_vwap": above_vwap,
            "near_vwap": near_vwap,
            "quiet_break": quiet_break,
            "extension": extension,
            "staircase": staircase,
            "stair_change": stair_change,
            "stair_up_ratio": stair_up_ratio,
            "stair_higher_low_ratio": stair_higher_low_ratio,
            "stair_dollar": stair_dollar,
            "stair_trades": stair_trades,
            "surge": surge,
            "surge_velocity": surge_velocity,
            "breakout": breakout,
            "breakout_level": breakout_level,
            "breakout_window": breakout_window,
            "breakout_penetration_pct": breakout_penetration_pct,
            "resistance_levels": resistance_levels,
            "score": score,
            "evidence": evidence,
        }

    async def _maybe_emit(self, s: SymbolState, m: dict, ts: float, fast: bool = False):
        if not (self.min_price <= m["price"] <= self.max_price):
            return
        reversal_extension_exception = bool(
            m.get("reversal_drawdown_pct", 0) >= settings.reversal_min_drawdown_pct
            and m.get("reversal_bounce_pct", 0) <= 12.0
        )
        halt_pressure_qualifies = bool(m.get("halt_pressure_score", 0) >= 82 and m.get("quality_label") == "CLEAN")
        if m["extension"] > settings.max_early_extension_pct and not reversal_extension_exception:
            return

        relative_activity = m["vol15"] >= settings.vol_ratio_trigger or m["vol30"] >= settings.vol_ratio_trigger
        fast_single_bucket = (
            m["vol15"] >= settings.fast_single_bucket_vol_ratio
            and m["change15"] >= settings.fast_single_bucket_change_15s_pct
            and m["dollar15"] >= settings.fast_single_bucket_dollar_volume
            and m["trades15"] >= settings.fast_single_bucket_trades
        )
        regular_participation = (
            m["dollar30"] >= settings.min_30s_dollar_volume
            and m["trades30"] >= settings.min_30s_trades
            and (not settings.require_two_active_buckets or m["active_buckets30"] >= 2)
        )

        sudden_impulse = (
            m["change15"] >= settings.early_min_change_15s_pct
            or m["change30"] >= settings.early_min_change_30s_pct
            or (m["quiet_break"] and m["extension"] >= settings.early_min_extension_pct)
        )
        bearish_short = m["change15"] < -0.15 and m["change30"] < -0.20 and not m["staircase"]
        deeply_below_vwap = bool(m["vwap_gap_pct"] is not None and m["vwap_gap_pct"] < -settings.early_max_below_vwap_pct)
        structural_failure = (
            deeply_below_vwap and m["ema_bear"] and not m["quiet_break"]
            and not m["price_accelerating"] and not m["staircase"]
        )
        structure_ok = m["ema_up"] or m["ema_bull"] or m["quiet_break"] or m["price_accelerating"] or m["staircase"]
        quality_actionable = m["quality_label"] == "CLEAN" and m["bullish_confirmed"]

        # V6.3 shadow recipe. These ingredients use only values available at
        # this evaluation timestamp. They are persisted for replay calibration
        # and are intentionally silent until lead-time and false-arm rates are
        # measured across representative sessions.
        recipe_checks = {
            "compressed or orderly base": bool(m.get("orderly_base")),
            "price remains near the base": bool(m.get("near_base")),
            "pressing a nearby trigger": bool(m.get("pressing_micro_resistance")),
            "EMA structure is improving": bool(m["ema_up"] or m["ema_bull"]),
            "relative volume is waking up": bool(m["vol15"] >= max(1.5, settings.first_leg_min_vol_ratio * 0.6)),
            "participation is broadening": bool(
                m["dollar15"] >= settings.first_leg_min_dollar_15s * 0.5
                and m["trades15"] >= max(2, settings.first_leg_min_trades_15s // 2)
            ),
            "price or volume is accelerating": bool(m["price_accelerating"] or m["volume_accelerating"] or m["change3"] > 0),
            "path avoids bearish failure": bool(not bearish_short and not structural_failure),
        }
        recipe_present = [name for name, present in recipe_checks.items() if present]
        recipe_missing = [name for name, present in recipe_checks.items() if not present]
        recipe_score = round(len(recipe_present) / len(recipe_checks) * 10)
        trigger_level = float(m.get("micro_resistance") or m["price"])
        trigger_distance_pct = ((trigger_level - m["price"]) / m["price"] * 100.0) if m["price"] else None
        base_extension = float(m.get("base_extension_pct") or 0.0)

        def timeliness_label(extension: float) -> str:
            if extension <= 0.75:
                return "PRE_IGNITION"
            if extension <= 2.0:
                return "AT_IGNITION"
            return "LATE"

        first_leg_candidate = bool(
            m.get("first_leg_watch") and not bearish_short and not structural_failure
            and m["direction_reversals"] <= settings.quality_max_direction_reversals
            and m["median_wick_ratio"] <= settings.quality_max_wick_ratio
        )
        pre_ignition_recipe_qualifies = bool(
            first_leg_candidate
            and recipe_score >= 7
            and trigger_distance_pct is not None
            and -0.35 <= trigger_distance_pct <= 0.75
            and base_extension <= 0.75
        )
        if first_leg_candidate:
            if not s.first_leg_candidate_at:
                s.first_leg_candidate_at = ts
                s.first_leg_context = str(m.get("leg_context") or "BASE_RELEASE")
            candidate_age_seconds = max(0.0, ts - s.first_leg_candidate_at)
            promotion_trace = build_promotion_trace(
                m, relative_activity=relative_activity, fast_single_bucket=fast_single_bucket,
                regular_participation=regular_participation, sudden_impulse=sudden_impulse,
                bearish_short=bearish_short, structural_failure=structural_failure, structure_ok=structure_ok,
                quality_actionable=quality_actionable, first_leg_candidate=first_leg_candidate,
                candidate_age_seconds=candidate_age_seconds,
            )
            if pre_ignition_recipe_qualifies and not s.pre_ignition_finding_id:
                watch_profile = dict(m["candidate_profile"])
                watch_profile["promotion_trace"] = promotion_trace
                watch = Finding(
                    ticker=s.symbol, stage="PRE_IGNITION", detected_at=ts, price=m["price"],
                    score=min(10, int(m["score"])), vol_ratio_15s=m["vol15"], vol_ratio_30s=m["vol30"],
                    change_60s_pct=m["change60"], extension_pct=m["base_extension_pct"],
                    ema9=m["ema9"], ema21=m["ema21"], ema9_slope=m["ema9_slope"], vwap=m["vwap"],
                    above_vwap=m["above_vwap"], quiet_break=m["quiet_break"],
                    evidence=list(m["evidence"]) + [f"{s.first_leg_context.lower().replace('_', ' ')} developing", f"base range {m['base_range_pct']:.2f}%"],
                    change_3s_pct=m["change3"], change_5s_pct=m["change5"], change_10s_pct=m["change10"],
                    change_15s_pct=m["change15"], change_30s_pct=m["change30"], accel_15s_pp=m["accel15_pp"],
                    dollar_volume_15s=m["dollar15"], dollar_volume_30s=m["dollar30"], trades_15s=m["trades15"], trades_30s=m["trades30"],
                    breakout_level=m["micro_resistance"], breakout_window="micro", signals=["PRE_IGNITION", "ARMED"],
                    quality_label="DEVELOPING", quality_score=m["quality_score"], actionable_rank="C",
                    rejection_reasons=m["rejection_reasons"], directional_efficiency=m["directional_efficiency"],
                    active_bucket_ratio=m["active_bucket_ratio"], direction_reversals=m["direction_reversals"],
                    previous_close=m["previous_close"], gap_pct=m["gap_pct"], day_volume=m["day_volume"],
                    projected_session_volume=m["projected_session_volume"], volume_rate_per_minute=m["volume_rate_per_minute"],
                    candidate_profile=watch_profile, episode_id=s.episode_id,
                    leg_context=s.first_leg_context, ross_match=m["ross_match"], ross_score=m["ross_score"],
                    detection_timeframe_seconds=settings.bucket_seconds,
                    formation_start_at=(ts - settings.first_leg_base_buckets * settings.bucket_seconds),
                    formation_end_at=ts, formation_low=m.get("base_low"), formation_high=m.get("base_high"),
                    trigger_level=trigger_level, invalidation_level=m.get("base_low"), urgency="WATCH",
                    engine_version=settings.app_version, lifecycle_phase="ARMED", shadow_mode=True,
                    recipe_score=recipe_score, recipe_present=recipe_present, recipe_missing=recipe_missing,
                    trigger_distance_pct=trigger_distance_pct, base_extension_at_detection_pct=base_extension,
                    timeliness_label=timeliness_label(base_extension),
                )
                self._decorate_hybrid(watch, "python")
                snap = self.snapshot(s.symbol)
                buckets, current = snap if snap else ([], None)
                s.pre_ignition_finding_id = await self.dispatcher.emit(watch, buckets, current)
        else:
            s.first_leg_candidate_at = 0.0
            s.first_leg_context = None
            s.pre_ignition_finding_id = None

        first_leg_qualifies = bool(
            first_leg_candidate and m.get("first_leg_release") and quality_actionable
            and ts - s.first_leg_candidate_at >= settings.first_leg_confirmation_seconds
            and ts - s.last_stage_alert_at.get("FIRST_LEG", 0.0) >= settings.first_leg_cooldown_seconds
        )

        candidate_age_seconds = max(0.0, ts - s.first_leg_candidate_at) if s.first_leg_candidate_at else 0.0
        early_release_decision = evaluate_early_release(
            m,
            first_leg_candidate=first_leg_candidate,
            quality_actionable=quality_actionable,
            participation_ok=bool(regular_participation or fast_single_bucket or m.get("staircase")),
            trigger_distance_pct=trigger_distance_pct,
            candidate_age_seconds=candidate_age_seconds,
        )
        early_release_qualifies = bool(
            early_release_decision["ready"]
            and ts - s.last_stage_alert_at.get("EARLY", 0.0) >= settings.first_leg_cooldown_seconds
        )

        early_signal_decision = evaluate_early_signal(
            m,
            first_leg_candidate=first_leg_candidate,
            quality_actionable=quality_actionable,
            participation_ok=bool(regular_participation or fast_single_bucket or m.get("staircase")),
            structure_ok=bool(structure_ok),
            bullish_confirmed=bool(m.get("bullish_confirmed")),
            bearish_short=bearish_short,
            structural_failure=structural_failure,
            relative_activity=bool(relative_activity or fast_single_bucket or m.get("staircase")),
            trigger_distance_pct=trigger_distance_pct,
            candidate_age_seconds=candidate_age_seconds,
        )
        early_signal_qualifies = bool(
            early_signal_decision["ready"]
            and ts - s.last_stage_alert_at.get("EARLY", 0.0) >= settings.early_signal_cooldown_seconds
        )

        reversal_context = bool(
            m["reversal_drawdown_pct"] >= settings.reversal_min_drawdown_pct
            and m["reversal_low_age_seconds"] <= settings.reversal_max_low_age_seconds
        )
        reversal_participation = bool(
            (m["vol15"] >= settings.reversal_min_vol_ratio or m["vol30"] >= settings.reversal_min_vol_ratio)
            and m["dollar30"] >= settings.reversal_min_dollar_30s
            and m["trades30"] >= settings.reversal_min_trades_30s
        )
        reversal_fresh_participation = bool(
            m["vol15"] >= settings.reversal_min_vol_ratio_15s
            and m["dollar15"] >= settings.reversal_min_dollar_15s
            and m["trades15"] >= settings.reversal_min_trades_15s
        )
        reversal_watch_qualifies = bool(
            reversal_context and reversal_participation
            and m["reversal_bounce_pct"] >= settings.reversal_watch_min_bounce_pct
            and (m["change5"] > 0 or m["change15"] > 0 or m["price_accelerating"])
            and not bearish_short
        )
        reclaim_qualifies = bool(
            reversal_context and reversal_participation and reversal_fresh_participation and quality_actionable
            and m["reversal_bounce_pct"] >= settings.reversal_reclaim_min_bounce_pct
            and m["reclaim_structure"]
            and (m["ema9_reclaimed"] or m["ema21_reclaimed"] or m["above_vwap"] or m["ema_bull"])
            and ts - s.last_reversal_episode_at >= settings.reversal_episode_cooldown_seconds
        )

        if s.reversal_phase != "IDLE" and ts - s.reversal_started_at > settings.reversal_max_low_age_seconds * 2:
            s.reversal_phase = "IDLE"
            s.reversal_low = None
            s.reversal_peak = None
            s.reversal_pullback_low = None

        if s.reversal_phase in {"RECLAIM", "PULLBACK"}:
            s.reversal_peak = max(float(s.reversal_peak or m["price"]), m["price"])
            pullback_depth = max(0.0, -pct_change(float(s.reversal_peak), m["price"]))
            structural_hold = bool(
                (m["ema21"] is None or m["price"] >= m["ema21"] * 0.9975)
                and (m["vwap"] is None or m["price"] >= m["vwap"] * 0.9975)
            )
            if settings.reversal_pullback_min_pct <= pullback_depth <= settings.reversal_pullback_max_pct and structural_hold:
                s.reversal_phase = "PULLBACK"
                s.reversal_pullback_low = min(float(s.reversal_pullback_low or m["price"]), m["price"])

        reversal_rearm_qualifies = bool(
            s.reversal_phase == "PULLBACK" and quality_actionable and reversal_participation
            and s.reversal_pullback_low is not None
            and pct_change(s.reversal_pullback_low, m["price"]) >= settings.reversal_rearm_min_bounce_pct
            and (m["change5"] >= settings.reversal_rearm_min_bounce_pct or m["change15"] >= settings.early_min_change_15s_pct)
            and m["ema9"] is not None and m["price"] >= m["ema9"]
        )

        reclaim_stage_candidate = "VWAP_RECLAIM" if m["above_vwap"] else "EMA_RECLAIM"
        reclaim_safety = evaluate_reentry_safety(reclaim_stage_candidate, m)
        if reclaim_qualifies and not reclaim_safety["ready"]:
            reclaim_qualifies = False

        reversal_rearm_safety = evaluate_reentry_safety("REARM", m)
        if reversal_rearm_qualifies and not reversal_rearm_safety["ready"]:
            reversal_rearm_qualifies = False

        # Preserve early awareness without surfacing noisy activity as an alert.
        # A WATCH finding is persisted/SSE-visible but is silent by default and
        # does not advance the ticker's actionable episode rank.
        if not quality_actionable:
            watchable = relative_activity and (regular_participation or fast_single_bucket) and (sudden_impulse or m["score"] >= settings.early_score)
            reversal_watch = reversal_watch_qualifies and s.reversal_phase == "IDLE" and ts - s.last_watch_at >= settings.quality_watch_cooldown_seconds
            activity_watch = watchable and s.last_stage_rank == 0 and ts - s.last_watch_at >= settings.quality_watch_cooldown_seconds
            if reversal_watch or activity_watch:
                watch_stage = "REVERSAL_WATCH" if reversal_watch else "ACTIVITY_WATCH"
                watch_evidence = list(m["evidence"])
                if reversal_watch:
                    watch_evidence.extend([
                        f"prior selloff -{m['reversal_drawdown_pct']:.2f}% from ${m['reversal_prior_peak']:.4f}",
                        f"local-low bounce +{m['reversal_bounce_pct']:.2f}% from ${m['reversal_low']:.4f}",
                        "reversal developing; structural reclaim not confirmed",
                    ])
                watch_profile = dict(m["candidate_profile"])
                watch_profile["promotion_trace"] = build_promotion_trace(
                    m, relative_activity=relative_activity, fast_single_bucket=fast_single_bucket,
                    regular_participation=regular_participation, sudden_impulse=sudden_impulse,
                    bearish_short=bearish_short, structural_failure=structural_failure, structure_ok=structure_ok,
                    quality_actionable=quality_actionable, first_leg_candidate=first_leg_candidate,
                    candidate_age_seconds=(max(0.0, ts - s.first_leg_candidate_at) if s.first_leg_candidate_at else 0.0),
                )
                watch = Finding(
                    ticker=s.symbol, stage=watch_stage, detected_at=ts, price=m["price"], score=min(10, int(m["score"])),
                    vol_ratio_15s=m["vol15"], vol_ratio_30s=m["vol30"], change_60s_pct=m["change60"], extension_pct=m["extension"],
                    ema9=m["ema9"], ema21=m["ema21"], ema9_slope=m["ema9_slope"], vwap=m["vwap"], above_vwap=m["above_vwap"],
                    quiet_break=m["quiet_break"], evidence=watch_evidence, change_3s_pct=m["change3"], change_5s_pct=m["change5"],
                    change_10s_pct=m["change10"], change_15s_pct=m["change15"], change_30s_pct=m["change30"], accel_15s_pp=m["accel15_pp"],
                    dollar_volume_15s=m["dollar15"], dollar_volume_30s=m["dollar30"], trades_15s=m["trades15"], trades_30s=m["trades30"],
                    breakout_level=m["breakout_level"], breakout_window=m["breakout_window"], signals=[watch_stage],
                    quality_label=m["quality_label"], quality_score=m["quality_score"], actionable_rank="C",
                    rejection_reasons=m["rejection_reasons"], directional_efficiency=m["directional_efficiency"],
                    active_bucket_ratio=m["active_bucket_ratio"], direction_reversals=m["direction_reversals"],
                    previous_close=m["previous_close"], gap_pct=m["gap_pct"], day_volume=m["day_volume"],
                    projected_session_volume=m["projected_session_volume"], volume_rate_per_minute=m["volume_rate_per_minute"],
                    float_shares=m["float_shares"], float_turnover=m["float_turnover"], candidate_profile=watch_profile,
                    episode_id=s.episode_id, reversal_phase="WATCH" if reversal_watch else None,
                    reversal_low=m["reversal_low"] if reversal_watch else None,
                    reversal_drawdown_pct=m["reversal_drawdown_pct"] if reversal_watch else None,
                )
                s.last_watch_at = ts
                if reversal_watch:
                    s.reversal_phase = "WATCH"
                    s.reversal_low = m["reversal_low"]
                    s.reversal_peak = m["price"]
                    s.reversal_started_at = ts
                self._decorate_hybrid(watch, "python")
                snap = self.snapshot(s.symbol)
                buckets, current = snap if snap else ([], None)
                await self.dispatcher.emit(watch, buckets, current)
            return

        early_qualifies = bool(
            m["full_warmup"]
            and m["score"] >= settings.early_score
            and (relative_activity or fast_single_bucket or m["staircase"])
            and (regular_participation or fast_single_bucket or m["staircase"])
            and (sudden_impulse or m["staircase"])
            and not bearish_short
            and not structural_failure
            and structure_ok
            and quality_actionable
        )
        staircase_qualifies = bool(early_qualifies and m["staircase"])
        fresh_velocity = max(m["change3"], m["change5"], m["change15"], m["change30"])
        surge_structure_ok = bool(
            m["ema_bull"]
            or (
                m["above_vwap"]
                and m["dollar15"] >= settings.surge_weak_structure_min_dollar_15s
                and m["trades15"] >= settings.surge_weak_structure_min_trades_15s
            )
        )
        surge_qualifies = bool(m["surge"] and quality_actionable and surge_structure_ok)
        breakout_continuation = evaluate_breakout_continuation_quality(m)
        breakout_qualifies = bool(
            m["breakout"] and quality_actionable
            and fresh_velocity >= settings.breakout_min_fresh_velocity_pct
            and breakout_continuation["ready"]
        )

        ignition_participation = m["dollar30"] >= settings.ignition_min_30s_dollar_volume and m["trades30"] >= settings.ignition_min_30s_trades
        ignition_impulse = (
            m["change15"] >= settings.ignition_min_change_15s_pct
            or m["change30"] >= settings.ignition_min_change_30s_pct
            or (m["quiet_break"] and m["extension"] >= settings.ignition_min_extension_pct)
        )
        ignition_structure = m["above_vwap"] and (
            m["ema_bull"] or (m["ema_up"] and (m["quiet_break"] or m["price_accelerating"] or m["staircase"] or m["breakout"]))
        )
        ignition_qualifies = bool(
            m["full_warmup"] and m["score"] >= settings.ignition_score
            and ignition_participation and ignition_impulse and ignition_structure
            and fresh_velocity >= settings.ignition_min_fresh_velocity_pct
            and quality_actionable
        )
        ignition_continuation = evaluate_late_stage_continuation_quality("IGNITION", m)
        if ignition_qualifies and not ignition_continuation["ready"]:
            ignition_qualifies = False

        halt_pressure_continuation = evaluate_late_stage_continuation_quality("HALT_PRESSURE", m)
        if halt_pressure_qualifies and not halt_pressure_continuation["ready"]:
            halt_pressure_qualifies = False

        if s.continuation_started_at and s.continuation_peak is not None:
            s.continuation_peak = max(s.continuation_peak, m["price"])
            continuation_depth = max(0.0, -pct_change(s.continuation_peak, m["price"]))
            if settings.reversal_pullback_min_pct <= continuation_depth <= settings.reversal_pullback_max_pct:
                s.continuation_pullback_low = min(float(s.continuation_pullback_low or m["price"]), m["price"])

        rearm_qualifies = False
        if breakout_qualifies and s.last_stage_rank >= 3 and s.last_breakout_level:
            level_improvement = pct_change(s.last_breakout_level, float(m["breakout_level"] or 0.0))
            rearm_qualifies = (
                level_improvement >= settings.rearm_min_level_improvement_pct
                and ts - s.last_alert_at >= settings.rearm_min_seconds
                and s.continuation_pullback_low is not None
                and pct_change(s.continuation_pullback_low, m["price"]) >= settings.reversal_rearm_min_bounce_pct
            )

        rearm_safety = evaluate_reentry_safety("REARM", m)
        if rearm_qualifies and not rearm_safety["ready"]:
            rearm_qualifies = False

        if not any((early_signal_qualifies, early_release_qualifies, first_leg_qualifies, early_qualifies, surge_qualifies, breakout_qualifies, ignition_qualifies, rearm_qualifies, reclaim_qualifies, reversal_rearm_qualifies)):
            return

        signals: list[str] = []
        if first_leg_qualifies:
            signals.extend(["FIRST_LEG", str(s.first_leg_context or m["leg_context"])])
        if early_signal_qualifies:
            signals.extend(["EARLY", "EARLY_SIGNAL", str(s.first_leg_context or m["leg_context"])])
        elif early_release_qualifies:
            signals.extend(["EARLY", "EARLY_RELEASE", str(s.first_leg_context or m["leg_context"])])
        elif early_qualifies:
            signals.append("EARLY")
        if m["staircase"]:
            signals.append("STAIRCASE")
        if surge_qualifies:
            signals.append("SURGE")
        if breakout_qualifies:
            signals.append("BREAKOUT")
        if ignition_qualifies:
            signals.append("IGNITION")
        reclaim_stage = "VWAP_RECLAIM" if m["above_vwap"] else "EMA_RECLAIM"
        if reclaim_qualifies:
            signals.extend(["RECLAIM", reclaim_stage])

        if reversal_rearm_qualifies:
            stage, rank = "REARM", 5
            signals.extend(["RECLAIM", "FIRST_PULLBACK", "REARM"])
        elif reclaim_qualifies:
            stage, rank = reclaim_stage, 3
        elif rearm_qualifies:
            stage, rank = "REARM", max(5, s.last_stage_rank)
            signals.append("REARM")
        elif halt_pressure_qualifies:
            stage, rank = "HALT_PRESSURE", 7
            signals.extend(["HALT_WATCH", "HALT_PRESSURE"])
        elif ignition_qualifies:
            stage, rank = "IGNITION", 5
        elif breakout_qualifies:
            stage, rank = "BREAKOUT", 4
        elif surge_qualifies:
            stage, rank = "SURGE", 3
        elif early_signal_qualifies:
            stage, rank = "EARLY", 2
        elif early_release_qualifies:
            stage, rank = "EARLY", 2
        elif first_leg_qualifies:
            stage, rank = "FIRST_LEG", 1
        elif staircase_qualifies and not sudden_impulse:
            stage, rank = "STAIRCASE", 2
        elif early_qualifies:
            stage, rank = "EARLY", 2
        else:
            return

        # v6.6.3: do not turn an already-extended expansion into a fresh
        # actionable chase alert. Explicit continuation/re-entry stages remain
        # eligible because they require their own pullback/reclaim structure.
        if should_suppress_late_fresh_promotion(stage, m):
            return

        # v6.6.7: forward-outcome audits showed B-rank EARLY alerts had
        # materially worse 5-minute return/adverse excursion and contained all
        # observed EARLY false positives in the captured mature cohort. Keep B
        # candidates tracked internally so they can still confirm into BREAKOUT/
        # IGNITION, but reserve fresh EARLY notifications for A-rank setups.
        if stage == "EARLY" and not should_allow_fresh_early_actionable(m.get("actionable_rank")):
            return

        if stage in {"EMA_RECLAIM", "VWAP_RECLAIM"}:
            s.episode_id += 1
            s.last_stage_rank = 0
            s.last_stage_alert_at.clear()
        if stage != "REARM" and rank <= s.last_stage_rank:
            return
        if ts - s.last_stage_alert_at.get(stage, 0.0) < settings.signal_stage_cooldown_seconds:
            return

        # Fast evaluations may publish the purpose-built event-driven signals. The
        # slower EARLY/STaircase path still requires the established fast gate.
        if fast and stage in {"EARLY", "STAIRCASE"} and not (
            (m["vol15"] >= settings.fast_vol_ratio_trigger and (m["change15"] >= settings.fast_price_15s_pct or m["change30"] >= settings.fast_price_30s_pct))
            or fast_single_bucket or m["staircase"]
        ):
            return

        catalyst = self.store.recent_catalyst(s.symbol)
        if catalyst and "CATALYST" not in signals:
            signals.append("CATALYST")
        signals = list(dict.fromkeys(signals))

        promoted_profile = dict(m["candidate_profile"])
        promoted_trace = build_promotion_trace(
            m, relative_activity=relative_activity, fast_single_bucket=fast_single_bucket,
            regular_participation=regular_participation, sudden_impulse=sudden_impulse,
            bearish_short=bearish_short, structural_failure=structural_failure, structure_ok=structure_ok,
            quality_actionable=quality_actionable, first_leg_candidate=first_leg_candidate,
            candidate_age_seconds=(max(0.0, ts - s.first_leg_candidate_at) if s.first_leg_candidate_at else 0.0),
        )
        promoted_trace.update({
            "promoted": True,
            "selected_stage": stage,
            "promotion_delay_seconds": (round(max(0.0, ts - s.first_leg_candidate_at), 3) if s.first_leg_candidate_at else None),
            "early_release": early_release_decision,
            "early_release_used": bool(early_release_qualifies),
            "early_signal": early_signal_decision,
            "early_signal_used": bool(early_signal_qualifies),
            "breakout_continuation": breakout_continuation,
            "reentry_safety": (
                rearm_safety if stage == "REARM"
                else reclaim_safety if stage in {"VWAP_RECLAIM", "EMA_RECLAIM"}
                else None
            ),
            "late_stage_continuation": (
                ignition_continuation if stage == "IGNITION"
                else halt_pressure_continuation if stage == "HALT_PRESSURE"
                else None
            ),
        })
        promoted_profile["promotion_trace"] = promoted_trace

        f = Finding(
            ticker=s.symbol,
            stage=stage,
            detected_at=ts,
            price=m["price"],
            score=min(10, int(m["score"])),
            vol_ratio_15s=m["vol15"],
            vol_ratio_30s=m["vol30"],
            change_60s_pct=m["change60"],
            extension_pct=m["extension"],
            ema9=m["ema9"],
            ema21=m["ema21"],
            ema9_slope=m["ema9_slope"],
            vwap=m["vwap"],
            above_vwap=m["above_vwap"],
            quiet_break=m["quiet_break"],
            evidence=list(m["evidence"]),
            change_3s_pct=m["change3"],
            change_5s_pct=m["change5"],
            change_10s_pct=m["change10"],
            change_15s_pct=m["change15"],
            change_30s_pct=m["change30"],
            accel_15s_pp=m["accel15_pp"],
            dollar_volume_15s=m["dollar15"],
            dollar_volume_30s=m["dollar30"],
            trades_15s=m["trades15"],
            trades_30s=m["trades30"],
            breakout_level=m["breakout_level"],
            breakout_window=m["breakout_window"],
            signals=signals,
            quality_label=m["quality_label"],
            quality_score=m["quality_score"],
            actionable_rank=m["actionable_rank"],
            rejection_reasons=m["rejection_reasons"],
            directional_efficiency=m["directional_efficiency"],
            active_bucket_ratio=m["active_bucket_ratio"],
            direction_reversals=m["direction_reversals"],
            previous_close=m["previous_close"],
            gap_pct=m["gap_pct"],
            day_volume=m["day_volume"],
            projected_session_volume=m["projected_session_volume"],
            volume_rate_per_minute=m["volume_rate_per_minute"],
            float_shares=m["float_shares"],
            float_turnover=m["float_turnover"],
            candidate_profile=promoted_profile,
            episode_id=s.episode_id,
            reversal_phase="REARM" if reversal_rearm_qualifies else "RECLAIM" if reclaim_qualifies else s.reversal_phase if s.reversal_phase != "IDLE" else None,
            reversal_low=s.reversal_low or (m["reversal_low"] if reclaim_qualifies else None),
            reversal_drawdown_pct=m["reversal_drawdown_pct"] if (reclaim_qualifies or reversal_rearm_qualifies) else None,
            leg_context=str(s.first_leg_context or m["leg_context"]) if (first_leg_qualifies or early_release_qualifies or early_signal_qualifies) else None,
            ross_match=m["ross_match"],
            ross_score=m["ross_score"],
            detection_timeframe_seconds=settings.bucket_seconds,
            formation_start_at=(ts - settings.first_leg_base_buckets * settings.bucket_seconds),
            formation_end_at=ts,
            formation_low=m.get("base_low"),
            formation_high=m.get("base_high"),
            trigger_level=m.get("breakout_level") or m.get("micro_resistance"),
            invalidation_level=m.get("base_low"),
            halt_pressure_score=int(m.get("halt_pressure_score") or 0),
            urgency=("NOW" if stage in {"FIRST_LEG","EARLY","HALT_PRESSURE"} else "EXTENDED" if m["extension"] >= 8 else "CONFIRMED" if stage in {"IGNITION","BREAKOUT","SURGE"} else "WATCH"),
            engine_version=settings.app_version,
            lifecycle_phase=("REARM" if stage == "REARM" else "CONFIRMED" if stage in {"IGNITION","BREAKOUT","SURGE"} else "IGNITING"),
            shadow_mode=False, recipe_score=recipe_score, recipe_present=recipe_present, recipe_missing=recipe_missing,
            trigger_distance_pct=trigger_distance_pct, base_extension_at_detection_pct=base_extension,
            timeliness_label=timeliness_label(base_extension), precursor_finding_id=s.pre_ignition_finding_id,
        )
        if early_signal_qualifies:
            f.evidence.extend([
                f"early signal evidence {early_signal_decision['score']}/{early_signal_decision['min_score']}",
                f"velocity {early_signal_decision['velocity_pct']:+.2f}% · acceleration {early_signal_decision['acceleration_pct']:+.2f}%",
                f"base extension {early_signal_decision['extension_pct']:+.2f}%",
                f"trigger distance {trigger_distance_pct:+.2f}%" if trigger_distance_pct is not None else "trigger distance unavailable",
            ])
        elif early_release_qualifies:
            f.evidence.extend([
                "early release: clean structure + participation before full impulse confirmation",
                f"fresh velocity {early_release_decision['fresh_velocity_pct']:+.2f}% while base extension is {base_extension:+.2f}%",
                f"trigger distance {trigger_distance_pct:+.2f}%" if trigger_distance_pct is not None else "trigger distance unavailable",
            ])
        if first_leg_qualifies:
            f.evidence.extend([
                f"{f.leg_context.lower().replace('_', ' ')} confirmed",
                f"base range {m['base_range_pct']:.2f}% · {m['higher_low_ratio']:.0%} higher lows",
                f"pressing ${m['micro_resistance']:.4f} micro resistance",
                f"fresh participation ${m['dollar15']:,.0f} / {m['trades15']} trades",
            ])
        if reclaim_qualifies:
            f.evidence.extend([
                f"fresh reversal after -{m['reversal_drawdown_pct']:.2f}% selloff",
                f"reclaimed structure +{m['reversal_bounce_pct']:.2f}% above ${m['reversal_low']:.4f} low",
                ("VWAP reclaimed with fresh participation" if m["above_vwap"] else "EMA9/EMA21 reclaimed; VWAP remains overhead"),
            ])
        elif reversal_rearm_qualifies:
            f.evidence.extend([
                f"first pullback held above reclaimed structure at ${s.reversal_pullback_low:.4f}",
                "demand re-accelerating after controlled pullback",
            ])
        elif rearm_qualifies:
            f.evidence.extend([
                f"documented pullback held at ${s.continuation_pullback_low:.4f}",
                f"continuation resumed after {int(ts - s.last_alert_at)}s",
            ])
        if catalyst:
            f.catalyst_headline, f.catalyst_category, f.catalyst_score, f.catalyst_url, _ = catalyst
            f.candidate_profile["catalyst"] = min(100, int((f.catalyst_score or 0) * 20))
            if first_leg_qualifies:
                f.leg_context = "CATALYST_RELEASE"

        s.last_stage_rank = max(s.last_stage_rank, rank)
        s.last_alert_at = ts
        s.last_stage_alert_at[stage] = ts
        if breakout_qualifies and m["breakout_level"]:
            s.last_breakout_level = float(m["breakout_level"])
        if stage in {"BREAKOUT", "IGNITION"}:
            s.continuation_peak = m["price"]
            s.continuation_pullback_low = None
            s.continuation_started_at = ts
        if reclaim_qualifies:
            s.reversal_phase = "RECLAIM"
            s.reversal_low = m["reversal_low"]
            s.reversal_peak = m["price"]
            s.reversal_pullback_low = None
            s.reversal_started_at = ts
            s.last_reversal_episode_at = ts
        elif reversal_rearm_qualifies:
            s.reversal_phase = "REARM"
        if rearm_qualifies:
            s.continuation_peak = m["price"]
            s.continuation_pullback_low = None
            s.continuation_started_at = ts

        self._decorate_hybrid(f, "python")
        snap = self.snapshot(s.symbol)
        buckets, current = snap if snap else ([], None)
        await self.dispatcher.emit(f, buckets, current)

        log.info(
            "%s %s $%.4f score=%d signals=%s 3s=%+.2f%% 5s=%+.2f%% 15s=%+.2f%% 30s=%+.2f%% "
            "vol15=%.1fx breakout=%s stair=%s ext=%+.2f%% $30s=%.0f trades30=%d",
            stage, s.symbol, f.price, f.score, ",".join(signals), m["change3"], m["change5"], m["change15"],
            m["change30"], m["vol15"], m["breakout_window"], m["staircase"], f.extension_pct,
            m["dollar30"], m["trades30"],
        )

    async def _handle_trade(self, msg: dict, subscribed: set[str], feed: str = "sip"):
        symbol = str(msg.get("S", "")).upper()
        if not symbol:
            return
        if "*" not in subscribed and symbol not in subscribed:
            return
        try:
            price = float(msg.get("p", 0)); size = float(msg.get("s", 0))
        except Exception:
            return
        if price <= 0:
            return
        ingest_now = time.time()
        self.last_market_event_at = ingest_now
        self.last_market_event_by_feed["boats" if str(feed).lower() == "boats" else "sip"] = ingest_now
        # Keep guard-band symbols warm even while just outside the alert price range.
        # _maybe_emit() enforces MIN_PRICE/MAX_PRICE when deciding whether to alert.
        raw_ts = msg.get("t")
        try:
            ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).timestamp()
        except Exception:
            ts = time.time()
        session_date = trading_session_key(ts)
        s = self.states.get(symbol)
        if s is None:
            s = self.states[symbol] = SymbolState(symbol, settings.bucket_seconds, settings.keep_buckets)
            await self._restore_state_from_store(s, ts)
        s.update_trade(ts, price, size, session_date)
        normalized_feed = "boats" if str(feed).lower() == "boats" else "sip"
        s.last_market_feed = normalized_feed
        s.last_market_trade_at = ts
        if normalized_feed == "boats":
            # A symbol is placed in the 24H panel only after Scout has actually
            # observed a BOATS print for it in this U.S. trading session.
            s.last_boats_trade_at = ts
            s.boats_session_date = session_date
        self._update_outcomes(symbol, ts, price)
        if self.rust_bridge:
            self.rust_bridge.submit_trade(symbol=symbol, ts=ts, price=price, size=size, feed=feed)

        now_ms = ts * 1000
        if now_ms - s.last_fast_eval_at * 1000 >= settings.fast_path_min_interval_ms:
            s.last_fast_eval_at = ts
            m = self._metrics(s, ts)
            if m:
                await self._maybe_emit(s, m, ts, fast=True)
        if ts - s.last_eval_at >= settings.eval_seconds:
            s.last_eval_at = ts
            m = self._metrics(s, ts)
            if m:
                await self._maybe_emit(s, m, ts, fast=False)

    async def _stream(self, uri: str, label: str, overnight: bool = False):
        if not settings.alpaca_key or not settings.alpaca_secret:
            raise RuntimeError("ALPACA_API_KEY/ALPACA_API_SECRET are required")
        await self._universe_ready.wait()
        backoff = 2
        while True:
            try:
                async with websockets.connect(uri, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=None, max_queue=4096) as ws:
                    subscribed = self.overnight_subscribed if overnight else self.subscribed
                    if overnight:
                        self.overnight_ws = ws
                    else:
                        self.ws = ws
                    subscribed.clear()
                    health_key = "boats" if overnight else "sip"
                    health = self.feed_health[health_key]
                    health["connected"] = True
                    health["connections"] = int(health["connections"] or 0) + 1
                    health["last_connected_at"] = int(time.time())
                    health["last_error"] = None
                    await ws.send(json.dumps({"action": "auth", "key": settings.alpaca_key, "secret": settings.alpaca_secret}))
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                    log.info("Alpaca %s auth: %s", label, str(raw)[:250])
                    await self._reconcile(ws, subscribed, label)
                    if not overnight:
                        await ws.send(json.dumps({"action": "subscribe", "statuses": ["*"]}))
                    backoff = 2
                    async for raw in ws:
                        try:
                            messages = orjson.loads(raw)
                        except Exception:
                            continue
                        if isinstance(messages, dict):
                            messages = [messages]
                        for msg in messages:
                            if not isinstance(msg, dict):
                                continue
                            if msg.get("T") == "t":
                                await self._handle_trade(msg, subscribed, settings.alpaca_overnight_feed if overnight else settings.alpaca_feed)
                            elif msg.get("T") == "s" and not overnight:
                                await self._handle_status(msg)
                    raise ConnectionError(f"Alpaca {label} stream closed")
            except asyncio.CancelledError:
                health_key = "boats" if overnight else "sip"
                self.feed_health[health_key]["connected"] = False
                raise
            except Exception as exc:
                health_key = "boats" if overnight else "sip"
                health = self.feed_health[health_key]
                health["connected"] = False
                health["disconnects"] = int(health["disconnects"] or 0) + 1
                health["last_disconnected_at"] = int(time.time())
                health["last_error"] = str(exc)
                if overnight:
                    self.overnight_ws = None
                    self.overnight_subscribed.clear()
                else:
                    self.ws = None
                    self.subscribed.clear()
                log.exception("Alpaca %s stream disconnected; retry in %ss", label, backoff)
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)

    async def stream_loop(self):
        await self._stream(settings.alpaca_market_ws, "SIP", overnight=False)

    async def overnight_stream_loop(self):
        await self._stream(settings.alpaca_overnight_ws, settings.alpaca_overnight_feed.upper(), overnight=True)

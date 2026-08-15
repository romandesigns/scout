from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque
from typing import Deque


@dataclass
class Bucket:
    start_ts: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    trades: int = 0

    def update(self, price: float, size: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += max(0.0, size)
        self.trades += 1


@dataclass
class Finding:
    ticker: str
    stage: str
    detected_at: float
    price: float
    score: int
    vol_ratio_15s: float
    vol_ratio_30s: float
    change_60s_pct: float
    extension_pct: float
    ema9: float | None
    ema21: float | None
    ema9_slope: float | None
    vwap: float | None
    above_vwap: bool
    quiet_break: bool
    evidence: list[str]
    catalyst_headline: str | None = None
    catalyst_category: str | None = None
    catalyst_score: int | None = None
    catalyst_url: str | None = None
    chart_path: str | None = None

    # V5 event-fusion metrics. Optional to preserve compatibility with older
    # replay fixtures and persisted findings.
    change_3s_pct: float | None = None
    change_5s_pct: float | None = None
    change_10s_pct: float | None = None
    change_15s_pct: float | None = None
    change_30s_pct: float | None = None
    accel_15s_pp: float | None = None
    dollar_volume_15s: float | None = None
    dollar_volume_30s: float | None = None
    trades_15s: int | None = None
    trades_30s: int | None = None
    breakout_level: float | None = None
    breakout_window: str | None = None
    signals: list[str] = field(default_factory=list)
    finding_id: int | None = None
    quality_label: str = "DEVELOPING"
    quality_score: int = 0
    actionable_rank: str = "C"
    rejection_reasons: list[str] = field(default_factory=list)
    directional_efficiency: float | None = None
    active_bucket_ratio: float | None = None
    direction_reversals: int | None = None
    previous_close: float | None = None
    gap_pct: float | None = None
    day_volume: float | None = None
    projected_session_volume: float | None = None
    volume_rate_per_minute: float | None = None
    float_shares: float | None = None
    float_turnover: float | None = None
    candidate_profile: dict[str, int | None] = field(default_factory=dict)
    episode_id: int = 0
    reversal_phase: str | None = None
    reversal_low: float | None = None
    reversal_drawdown_pct: float | None = None
    leg_context: str | None = None
    ross_match: bool = False
    ross_score: int = 0
    detection_timeframe_seconds: int = 15
    formation_start_at: float | None = None
    formation_end_at: float | None = None
    formation_low: float | None = None
    formation_high: float | None = None
    trigger_level: float | None = None
    invalidation_level: float | None = None
    halt_pressure_score: int = 0
    urgency: str = "WATCH"
    engine_version: str | None = None


@dataclass
class SymbolState:
    symbol: str
    bucket_seconds: int
    keep_buckets: int
    buckets: Deque[Bucket] = field(default_factory=deque)
    current: Bucket | None = None
    price_points: Deque[tuple[float, float]] = field(default_factory=deque)
    session_pv: float = 0.0
    session_volume: float = 0.0
    session_date: str = ""
    session_first_price: float | None = None
    last_eval_at: float = 0.0
    last_fast_eval_at: float = 0.0
    last_alert_at: float = 0.0
    last_stage_rank: int = 0
    last_stage_alert_at: dict[str, float] = field(default_factory=dict)
    last_breakout_level: float | None = None
    last_watch_at: float = 0.0
    episode_id: int = 0
    reversal_phase: str = "IDLE"
    reversal_low: float | None = None
    reversal_peak: float | None = None
    reversal_pullback_low: float | None = None
    reversal_started_at: float = 0.0
    last_reversal_episode_at: float = 0.0
    continuation_peak: float | None = None
    continuation_pullback_low: float | None = None
    continuation_started_at: float = 0.0
    first_leg_candidate_at: float = 0.0
    first_leg_context: str | None = None

    def _roll(self, ts: float, price: float) -> None:
        bucket_start = ts - (ts % self.bucket_seconds)
        if self.current is None:
            self.current = Bucket(bucket_start, price, price, price, price)
            return
        if bucket_start <= self.current.start_ts:
            return
        self.buckets.append(self.current)
        while len(self.buckets) > self.keep_buckets:
            self.buckets.popleft()
        # Preserve empty intervals as zero-volume buckets so the baseline sees dormancy.
        next_start = self.current.start_ts + self.bucket_seconds
        prev_close = self.current.close
        while next_start < bucket_start:
            self.buckets.append(Bucket(next_start, prev_close, prev_close, prev_close, prev_close, 0.0, 0))
            while len(self.buckets) > self.keep_buckets:
                self.buckets.popleft()
            next_start += self.bucket_seconds
        self.current = Bucket(bucket_start, price, price, price, price)

    def update_trade(self, ts: float, price: float, size: float, session_date: str) -> None:
        # A new U.S. equity trading day begins with the 8 PM ET overnight session.
        if self.session_date and self.session_date != session_date:
            self.buckets.clear()
            self.current = None
            self.price_points.clear()
            self.session_pv = 0.0
            self.session_volume = 0.0
            self.session_first_price = None
            self.last_eval_at = 0.0
            self.last_fast_eval_at = 0.0
            self.last_alert_at = 0.0
            self.last_stage_rank = 0
            self.last_stage_alert_at.clear()
            self.last_breakout_level = None
            self.last_watch_at = 0.0
            self.episode_id = 0
            self.reversal_phase = "IDLE"
            self.reversal_low = None
            self.reversal_peak = None
            self.reversal_pullback_low = None
            self.reversal_started_at = 0.0
            self.last_reversal_episode_at = 0.0
            self.continuation_peak = None
            self.continuation_pullback_low = None
            self.continuation_started_at = 0.0
            self.first_leg_candidate_at = 0.0
            self.first_leg_context = None
        self.session_date = session_date
        if self.session_first_price is None:
            self.session_first_price = price

        self._roll(ts, price)
        if self.current is None:
            return
        self.current.update(price, size)
        self.price_points.append((ts, price))
        cutoff = ts - 180
        while self.price_points and self.price_points[0][0] < cutoff:
            self.price_points.popleft()
        if size > 0:
            self.session_pv += price * size
            self.session_volume += size

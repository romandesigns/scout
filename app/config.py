from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _i(name: str, default: int, minimum: int | None = None) -> int:
    v = int(os.getenv(name, str(default)))
    return max(minimum, v) if minimum is not None else v


def _f(name: str, default: float, minimum: float | None = None) -> float:
    v = float(os.getenv(name, str(default)))
    return max(minimum, v) if minimum is not None else v


def _repository_version() -> str:
    """Use the release manifest unless deployment explicitly overrides it."""
    for candidate in (Path(__file__).resolve().parents[1] / "VERSION", Path("/srv/VERSION")):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return "dev"


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "StockHunter Scout")
    app_version: str = os.getenv("APP_VERSION", "").strip() or _repository_version()
    env: str = os.getenv("APP_ENV", "production")
    # Test-only trigger for /api/development/simulate-finding; off unless explicitly enabled.
    enable_finding_simulation: bool = _b("ENABLE_FINDING_SIMULATION", False)
    timezone: str = os.getenv("APP_TIMEZONE", "America/New_York")
    imminent_gate_model_path: Path | None = (
        Path(os.environ["IMMINENT_GATE_MODEL_PATH"])
        if os.getenv("IMMINENT_GATE_MODEL_PATH", "").strip() else None
    )

    data_dir: Path = Path(os.getenv("DATA_DIR", "/data"))
    chart_dir: Path = Path(os.getenv("CHART_DIR", "/charts"))
    web_out_dir: Path = Path(os.getenv("WEB_OUT_DIR", "/srv/web-out"))
    allowed_origins: tuple[str, ...] = tuple(x.strip() for x in os.getenv("SCOUT_ALLOWED_ORIGINS", "http://tauri.localhost,tauri://localhost,http://localhost:3000,http://127.0.0.1:3000").split(",") if x.strip())

    alpaca_key: str = os.getenv("ALPACA_API_KEY", "").strip()
    alpaca_secret: str = os.getenv("ALPACA_API_SECRET", "").strip()
    alpaca_feed: str = os.getenv("ALPACA_FEED", "sip").strip().lower()
    alpaca_market_ws: str = os.getenv("ALPACA_MARKET_WS", "wss://stream.data.alpaca.markets/v2/sip")
    enable_overnight_stream: bool = _b("ENABLE_OVERNIGHT_STREAM", True)
    twenty_four_hour_panel_enabled: bool = _b("TWENTY_FOUR_HOUR_PANEL_ENABLED", True)
    twenty_four_hour_recent_seconds: int = _i("TWENTY_FOUR_HOUR_RECENT_SECONDS", 86400, 900)
    alpaca_overnight_feed: str = os.getenv("ALPACA_OVERNIGHT_FEED", "boats").strip().lower()
    alpaca_overnight_ws: str = os.getenv("ALPACA_OVERNIGHT_WS", "wss://stream.data.alpaca.markets/v1beta1/boats")
    alpaca_news_ws: str = os.getenv("ALPACA_NEWS_WS", "wss://stream.data.alpaca.markets/v1beta1/news")
    alpaca_data_base: str = os.getenv("ALPACA_DATA_BASE", "https://data.alpaca.markets")
    alpaca_trading_base: str = os.getenv("ALPACA_TRADING_BASE", "https://paper-api.alpaca.markets")

    # Universe. Default is broad low-priced US equities. Wildcard mode is available
    # but explicit dynamic subscription is safer for throughput on a Python daemon.
    min_price: float = _f("MIN_PRICE", 0.15, 0.01)
    max_price: float = _f("MAX_PRICE", 10.00, 0.02)
    universe_refresh_seconds: int = _i("UNIVERSE_REFRESH_SECONDS", 60, 30)
    universe_min_price: float = _f("UNIVERSE_MIN_PRICE", 0.15, 0.01)
    universe_max_price: float = _f("UNIVERSE_MAX_PRICE", 10.00, 0.02)
    universe_batch_size: int = _i("UNIVERSE_BATCH_SIZE", 200, 20)
    universe_max_symbols: int = _i("UNIVERSE_MAX_SYMBOLS", 12000, 100)
    wildcard_market_stream: bool = _b("WILDCARD_MARKET_STREAM", False)

    # Wake-up logic.
    bucket_seconds: int = _i("BUCKET_SECONDS", 15, 5)
    eval_seconds: int = _i("WAKEUP_EVAL_SECONDS", 15, 5)
    baseline_buckets: int = _i("BASELINE_BUCKETS", 12, 6)
    warmup_buckets: int = _i("WARMUP_BUCKETS", 8, 4)
    vol_ratio_trigger: float = _f("WAKEUP_VOL_RATIO", 4.0, 1.5)
    fast_vol_ratio_trigger: float = _f("FAST_VOL_RATIO", 5.0, 2.0)
    price_60s_trigger_pct: float = _f("WAKEUP_PRICE_60S_PCT", 2.0, 0.25)
    fast_price_60s_pct: float = _f("FAST_PRICE_60S_PCT", 1.5, 0.25)
    early_score: int = _i("EARLY_SCORE", 6, 3)
    ignition_score: int = _i("IGNITION_SCORE", 9, 4)
    max_early_extension_pct: float = _f("MAX_EARLY_EXTENSION_PCT", 15.0, 2.0)
    alert_cooldown_seconds: int = _i("ALERT_COOLDOWN_SECONDS", 180, 30)
    keep_buckets: int = _i("KEEP_BUCKETS", 160, 40)
    fast_path_min_interval_ms: int = _i("FAST_PATH_MIN_INTERVAL_MS", 750, 100)

    # Wake-up quality gates. Relative-volume ratios alone must never promote
    # a quiet symbol into a bullish alert.
    baseline_volume_floor: float = _f("BASELINE_VOLUME_FLOOR", 50.0, 1.0)
    early_min_change_60s_pct: float = _f("EARLY_MIN_CHANGE_60S_PCT", 0.50, 0.05)
    early_min_extension_pct: float = _f("EARLY_MIN_EXTENSION_PCT", 0.50, 0.0)
    ignition_min_change_60s_pct: float = _f("IGNITION_MIN_CHANGE_60S_PCT", 1.50, 0.25)
    ignition_min_extension_pct: float = _f("IGNITION_MIN_EXTENSION_PCT", 1.25, 0.25)

    ema_gap_tolerance_pct: float = _f("EMA_GAP_TOLERANCE_PCT", 0.05, 0.0)
    ema_slope_tolerance_pct: float = _f("EMA_SLOPE_TOLERANCE_PCT", 0.01, 0.0)
    vwap_tolerance_pct: float = _f("VWAP_TOLERANCE_PCT", 0.20, 0.0)

    min_30s_dollar_volume: float = _f("MIN_30S_DOLLAR_VOLUME", 1000.0, 0.0)
    ignition_min_30s_dollar_volume: float = _f("IGNITION_MIN_30S_DOLLAR_VOLUME", 2500.0, 0.0)
    min_30s_trades: int = _i("MIN_30S_TRADES", 3, 1)
    ignition_min_30s_trades: int = _i("IGNITION_MIN_30S_TRADES", 5, 1)
    require_two_active_buckets: bool = _b("REQUIRE_TWO_ACTIVE_BUCKETS", True)

    # V5.3 market-quality gate. Scout still records developing activity, but
    # only orderly, sufficiently participated bullish structure can escalate
    # into an actionable stage or notification.
    quality_profile: str = os.getenv("MARKET_QUALITY_PROFILE", "balanced").strip().lower()
    quality_window_buckets: int = _i("QUALITY_WINDOW_BUCKETS", 8, 4)
    quality_min_active_ratio: float = _f("QUALITY_MIN_ACTIVE_RATIO", 0.625, 0.25)
    quality_min_trades_30s: int = _i("QUALITY_MIN_TRADES_30S", 12, 2)
    quality_min_dollar_30s: float = _f("QUALITY_MIN_DOLLAR_30S", 5000.0, 0.0)
    quality_min_directional_efficiency: float = _f("QUALITY_MIN_DIRECTIONAL_EFFICIENCY", 0.30, 0.0)
    quality_max_direction_reversals: int = _i("QUALITY_MAX_DIRECTION_REVERSALS", 4, 1)
    quality_max_gap_pct: float = _f("QUALITY_MAX_GAP_PCT", 2.50, 0.10)
    quality_max_wick_ratio: float = _f("QUALITY_MAX_WICK_RATIO", 5.0, 1.0)
    quality_max_stale_seconds: int = _i("QUALITY_MAX_STALE_SECONDS", 30, 15)
    quality_impulse_min_trades_15s: int = _i("QUALITY_IMPULSE_MIN_TRADES_15S", 10, 2)
    quality_impulse_min_dollar_15s: float = _f("QUALITY_IMPULSE_MIN_DOLLAR_15S", 5000.0, 0.0)

    # 2026-08-19 experiments (all default OFF -- production behavior unchanged unless
    # explicitly enabled). See MILESTONES/2026-08-19-* for the backtest results each one
    # was validated against before any recommendation was made.
    experiment_adaptive_participation_bar: bool = _b("EXPERIMENT_ADAPTIVE_PARTICIPATION_BAR", False)
    experiment_adaptive_bar_max_reduction_pct: float = _f("EXPERIMENT_ADAPTIVE_BAR_MAX_REDUCTION_PCT", 0.50, 0.0)
    # Adopted after historical replay was the only participation variant to improve both
    # mean value per finding and total captured value. The environment switch remains a
    # rollback lever, while the validated behavior is now the production default.
    experiment_time_decay_participation_bar: bool = _b("EXPERIMENT_TIME_DECAY_PARTICIPATION_BAR", True)
    experiment_time_decay_window_seconds: float = _f("EXPERIMENT_TIME_DECAY_WINDOW_SECONDS", 60.0, 10.0)
    experiment_time_decay_max_reduction_pct: float = _f("EXPERIMENT_TIME_DECAY_MAX_REDUCTION_PCT", 0.50, 0.0)
    experiment_unified_participation_gate: bool = _b("EXPERIMENT_UNIFIED_PARTICIPATION_GATE", False)
    experiment_rust_fast_confirm: bool = _b("EXPERIMENT_RUST_FAST_CONFIRM", False)
    # #5, 2026-08-19: session-relative participation bar. The fixed QUALITY_MIN_TRADES_30S/
    # QUALITY_MIN_DOLLAR_30S bar is session-blind -- premarket, regular, and after-hours have
    # very different baseline liquidity (confirmed from real historical data, see
    # scripts/build_participation_baseline.py). These are the p65 dollar30/trades30
    # percentiles per session, computed from that real data, not guessed.
    experiment_session_relative_participation_bar: bool = _b("EXPERIMENT_SESSION_RELATIVE_PARTICIPATION_BAR", False)
    # #6, 2026-08-19 follow-up to #5: #5 used the p60 percentile (close to, sometimes below,
    # the existing fixed bar -- a mild adjustment). This tests a materially stricter
    # operating point instead: gate on whether a candidate's participation is genuinely
    # ABNORMAL for its session (top 15-20%), not merely "at or above par". Percentile table
    # is the same real historical data (scripts/build_participation_baseline.py), just read
    # at a higher percentile via experiment_market_relative_percentile.
    experiment_market_relative_participation_gate: bool = _b("EXPERIMENT_MARKET_RELATIVE_PARTICIPATION", False)
    experiment_market_relative_percentile: int = _i("EXPERIMENT_MARKET_RELATIVE_PERCENTILE", 80, 50)
    quality_watch_cooldown_seconds: int = _i("QUALITY_WATCH_COOLDOWN_SECONDS", 120, 30)

    # V5.6 first-leg initiation. Watch candidates remain silent; a confirmed
    # release receives a short, high-priority notification path.
    first_leg_base_buckets: int = _i("FIRST_LEG_BASE_BUCKETS", 12, 6)
    first_leg_max_base_range_pct: float = _f("FIRST_LEG_MAX_BASE_RANGE_PCT", 1.50, 0.25)
    first_leg_max_extension_pct: float = _f("FIRST_LEG_MAX_EXTENSION_PCT", 2.0, 0.25)
    first_leg_min_vol_ratio: float = _f("FIRST_LEG_MIN_VOL_RATIO", 3.0, 1.0)
    first_leg_min_change_3s_pct: float = _f("FIRST_LEG_MIN_CHANGE_3S_PCT", 0.18, 0.02)
    first_leg_min_change_5s_pct: float = _f("FIRST_LEG_MIN_CHANGE_5S_PCT", 0.28, 0.05)
    first_leg_min_change_15s_pct: float = _f("FIRST_LEG_MIN_CHANGE_15S_PCT", 0.45, 0.05)
    first_leg_min_dollar_15s: float = _f("FIRST_LEG_MIN_DOLLAR_15S", 2500.0, 0.0)
    first_leg_min_trades_15s: int = _i("FIRST_LEG_MIN_TRADES_15S", 8, 2)
    first_leg_confirmation_seconds: float = _f("FIRST_LEG_CONFIRMATION_SECONDS", 3.0, 1.0)
    first_leg_cooldown_seconds: int = _i("FIRST_LEG_COOLDOWN_SECONDS", 180, 30)
    first_leg_notification_consolidation_seconds: float = _f("FIRST_LEG_NOTIFICATION_CONSOLIDATION_SECONDS", 3.0, 0.0)

    # V6.6.1 evidence-driven early release. This path does not relax market
    # quality: it only allows a CLEAN, bullish, structurally valid first-leg
    # candidate to notify before the legacy full impulse/release gate catches up.
    early_release_enabled: bool = _b("EARLY_RELEASE_ENABLED", True)
    early_release_min_quality_score: int = _i("EARLY_RELEASE_MIN_QUALITY_SCORE", 82, 70)
    early_release_min_fresh_velocity_pct: float = _f("EARLY_RELEASE_MIN_FRESH_VELOCITY_PCT", 0.12, 0.02)
    early_release_max_base_extension_pct: float = _f("EARLY_RELEASE_MAX_BASE_EXTENSION_PCT", 0.75, 0.10)
    early_release_min_trigger_distance_pct: float = _f("EARLY_RELEASE_MIN_TRIGGER_DISTANCE_PCT", -0.35, -2.0)
    early_release_max_trigger_distance_pct: float = _f("EARLY_RELEASE_MAX_TRIGGER_DISTANCE_PCT", 0.50, 0.0)
    early_release_max_candidate_age_seconds: float = _f("EARLY_RELEASE_MAX_CANDIDATE_AGE_SECONDS", 15.0, 1.0)

    # V6.6.2 optimized evidence-score notification path.
    early_signal_enabled: bool = _b("EARLY_SIGNAL_ENABLED", True)
    early_signal_min_evidence_score: int = _i("EARLY_SIGNAL_MIN_EVIDENCE_SCORE", 7, 4)
    early_signal_min_quality_score: int = _i("EARLY_SIGNAL_MIN_QUALITY_SCORE", 76, 60)
    early_signal_min_velocity_pct: float = _f("EARLY_SIGNAL_MIN_VELOCITY_PCT", 0.08, 0.01)
    early_signal_min_accel_pct: float = _f("EARLY_SIGNAL_MIN_ACCEL_PCT", -0.03, -0.25)
    early_signal_max_extension_pct: float = _f("EARLY_SIGNAL_MAX_EXTENSION_PCT", 0.90, 0.10)
    early_signal_min_trigger_distance_pct: float = _f("EARLY_SIGNAL_MIN_TRIGGER_DISTANCE_PCT", -0.45, -2.0)
    early_signal_max_trigger_distance_pct: float = _f("EARLY_SIGNAL_MAX_TRIGGER_DISTANCE_PCT", 0.65, 0.0)
    early_signal_max_candidate_age_seconds: float = _f("EARLY_SIGNAL_MAX_CANDIDATE_AGE_SECONDS", 20.0, 1.0)
    early_signal_cooldown_seconds: float = _f("EARLY_SIGNAL_COOLDOWN_SECONDS", 45.0, 5.0)
    # V6.6.6 continuation-quality selectivity. These do not delay the legacy
    # confirmation paths; they only stop weak EARLY_SIGNAL fast-path releases.
    early_signal_continuation_gate_enabled: bool = _b("EARLY_SIGNAL_CONTINUATION_GATE_ENABLED", True)
    early_signal_continuation_min_velocity_pct: float = _f("EARLY_SIGNAL_CONTINUATION_MIN_VELOCITY_PCT", 0.18, 0.01)
    early_signal_continuation_min_accel_pct: float = _f("EARLY_SIGNAL_CONTINUATION_MIN_ACCEL_PCT", 0.05, 0.0)
    early_signal_pristine_min_velocity_pct: float = _f("EARLY_SIGNAL_PRISTINE_MIN_VELOCITY_PCT", 0.12, 0.01)
    early_actionable_require_rank_a: bool = _b("EARLY_ACTIONABLE_REQUIRE_RANK_A", True)

    # Multi-timescale price velocity.
    # 15s = primary wake-up sensor
    # 30s = confirmation
    # 60s = context only
    early_min_change_15s_pct: float = _f("EARLY_MIN_CHANGE_15S_PCT", 0.35, 0.05)
    early_min_change_30s_pct: float = _f("EARLY_MIN_CHANGE_30S_PCT", 0.65, 0.10)

    ignition_min_change_15s_pct: float = _f("IGNITION_MIN_CHANGE_15S_PCT", 0.85, 0.10)
    ignition_min_change_30s_pct: float = _f("IGNITION_MIN_CHANGE_30S_PCT", 1.25, 0.20)

    fast_price_15s_pct: float = _f("FAST_PRICE_15S_PCT", 0.60, 0.05)
    fast_price_30s_pct: float = _f("FAST_PRICE_30S_PCT", 1.00, 0.10)

    price_acceleration_min_pp: float = _f("PRICE_ACCELERATION_MIN_PP", 0.20, 0.01)

    # V4: sudden ignition + staircase wake-up.
    early_max_below_vwap_pct: float = _f("EARLY_MAX_BELOW_VWAP_PCT", 0.75, 0.0)

    fast_single_bucket_vol_ratio: float = _f("FAST_SINGLE_BUCKET_VOL_RATIO", 8.0, 2.0)
    fast_single_bucket_change_15s_pct: float = _f("FAST_SINGLE_BUCKET_CHANGE_15S_PCT", 0.60, 0.10)
    fast_single_bucket_dollar_volume: float = _f("FAST_SINGLE_BUCKET_DOLLAR_VOLUME", 2500.0, 0.0)
    fast_single_bucket_trades: int = _i("FAST_SINGLE_BUCKET_TRADES", 5, 1)

    staircase_window_buckets: int = _i("STAIRCASE_WINDOW_BUCKETS", 8, 4)
    staircase_min_active_buckets: int = _i("STAIRCASE_MIN_ACTIVE_BUCKETS", 5, 3)
    staircase_min_change_pct: float = _f("STAIRCASE_MIN_CHANGE_PCT", 1.00, 0.20)
    staircase_min_up_step_ratio: float = _f("STAIRCASE_MIN_UP_STEP_RATIO", 0.60, 0.40)
    staircase_min_higher_low_ratio: float = _f("STAIRCASE_MIN_HIGHER_LOW_RATIO", 0.60, 0.40)
    staircase_min_dollar_volume: float = _f("STAIRCASE_MIN_DOLLAR_VOLUME", 4000.0, 0.0)
    staircase_min_trades: int = _i("STAIRCASE_MIN_TRADES", 8, 2)

    # V5 immediate-surge engine. Uses trade-by-trade rolling price windows while
    # retaining participation gates so a single odd print cannot become an alert.
    surge_min_change_3s_pct: float = _f("SURGE_MIN_CHANGE_3S_PCT", 0.45, 0.05)
    surge_min_change_5s_pct: float = _f("SURGE_MIN_CHANGE_5S_PCT", 0.70, 0.10)
    surge_min_change_10s_pct: float = _f("SURGE_MIN_CHANGE_10S_PCT", 1.10, 0.15)
    surge_min_change_15s_pct: float = _f("SURGE_MIN_CHANGE_15S_PCT", 1.50, 0.20)
    surge_min_vol_ratio: float = _f("SURGE_MIN_VOL_RATIO", 5.0, 1.5)
    surge_min_dollar_15s: float = _f("SURGE_MIN_DOLLAR_15S", 2500.0, 0.0)
    surge_min_trades_15s: int = _i("SURGE_MIN_TRADES_15S", 5, 1)

    # V5 structural-breakout engine. 1m / 3m / 5m resistance levels are based
    # only on completed buckets, so the current candle cannot define its own level.
    breakout_min_penetration_pct: float = _f("BREAKOUT_MIN_PENETRATION_PCT", 0.15, 0.01)
    breakout_min_vol_ratio: float = _f("BREAKOUT_MIN_VOL_RATIO", 3.0, 1.0)
    breakout_min_dollar_30s: float = _f("BREAKOUT_MIN_DOLLAR_30S", 1500.0, 0.0)
    breakout_min_trades_30s: int = _i("BREAKOUT_MIN_TRADES_30S", 4, 1)
    breakout_min_fresh_velocity_pct: float = _f("BREAKOUT_MIN_FRESH_VELOCITY_PCT", 0.20, 0.05)
    # v6.6.9: BREAKOUT must still be accelerating on the immediate tape.
    breakout_continuation_gate_enabled: bool = _b("BREAKOUT_CONTINUATION_GATE_ENABLED", True)
    breakout_min_change_5s_pct: float = _f("BREAKOUT_MIN_CHANGE_5S_PCT", 0.10, 0.0)
    breakout_min_persistence_ratio: float = _f("BREAKOUT_MIN_PERSISTENCE_RATIO", 1.0, 0.0)
    # Re-entry/reclaim safety: block late/chasing or immediately fading re-entry alerts.
    reentry_safety_gate_enabled: bool = _b("REENTRY_SAFETY_GATE_ENABLED", True)
    reentry_min_change_5s_pct: float = _f("REENTRY_MIN_CHANGE_5S_PCT", 0.05, 0.0)
    # 2026-08-19: the existing reentry safety gate only checks extension from the *local*
    # base, which is blind to a ticker that's been fading for hours and formed a new tight
    # base far below both its session peak and VWAP (see MILESTONES/2026-08-19-008, the
    # BIVI case). The VWAP-distance blocker was subsequently validated across two live
    # sessions and adopted by default below.
    # Adopted after two independent live-session audits found negative aggregate forward
    # value in reentry signals far from VWAP. Keep the switch for emergency rollback.
    experiment_reentry_vwap_safety_gate: bool = _b("EXPERIMENT_REENTRY_VWAP_SAFETY_GATE", True)
    reentry_max_below_vwap_pct: float = _f("REENTRY_MAX_BELOW_VWAP_PCT", 2.0, 0.0)
    # Retroactive validation against a real regular-hours session showed the dominant real
    # loss pattern was the mirror case -- chasing a reentry already extended well above
    # VWAP, not fading below it. See app/market.py's evaluate_reentry_safety docstring.
    reentry_max_above_vwap_pct: float = _f("REENTRY_MAX_ABOVE_VWAP_PCT", 3.0, 0.0)
    ignition_min_fresh_velocity_pct: float = _f("IGNITION_MIN_FRESH_VELOCITY_PCT", 0.50, 0.10)
    # V6.6.8 late-stage continuation guard. This is intentionally scoped to
    # fresh IGNITION/HALT_PRESSURE promotions so EARLY/BREAKOUT timing remains
    # unchanged. It rejects stale 15s/30s impulse when the last 5s no longer
    # show positive continuation.
    late_stage_continuation_gate_enabled: bool = _b("LATE_STAGE_CONTINUATION_GATE_ENABLED", True)
    ignition_min_change_5s_pct: float = _f("IGNITION_MIN_CHANGE_5S_PCT", 0.10, 0.0)
    halt_pressure_min_change_5s_pct: float = _f("HALT_PRESSURE_MIN_CHANGE_5S_PCT", 0.25, 0.0)
    halt_pressure_min_change_15s_pct: float = _f("HALT_PRESSURE_MIN_CHANGE_15S_PCT", 0.75, 0.0)
    surge_weak_structure_min_dollar_15s: float = _f("SURGE_WEAK_STRUCTURE_MIN_DOLLAR_15S", 10000.0, 0.0)
    surge_weak_structure_min_trades_15s: int = _i("SURGE_WEAK_STRUCTURE_MIN_TRADES_15S", 20, 1)
    signal_stage_cooldown_seconds: int = _i("SIGNAL_STAGE_COOLDOWN_SECONDS", 60, 5)
    rearm_min_seconds: int = _i("REARM_MIN_SECONDS", 120, 30)
    rearm_min_level_improvement_pct: float = _f("REARM_MIN_LEVEL_IMPROVEMENT_PCT", 0.50, 0.05)

    # V5.5 reversal/reclaim episode engine. This pathway reopens a ticker only
    # after a material selloff forms a local low and demand reclaims structure.
    reversal_lookback_buckets: int = _i("REVERSAL_LOOKBACK_BUCKETS", 120, 24)
    reversal_low_window_buckets: int = _i("REVERSAL_LOW_WINDOW_BUCKETS", 40, 8)
    reversal_min_drawdown_pct: float = _f("REVERSAL_MIN_DRAWDOWN_PCT", 5.0, 2.0)
    reversal_watch_min_bounce_pct: float = _f("REVERSAL_WATCH_MIN_BOUNCE_PCT", 0.75, 0.25)
    reversal_reclaim_min_bounce_pct: float = _f("REVERSAL_RECLAIM_MIN_BOUNCE_PCT", 2.0, 0.50)
    reversal_min_vol_ratio: float = _f("REVERSAL_MIN_VOL_RATIO", 3.0, 1.5)
    reversal_min_dollar_30s: float = _f("REVERSAL_MIN_DOLLAR_30S", 5000.0, 0.0)
    reversal_min_trades_30s: int = _i("REVERSAL_MIN_TRADES_30S", 12, 2)
    reversal_min_dollar_15s: float = _f("REVERSAL_MIN_DOLLAR_15S", 2500.0, 0.0)
    reversal_min_trades_15s: int = _i("REVERSAL_MIN_TRADES_15S", 5, 1)
    reversal_min_vol_ratio_15s: float = _f("REVERSAL_MIN_VOL_RATIO_15S", 1.5, 1.0)
    reversal_max_low_age_seconds: int = _i("REVERSAL_MAX_LOW_AGE_SECONDS", 900, 60)
    reversal_episode_cooldown_seconds: int = _i("REVERSAL_EPISODE_COOLDOWN_SECONDS", 300, 60)
    reversal_pullback_min_pct: float = _f("REVERSAL_PULLBACK_MIN_PCT", 0.75, 0.10)
    reversal_pullback_max_pct: float = _f("REVERSAL_PULLBACK_MAX_PCT", 6.0, 1.0)
    reversal_rearm_min_bounce_pct: float = _f("REVERSAL_REARM_MIN_BOUNCE_PCT", 0.35, 0.10)

    # Dashboard / deep-link destination used by push notifications. This should
    # normally be a private Tailscale URL, not a public internet endpoint.
    scout_client_base_url: str = os.getenv("SCOUT_CLIENT_BASE_URL", "").strip().rstrip("/")

    # Catalyst logic.
    sec_poll_seconds: int = _i("SEC_POLL_SECONDS", 10, 10)
    min_bullish_score: int = _i("MIN_BULLISH_SCORE", 3, 1)
    sec_user_agent: str = os.getenv("SEC_USER_AGENT", "StockHunterScout/3.0 contact@example.com")
    rss_feeds: tuple[str, ...] = tuple(x.strip() for x in os.getenv("RSS_FEEDS", "").split(",") if x.strip())
    catalyst_watchlist: tuple[str, ...] = tuple(x.strip().upper() for x in os.getenv("CATALYST_WATCHLIST", "").split(",") if x.strip())
    catalyst_source_stale_seconds: int = _i("CATALYST_SOURCE_STALE_SECONDS", 300, 30)

    # Mobile push.
    ntfy_server: str = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    ntfy_topic: str = os.getenv("NTFY_TOPIC", "").strip()
    ntfy_chart_followup: bool = _b("NTFY_CHART_FOLLOWUP", False)
    vapid_public_key: str = os.getenv("VAPID_PUBLIC_KEY", "").strip()
    vapid_private_key: str = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    vapid_subject: str = os.getenv("VAPID_SUBJECT", "mailto:contact@example.com").strip()

    # Resend email.
    resend_api_key: str = os.getenv("RESEND_API_KEY", "").strip()
    resend_from: str = os.getenv("RESEND_FROM", "").strip()
    resend_to: tuple[str, ...] = tuple(x.strip() for x in os.getenv("RESEND_TO", "").split(",") if x.strip())
    email_every_finding: bool = _b("EMAIL_EVERY_FINDING", True)
    notification_queue_max: int = _i("NOTIFICATION_QUEUE_MAX", 250, 20)
    notification_consolidation_seconds: float = _f("NOTIFICATION_CONSOLIDATION_SECONDS", 8.0, 0.0)
    # Persist stale findings for audit, but do not turn an old queued market event
    # into a fresh device notification after its trading value has expired.
    notification_max_candidate_age_seconds: float = _f("NOTIFICATION_MAX_CANDIDATE_AGE_SECONDS", 15.0, 1.0)
    notification_special_max_candidate_age_seconds: float = _f("NOTIFICATION_SPECIAL_MAX_CANDIDATE_AGE_SECONDS", 45.0, 1.0)
    notification_preferences_cache_seconds: float = _f("NOTIFICATION_PREFERENCES_CACHE_SECONDS", 2.0, 0.1)
    webpush_subscription_cache_seconds: float = _f("WEBPUSH_SUBSCRIPTION_CACHE_SECONDS", 5.0, 0.1)
    dispatch_queue_max: int = _i("DISPATCH_QUEUE_MAX", 2000, 100)
    dispatch_worker_count: int = _i("DISPATCH_WORKER_COUNT", 4, 1)
    dispatch_low_priority_max_utilization: float = _f("DISPATCH_LOW_PRIORITY_MAX_UTILIZATION", 0.75, 0.1)
    ntfy_min_interval_seconds: float = _f("NTFY_MIN_INTERVAL_SECONDS", 2.0, 0.25)
    resend_min_interval_seconds: float = _f("RESEND_MIN_INTERVAL_SECONDS", 1.0, 0.25)
    notification_retry_attempts: int = _i("NOTIFICATION_RETRY_ATTEMPTS", 5, 1)
    notification_retry_base_seconds: float = _f("NOTIFICATION_RETRY_BASE_SECONDS", 2.0, 0.25)
    notification_retry_max_seconds: float = _f("NOTIFICATION_RETRY_MAX_SECONDS", 120.0, 1.0)
    ntfy_quota_cooldown_seconds: int = _i("NTFY_QUOTA_COOLDOWN_SECONDS", 21600, 300)

    # Runtime reliability. The watchdog is process-local so Docker restart policy
    # can recover an event-loop stall without privileged Docker socket access.
    event_loop_watchdog_seconds: int = _i("EVENT_LOOP_WATCHDOG_SECONDS", 45, 15)
    event_loop_watchdog_grace_seconds: int = _i("EVENT_LOOP_WATCHDOG_GRACE_SECONDS", 90, 15)
    api_cache_ttl_seconds: float = _f("API_CACHE_TTL_SECONDS", 1.5, 0.1)
    reconcile_send_timeout_seconds: float = _f("RECONCILE_SEND_TIMEOUT_SECONDS", 5.0, 1.0)

    # Hybrid Rust-primary + Python-specialist runtime. These settings affect
    # integration/routing only; they do not change the frozen v6.4.13 detector
    # thresholds or replay semantics.
    hybrid_enabled: bool = _b("HYBRID_ENABLED", True)
    rust_perception_binary: str = os.getenv("RUST_PERCEPTION_BINARY", "/usr/local/bin/scout-market-replay").strip()
    rust_bridge_queue_max: int = _i("RUST_BRIDGE_QUEUE_MAX", 50000, 1000)
    rust_bridge_batch_max: int = _i("RUST_BRIDGE_BATCH_MAX", 256, 1)
    rust_bridge_batch_bytes: int = _i("RUST_BRIDGE_BATCH_BYTES", 65536, 4096)
    rust_quote_min_interval_ms: int = _i("RUST_QUOTE_MIN_INTERVAL_MS", 1000, 100)
    hybrid_merge_window_seconds: float = _f("HYBRID_MERGE_WINDOW_SECONDS", 45.0, 1.0)
    hybrid_dedupe_seconds: float = _f("HYBRID_DEDUPE_SECONDS", 20.0, 0.0)
    hybrid_episode_gap_seconds: float = _f("HYBRID_EPISODE_GAP_SECONDS", 900.0, 60.0)
    hybrid_awakening_min_vol_ratio: float = _f("HYBRID_AWAKENING_MIN_VOL_RATIO", 1.5, 1.0)
    hybrid_awakening_min_change_15s_pct: float = _f("HYBRID_AWAKENING_MIN_CHANGE_15S_PCT", 0.15, 0.0)
    rust_shaping_up_notify_enabled: bool = _b("RUST_SHAPING_UP_NOTIFY_ENABLED", True)
    rust_shaping_up_min_confidence: int = _i("RUST_SHAPING_UP_MIN_CONFIDENCE", 65, 50)
    hybrid_precision_threshold_pct: float = _f("HYBRID_PRECISION_THRESHOLD_PCT", 5.0, 0.5)

    # Health / ops.
    health_port: int = _i("HEALTH_PORT", 8080, 1)
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.chart_dir.mkdir(parents=True, exist_ok=True)

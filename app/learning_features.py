"""Stable feature contract shared by Scout's trainer and live shadow scorer.

Only information present at detection time belongs here.  Keeping this module
shared prevents training/serving skew and makes Rust-originated findings use
the exact same deterministic transformations as Python findings.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


NUMERIC_FEATURES = (
    "score", "quality_score", "change_3s_pct", "change_5s_pct", "change_10s_pct",
    "change_15s_pct", "change_30s_pct", "change_60s_pct", "accel_15s_pp",
    "extension_pct", "directional_efficiency", "active_bucket_ratio", "direction_reversals",
    "ema9_slope", "vol_ratio_15s", "vol_ratio_30s", "dollar_volume_15s",
    "dollar_volume_30s", "trades_15s", "trades_30s",
)
STAGES = ("EARLY", "FIRST_LEG", "SURGE", "BREAKOUT", "IGNITION", "REARM", "RECLAIM", "VWAP_RECLAIM", "EMA_RECLAIM")
SOURCES = ("python", "python_native", "rust_triggered", "rust_primary", "hybrid")
DERIVED_FEATURES = (
    "log_price", "log_dollar_15s", "log_dollar_30s", "log_trades_15s", "log_trades_30s",
    "log_float_turnover", "vwap_distance_pct", "velocity_persistence", "participation_coupling",
    "compression_quality", "supply_family", "lifecycle_family", "box_quality",
    "pullback_quality", "front_side", "backside", "above_vwap", "quiet_break", "rank_a",
)
FEATURES = (
    *NUMERIC_FEATURES, *DERIVED_FEATURES,
    *(f"stage_{stage.lower()}" for stage in STAGES),
    *(f"source_{source}" for source in SOURCES),
)


def _get(value: Any, name: str, default: Any = 0.0) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _number(value: Any, name: str) -> float:
    try:
        return float(_get(value, name, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def feature_vector(value: Any) -> list[float]:
    """Return bounded, point-in-time features for a Finding or persisted row."""
    numbers = {name: _number(value, name) for name in NUMERIC_FEATURES}
    price = max(0.0, _number(value, "price"))
    vwap = max(0.0, _number(value, "vwap"))
    turnover = max(0.0, _number(value, "float_turnover"))
    profile = _get(value, "candidate_profile", {}) or {}
    if not isinstance(profile, Mapping):
        profile = {}

    velocities = [numbers[name] for name in ("change_5s_pct", "change_15s_pct", "change_30s_pct")]
    positive = sum(item > 0.0 for item in velocities) / len(velocities)
    ordered = sum(right >= left for left, right in zip(velocities, velocities[1:])) / 2.0
    velocity_persistence = (positive + ordered) / 2.0
    participation_coupling = math.tanh(
        max(0.0, numbers["change_15s_pct"]) * math.log1p(max(0.0, numbers["dollar_volume_15s"]))
        * math.log1p(max(0.0, numbers["trades_15s"])) / 100.0
    )
    compression_quality = float(profile.get("compression_quality") or 0.0)
    compression_quality = min(1.0, max(0.0, compression_quality / 100.0 if compression_quality > 1 else compression_quality))
    supply = min(1.0, max(0.0, float(profile.get("supply") or 0.0) / 100.0))
    lifecycle = min(1.0, max(0.0, float(profile.get("lifecycle") or 0.0) / 100.0))
    box = profile.get("box") if isinstance(profile.get("box"), Mapping) else {}
    pullback = profile.get("pullback") if isinstance(profile.get("pullback"), Mapping) else {}
    box_quality = min(1.0, max(0.0, float(box.get("quality") or 0.0) / 100.0))
    pullback_quality = min(1.0, max(0.0, float(pullback.get("quality") or 0.0) / 100.0))
    phase = str(profile.get("phase") or "").upper()
    vwap_distance = ((price - vwap) / vwap * 100.0) if price and vwap else 0.0

    result = [numbers[name] for name in NUMERIC_FEATURES]
    result.extend([
        math.log1p(price), math.log1p(max(0.0, numbers["dollar_volume_15s"])),
        math.log1p(max(0.0, numbers["dollar_volume_30s"])),
        math.log1p(max(0.0, numbers["trades_15s"])), math.log1p(max(0.0, numbers["trades_30s"])),
        math.log1p(turnover), max(-50.0, min(50.0, vwap_distance)), velocity_persistence,
        participation_coupling, compression_quality, supply, lifecycle, box_quality,
        pullback_quality, float(phase == "FRONT_SIDE"), float(phase == "BACKSIDE"),
        float(bool(_get(value, "above_vwap", False))),
        float(bool(_get(value, "quiet_break", False))),
        float(str(_get(value, "actionable_rank", "")).upper() == "A"),
    ])
    stage = str(_get(value, "stage", "")).upper()
    source = str(_get(value, "engine_source", _get(value, "source", "python"))).lower()
    result.extend(float(stage == name) for name in STAGES)
    result.extend(float(source == name) for name in SOURCES)
    return result

"""Advisory structural evidence for Scout's existing lifecycle.

These measurements intentionally do not emit findings or gate notifications.
They translate durable momentum observations into de-correlated evidence
families that can be evaluated in replay before any production promotion.
"""
from __future__ import annotations

import statistics
from typing import Iterable

from .models import Bucket


def _range_pct(row: Bucket) -> float:
    return max(0.0, (row.high - row.low) / max(row.low, 0.000001) * 100.0)


def unified_structure_profile(
    rows: Iterable[Bucket], *, price: float, vwap: float | None,
    continuation_peak: float | None = None,
) -> dict[str, object]:
    """Compute point-in-time supply, box, pullback, and lifecycle context."""
    data = list(rows)
    if not data:
        return {"supply": 0, "lifecycle": 0, "compression_quality": 0, "phase": "UNKNOWN"}

    recent = data[-12:]
    thirds = [recent[index:index + 4] for index in range(0, len(recent), 4)]
    ranges = [statistics.median(_range_pct(row) for row in group) for group in thirds if group]
    volumes = [statistics.median(row.volume for row in group) for group in thirds if group]
    range_tightening = sum(right <= left for left, right in zip(ranges, ranges[1:]))
    volume_drying = sum(right <= left for left, right in zip(volumes, volumes[1:]))
    pairs = list(zip(recent, recent[1:]))
    higher_low_ratio = sum(right.low >= left.low * 0.998 for left, right in pairs) / len(pairs) if pairs else 0.0
    recent_low = min(row.low for row in recent)
    recent_high = max(row.high for row in recent)
    location = (price - recent_low) / max(recent_high - recent_low, 0.000001)
    compression_quality = round(min(100.0, max(0.0,
        range_tightening * 20.0 + volume_drying * 15.0 + higher_low_ratio * 30.0
        + min(1.0, max(0.0, location)) * 20.0
    )))

    box_rows = data[-9:-1] if len(data) >= 9 else data[:-1]
    box_low = min((row.low for row in box_rows), default=recent_low)
    box_high = max((row.high for row in box_rows), default=recent_high)
    box_width_pct = (box_high - box_low) / max(box_low, 0.000001) * 100.0
    box_breakout = price > box_high
    box_holding = price >= box_low and (vwap is None or price >= vwap * 0.995)
    box_quality = round(min(100.0, higher_low_ratio * 35.0 + (30.0 if box_holding else 0.0)
                            + (20.0 if box_width_pct <= 5.0 else 5.0) + (15.0 if box_breakout else 0.0)))

    session_high = max(row.high for row in data)
    drawdown_pct = max(0.0, (session_high - price) / max(session_high, 0.000001) * 100.0)
    near_high_tests = sum(row.high >= session_high * 0.995 for row in data[-20:])
    lower_highs = sum(right.high < left.high * 0.998 for left, right in zip(data[-8:], data[-7:]))
    vwap_loss_buckets = 0
    if vwap is not None:
        for row in reversed(data):
            if row.close >= vwap:
                break
            vwap_loss_buckets += 1
    front_side = drawdown_pct <= 5.0 and lower_highs <= 3 and vwap_loss_buckets <= 2
    backside = drawdown_pct >= 10.0 or lower_highs >= 5 or vwap_loss_buckets >= 6
    phase = "BACKSIDE" if backside else "FRONT_SIDE" if front_side else "TRANSITION"
    lifecycle_score = round(max(0.0, min(100.0, 100.0 - drawdown_pct * 4.0
                                          - lower_highs * 7.0 - vwap_loss_buckets * 4.0)))

    pullback_depth = None
    pullback_quality = None
    if continuation_peak and continuation_peak > 0:
        pullback_depth = max(0.0, (continuation_peak - price) / continuation_peak * 100.0)
        current_volume = data[-1].volume
        prior_volume = statistics.median(row.volume for row in data[-6:-1]) if len(data) > 1 else current_volume
        volume_contracted = current_volume <= prior_volume
        pullback_quality = round(max(0.0, min(100.0,
            (35.0 if pullback_depth <= 6.0 else 10.0) + (30.0 if volume_contracted else 0.0)
            + (20.0 if box_holding else 0.0) + higher_low_ratio * 15.0
        )))

    # Family scores cap correlated descriptions instead of stacking bonuses.
    supply = round((compression_quality + box_quality) / 2.0)
    return {
        "supply": supply,
        "lifecycle": lifecycle_score,
        "compression_quality": compression_quality,
        "box": {"low": box_low, "high": box_high, "width_pct": round(box_width_pct, 3),
                "breakout": box_breakout, "holding": box_holding, "quality": box_quality},
        "lifecycle_context": {"phase": phase, "drawdown_from_high_pct": round(drawdown_pct, 3),
                              "near_high_tests": near_high_tests, "lower_highs": lower_highs,
                              "vwap_loss_buckets": vwap_loss_buckets},
        "pullback": {"depth_pct": round(pullback_depth, 3) if pullback_depth is not None else None,
                     "quality": pullback_quality},
        "phase": phase,
    }

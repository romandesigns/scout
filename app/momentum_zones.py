"""Deterministic, Scout-independent "ground truth" bullish momentum zones,
computed directly from OHLCV bars -- not from anything Scout's detector
found. Exists so a Scout Development chart can show both "here is where
price actually expanded" and "here is what Scout's detector flagged",
letting the user audit detector accuracy visually and numerically instead
of only ever seeing Scout's own (possibly incomplete) markers.

Reuses the same "objective move" shape as `app.replay.calibrate_pre_ignition`
(a rolling base-window low, a minimum expansion_pct, deduplicated into
episodes no closer together than a cooldown) so the definition of "real
momentum" stays consistent with the rest of the codebase's backtest
validation -- just applied to bar closes here instead of tick prices, since
a chart-level audit doesn't need tick resolution.
"""
from __future__ import annotations

from collections import deque
from typing import Any

DEFAULT_EXPANSION_PCT = 2.0
DEFAULT_BASE_WINDOW_SECONDS = 300
DEFAULT_HORIZON_SECONDS = 900
DEFAULT_DEDUPE_SECONDS = 600
DEFAULT_LEAD_SECONDS = 120.0


def find_momentum_zones(bars: list[dict[str, Any]], *, expansion_pct: float = DEFAULT_EXPANSION_PCT,
                        base_window_seconds: int = DEFAULT_BASE_WINDOW_SECONDS,
                        horizon_seconds: int = DEFAULT_HORIZON_SECONDS,
                        dedupe_seconds: int = DEFAULT_DEDUPE_SECONDS) -> list[dict[str, Any]]:
    """Identify real (price-only) bullish momentum zones from OHLCV bars.

    A zone begins the first bar whose close is `expansion_pct`% or more
    above the lowest close in the preceding `base_window_seconds`, at least
    `dedupe_seconds` after the previous zone's onset. It extends forward to
    the highest high reached within `horizon_seconds` of onset (or the end
    of the data, if sooner).
    """
    ordered = sorted(bars, key=lambda row: float(row["start_ts"]))
    rolling: deque[tuple[float, float]] = deque()
    zones: list[dict[str, Any]] = []
    last_onset = float("-inf")
    for row in ordered:
        ts = float(row["start_ts"])
        close = float(row["close"])
        rolling.append((ts, close))
        cutoff = ts - base_window_seconds
        while rolling and rolling[0][0] < cutoff:
            rolling.popleft()
        base = min((value for _, value in rolling), default=close)
        if base <= 0:
            continue
        if close >= base * (1 + expansion_pct / 100) and ts - last_onset >= dedupe_seconds:
            base_ts = next((t for t, value in rolling if value == base), ts)
            future = [r for r in ordered if ts <= float(r["start_ts"]) <= ts + horizon_seconds]
            peak_row = max(future, key=lambda r: float(r["high"])) if future else row
            peak_price = float(peak_row["high"])
            zones.append({
                "onset_at": ts, "base_at": base_ts, "base_price": round(base, 6),
                "onset_price": close, "peak_at": float(peak_row["start_ts"]),
                "peak_price": round(peak_price, 6),
                "expansion_pct": round((peak_price - base) / base * 100, 3),
            })
            last_onset = ts
    return zones


def match_detections_to_zones(zones: list[dict[str, Any]], qualifying_detections: list[dict[str, Any]],
                              lead_seconds: float = DEFAULT_LEAD_SECONDS) -> list[dict[str, Any]]:
    """For each zone, find the earliest qualifying Scout detection (the
    caller pre-filters to whatever counts as "Scout caught this" -- e.g.
    tier 1/2 or would_notify) inside [onset - lead_seconds, peak_at].
    Returns a copy of each zone with `caught`, `matched_finding_id`, and
    `lead_seconds` attached (positive = Scout was early, negative = late)."""
    annotated = []
    for zone in zones:
        window_start = zone["onset_at"] - lead_seconds
        window_end = zone["peak_at"]
        candidates = [
            item for item in qualifying_detections
            if window_start <= float(item.get("detected_at") or 0) <= window_end
        ]
        match = min(candidates, key=lambda item: float(item["detected_at"])) if candidates else None
        entry = dict(zone)
        entry["caught"] = match is not None
        entry["matched_finding_id"] = (match or {}).get("id")
        entry["lead_seconds"] = round(zone["onset_at"] - float(match["detected_at"]), 1) if match else None
        annotated.append(entry)
    return annotated

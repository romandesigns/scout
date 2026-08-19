"""Bullish candlestick pattern recognition — observational/shadow module.

Scout goal item (2026-08-18): "catch all meaningful bullish moves early... that includes
all possible candlestick pattern formations with certainty and accuracy."

This module is intentionally standalone and does not feed live detection gates, promotion
rules, or notifications. It follows the same discipline Scout already uses for new signal
ideas (see the "V6.3 shadow recipe" in app/market.py: new evidence stays silent/observational
until lead-time and false-arm rates are measured across representative sessions via the real
backtest pipeline). Wire this into detection only after that validation, and only as
additional evidence alongside existing gates -- never as a bypass of them.

Pattern definitions are the standard, well-established technical-analysis definitions, not
invented heuristics. Each function is pure and independently testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Candle:
    start_ts: float
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return max(1e-9, self.high - self.low)

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def midpoint(self) -> float:
        return (self.open + self.close) / 2.0


@dataclass(frozen=True)
class PatternMatch:
    name: str
    candles_used: int
    confidence: float  # 0-1, how cleanly the candle(s) satisfy the pattern's ideal shape
    evidence: dict


def resample(candles: Sequence[Candle], bucket_seconds: float) -> list[Candle]:
    """Aggregate finer candles (e.g. Scout's 15s buckets) into coarser ones for pattern
    evaluation. Classic candlestick patterns are defined for daily bars; on intraday data
    they need a coarser timeframe (1-5min) than raw 15s prints to mean anything, else noise
    dominates. Empty input buckets are skipped."""
    if not candles:
        return []
    out: list[Candle] = []
    bucket_start = candles[0].start_ts - (candles[0].start_ts % bucket_seconds)
    group: list[Candle] = []
    for c in candles:
        if c.start_ts >= bucket_start + bucket_seconds:
            if group:
                out.append(_merge(group, bucket_start))
            bucket_start = c.start_ts - (c.start_ts % bucket_seconds)
            group = []
        group.append(c)
    if group:
        out.append(_merge(group, bucket_start))
    return out


def _merge(group: list[Candle], start_ts: float) -> Candle:
    return Candle(
        start_ts=start_ts, open=group[0].open, close=group[-1].close,
        high=max(c.high for c in group), low=min(c.low for c in group),
        volume=sum(c.volume for c in group),
    )


# --- Single-candle patterns -------------------------------------------------

def _hammer_like(c: Candle, *, inverted: bool) -> PatternMatch | None:
    if c.body <= 0:
        return None
    long_wick = c.upper_wick if inverted else c.lower_wick
    short_wick = c.lower_wick if inverted else c.upper_wick
    if long_wick < 2.0 * c.body:
        return None
    # Compare the short wick against the candle's total range, not its body -- a body-relative
    # threshold breaks down for near-doji hammers where the body is tiny (any wick, however
    # small in absolute terms, would exceed a percentage of an even tinier body).
    if short_wick > 0.15 * c.range:
        return None
    if c.body > 0.35 * c.range:
        return None
    confidence = min(1.0, (long_wick / max(c.body, 1e-9)) / 4.0)
    name = "INVERTED_HAMMER" if inverted else "HAMMER"
    return PatternMatch(name, 1, round(confidence, 3), {
        "body": round(c.body, 6), "long_wick": round(long_wick, 6), "short_wick": round(short_wick, 6),
    })


def hammer(c: Candle) -> PatternMatch | None:
    """Small body near the top of the range, long lower wick, little upper wick.
    Bullish reversal signal after a downtrend -- caller supplies prior-trend context."""
    return _hammer_like(c, inverted=False)


def inverted_hammer(c: Candle) -> PatternMatch | None:
    """Small body near the bottom of the range, long upper wick, little lower wick."""
    return _hammer_like(c, inverted=True)


def dragonfly_doji(c: Candle, *, doji_body_ratio: float = 0.08) -> PatternMatch | None:
    """Open ~= close ~= high (negligible upper wick), long lower wick."""
    if c.body > doji_body_ratio * c.range:
        return None
    if c.upper_wick > 0.15 * c.range:
        return None
    if c.lower_wick < 0.55 * c.range:
        return None
    confidence = min(1.0, c.lower_wick / c.range)
    return PatternMatch("DRAGONFLY_DOJI", 1, round(confidence, 3), {
        "body": round(c.body, 6), "lower_wick": round(c.lower_wick, 6), "range": round(c.range, 6),
    })


# --- Two-candle patterns -----------------------------------------------------

def bullish_engulfing(prev: Candle, cur: Candle) -> PatternMatch | None:
    if not (prev.is_bearish and cur.is_bullish):
        return None
    if not (cur.open <= prev.close and cur.close >= prev.open):
        return None
    if cur.body <= prev.body:
        return None
    confidence = min(1.0, cur.body / max(prev.body, 1e-9) / 2.0)
    return PatternMatch("BULLISH_ENGULFING", 2, round(confidence, 3), {
        "prev_body": round(prev.body, 6), "cur_body": round(cur.body, 6),
    })


def bullish_harami(prev: Candle, cur: Candle) -> PatternMatch | None:
    if not (prev.is_bearish and cur.is_bullish):
        return None
    if not (cur.open >= prev.close and cur.close <= prev.open):
        return None
    if prev.body <= 0 or cur.body >= prev.body * 0.6:
        return None
    confidence = min(1.0, 1.0 - (cur.body / max(prev.body, 1e-9)))
    return PatternMatch("BULLISH_HARAMI", 2, round(confidence, 3), {
        "prev_body": round(prev.body, 6), "cur_body": round(cur.body, 6),
    })


def piercing_line(prev: Candle, cur: Candle) -> PatternMatch | None:
    if not (prev.is_bearish and cur.is_bullish):
        return None
    if cur.open >= prev.low:
        return None  # requires a gap down at the open
    if not (cur.close > prev.midpoint and cur.close < prev.open):
        return None
    penetration = (cur.close - prev.close) / max(prev.body, 1e-9)
    return PatternMatch("PIERCING_LINE", 2, round(min(1.0, penetration), 3), {
        "gap_down": round(prev.low - cur.open, 6), "penetration_pct_of_prev_body": round(penetration * 100, 2),
    })


def tweezer_bottom(prev: Candle, cur: Candle, *, tolerance_pct: float = 0.15) -> PatternMatch | None:
    if not (prev.is_bearish and cur.is_bullish):
        return None
    low_diff_pct = abs(prev.low - cur.low) / max(prev.low, 1e-9) * 100.0
    if low_diff_pct > tolerance_pct:
        return None
    confidence = max(0.0, 1.0 - low_diff_pct / tolerance_pct)
    return PatternMatch("TWEEZER_BOTTOM", 2, round(confidence, 3), {"low_diff_pct": round(low_diff_pct, 4)})


# --- Three-candle patterns ----------------------------------------------------

def morning_star(first: Candle, star: Candle, third: Candle) -> PatternMatch | None:
    if not first.is_bearish or first.body <= 0:
        return None
    if star.body > 0.4 * first.body:
        return None
    if max(star.open, star.close) >= first.close:
        return None  # star must gap down from the first candle's close
    if not third.is_bullish:
        return None
    if third.close <= first.midpoint:
        return None
    penetration = (third.close - first.close) / max(first.body, 1e-9)
    return PatternMatch("MORNING_STAR", 3, round(min(1.0, penetration), 3), {
        "first_body": round(first.body, 6), "star_body": round(star.body, 6),
        "third_close_penetration_pct": round(penetration * 100, 2),
    })


def three_white_soldiers(a: Candle, b: Candle, c: Candle) -> PatternMatch | None:
    candles = (a, b, c)
    if not all(x.is_bullish for x in candles):
        return None
    if not (b.close > a.close and c.close > b.close):
        return None
    if not (a.open <= b.open <= a.close and b.open <= c.open <= b.close):
        return None
    if any(x.upper_wick > 0.3 * x.body for x in candles if x.body > 0):
        return None
    avg_body = sum(x.body for x in candles) / 3.0
    confidence = min(1.0, avg_body / max(a.range, 1e-9))
    return PatternMatch("THREE_WHITE_SOLDIERS", 3, round(confidence, 3), {
        "closes": [round(x.close, 6) for x in candles],
    })


SINGLE_CANDLE_PATTERNS = (hammer, inverted_hammer, dragonfly_doji)
TWO_CANDLE_PATTERNS = (bullish_engulfing, bullish_harami, piercing_line, tweezer_bottom)
THREE_CANDLE_PATTERNS = (morning_star, three_white_soldiers)


def scan(candles: Sequence[Candle]) -> list[PatternMatch]:
    """Scan a candle series for every supported bullish pattern ending at the last candle.
    Returns zero or more matches (patterns can legitimately co-occur, e.g. a hammer that is
    also part of a later engulfing setup)."""
    if not candles:
        return []
    matches: list[PatternMatch] = []
    last = candles[-1]
    for fn in SINGLE_CANDLE_PATTERNS:
        m = fn(last)
        if m:
            matches.append(m)
    if len(candles) >= 2:
        prev = candles[-2]
        for fn in TWO_CANDLE_PATTERNS:
            m = fn(prev, last)
            if m:
                matches.append(m)
    if len(candles) >= 3:
        first, star = candles[-3], candles[-2]
        for fn in THREE_CANDLE_PATTERNS:
            m = fn(first, star, last)
            if m:
                matches.append(m)
    return matches

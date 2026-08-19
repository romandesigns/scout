#!/usr/bin/env python3
"""
Scout Candlestick Pattern Real-Data Validation (v6.7.4, shadow/observational)

Scans the already-cached historical tick data (from the historical backtest work) for
bullish candlestick patterns on 1-minute resampled candles, and measures what actually
happened afterward -- forward return at 5/15/30 minutes -- using the same style of
recomputation as scripts/detection_quality.py.

This does NOT feed live detection. Purely measurement: does each pattern actually correlate
with favorable forward movement on real data, and how often does it fire at all?

Usage
-----
python -m scripts.candlestick_backtest --cache-dir data/replay-datasets/backtest \
    --output data/optimization/backtest/candlestick-report.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.candlestick import Candle, resample, scan
from app.replay import load_events

CANDLE_SECONDS = 15
RESAMPLE_SECONDS = 60
HORIZONS = (300, 900, 1800)  # 5m, 15m, 30m


def build_15s_candles(events) -> list[Candle]:
    trades = [e for e in events if e.event_type == "trade"]
    trades.sort(key=lambda e: e.source_ts)
    if not trades:
        return []
    buckets: dict[int, list[float]] = defaultdict(list)
    for t in trades:
        b = int(t.source_ts // CANDLE_SECONDS) * CANDLE_SECONDS
        buckets[b].append(float(t.payload["price"]))
    out = []
    for start in sorted(buckets):
        prices = buckets[start]
        out.append(Candle(start_ts=float(start), open=prices[0], high=max(prices), low=min(prices), close=prices[-1]))
    return out


def forward_return(candles: list[Candle], at_ts: float, entry_price: float, seconds: int) -> float | None:
    target = at_ts + seconds
    window = [c for c in candles if at_ts <= c.start_ts <= target]
    if not window:
        return None
    exit_price = window[-1].close
    if entry_price <= 0:
        return None
    return (exit_price / entry_price - 1.0) * 100.0


def main() -> int:
    p = argparse.ArgumentParser(description="Validate candlestick pattern signal quality against real historical data")
    p.add_argument("--cache-dir", default="data/replay-datasets/backtest")
    p.add_argument("--output", required=True)
    p.add_argument("--limit-files", type=int, default=None, help="Cap number of cached datasets scanned (smoke-testing)")
    args = p.parse_args()

    cache_dir = Path(args.cache_dir)
    files = sorted(cache_dir.glob("*.ndjson"))
    if args.limit_files:
        files = files[:args.limit_files]

    by_pattern: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scanned = 0
    for dataset in files:
        try:
            events, _ = load_events(dataset)
        except Exception:
            continue
        fine = build_15s_candles(events)
        if len(fine) < 5:
            continue
        coarse = resample(fine, RESAMPLE_SECONDS)
        if len(coarse) < 3:
            continue
        scanned += 1
        for i in range(2, len(coarse)):
            window = coarse[max(0, i - 2):i + 1]
            matches = scan(window)
            if not matches:
                continue
            entry = window[-1]
            forward_pool = coarse[i:]  # only forward candles for return calc, no lookahead leak
            for m in matches:
                row = {
                    "confidence": m.confidence,
                    "return_5m_pct": forward_return(forward_pool, entry.start_ts, entry.close, HORIZONS[0]),
                    "return_15m_pct": forward_return(forward_pool, entry.start_ts, entry.close, HORIZONS[1]),
                    "return_30m_pct": forward_return(forward_pool, entry.start_ts, entry.close, HORIZONS[2]),
                }
                by_pattern[m.name].append(row)

    summary = {}
    for name, rows in by_pattern.items():
        r5 = [r["return_5m_pct"] for r in rows if r["return_5m_pct"] is not None]
        r15 = [r["return_15m_pct"] for r in rows if r["return_15m_pct"] is not None]
        r30 = [r["return_30m_pct"] for r in rows if r["return_30m_pct"] is not None]
        summary[name] = {
            "occurrences": len(rows),
            "avg_confidence": round(statistics.mean(r["confidence"] for r in rows), 3),
            "avg_return_5m_pct": round(statistics.mean(r5), 3) if r5 else None,
            "avg_return_15m_pct": round(statistics.mean(r15), 3) if r15 else None,
            "avg_return_30m_pct": round(statistics.mean(r30), 3) if r30 else None,
            "positive_5m_rate": round(sum(1 for x in r5 if x > 0) / len(r5), 3) if r5 else None,
            "positive_15m_rate": round(sum(1 for x in r15 if x > 0) / len(r15), 3) if r15 else None,
        }

    report = {"datasets_scanned": scanned, "patterns": summary}
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nReport: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

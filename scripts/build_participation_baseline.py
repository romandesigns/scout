#!/usr/bin/env python3
"""
Scout Session-Relative Participation Baseline (2026-08-19 follow-up)

Purpose
-------
Every participation gate diagnosed this week (quality-layer, reversal-specific, Rust's
frozen recipe) uses one fixed absolute dollar/trade-count bar regardless of session --
premarket, regular hours, and after-hours have very different baseline liquidity, so a bar
calibrated for regular-hours activity is either too strict premarket or too loose late in
the day. This computes the REAL historical distribution of 30s trade-count and dollar-volume
from the cached tick data already collected this week, split by session, so a
session-relative bar can be calibrated from actual data instead of one universal guess.

This is a scoped first step toward "relative to the live market," not the full live
cross-sectional version (which would need synchronized cross-symbol state during replay --
a bigger infrastructure change not attempted here). A precomputed, session-specific
static bar still fixes half of the diagnosed flaw (session-blindness) using the
infrastructure that already exists.

Usage
-----
python -m scripts.build_participation_baseline --cache-dir data/replay-datasets/backtest \
    --output data/optimization/backtest/participation-baseline.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.replay import load_events

ET = ZoneInfo("America/New_York")


def session_for(ts: float) -> str:
    local = datetime.fromtimestamp(ts, timezone.utc).astimezone(ET)
    minutes = local.hour * 60 + local.minute
    if minutes >= 20 * 60 or minutes < 4 * 60:
        return "overnight"
    if minutes < 9 * 60 + 30:
        return "premarket"
    if minutes < 16 * 60:
        return "regular"
    return "afterhours"


def rolling_30s_samples(events) -> list[tuple[str, float, int]]:
    """Returns (session, dollar_volume_30s, trade_count_30s) sampled every ~15s, using a
    trailing 30s window -- mirrors Scout's own dollar30/trades30 computation exactly."""
    trades = sorted(((e.source_ts, float(e.payload["price"]), float(e.payload["size"]))
                      for e in events if e.event_type == "trade"), key=lambda t: t[0])
    if len(trades) < 5:
        return []
    out = []
    window: deque[tuple[float, float, float]] = deque()
    next_sample_at = trades[0][0]
    idx = 0
    for ts, price, size in trades:
        window.append((ts, price, size))
        while window and window[0][0] < ts - 30.0:
            window.popleft()
        if ts >= next_sample_at:
            dollar30 = sum(p * s for _, p, s in window)
            trades30 = len(window)
            out.append((session_for(ts), dollar30, trades30))
            next_sample_at = ts + 15.0
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Build session-relative participation percentile baseline from cached tick data")
    p.add_argument("--cache-dir", default="data/replay-datasets/backtest")
    p.add_argument("--output", required=True)
    args = p.parse_args()

    cache_dir = Path(args.cache_dir)
    files = sorted(cache_dir.glob("*.ndjson"))
    by_session_dollar: dict[str, list[float]] = defaultdict(list)
    by_session_trades: dict[str, list[int]] = defaultdict(list)

    for i, dataset in enumerate(files, 1):
        try:
            events, _ = load_events(dataset)
        except Exception:
            continue
        for session, dollar30, trades30 in rolling_30s_samples(events):
            by_session_dollar[session].append(dollar30)
            by_session_trades[session].append(trades30)
        if i % 100 == 0:
            print(f"[{i}/{len(files)}] datasets processed")

    percentiles = [50, 60, 70, 75, 80, 85, 90, 95]
    baseline = {}
    for session in ("premarket", "regular", "afterhours", "overnight"):
        dollars = sorted(by_session_dollar.get(session, []))
        trades = sorted(by_session_trades.get(session, []))
        if not dollars:
            continue
        def pct(sorted_vals, q):
            idx = min(len(sorted_vals) - 1, max(0, int(len(sorted_vals) * q / 100)))
            return sorted_vals[idx]
        baseline[session] = {
            "samples": len(dollars),
            "dollar_30s_percentiles": {str(q): round(pct(dollars, q), 2) for q in percentiles},
            "trades_30s_percentiles": {str(q): pct(trades, q) for q in percentiles},
        }
        print(f"{session:12s} n={len(dollars):7d}  dollar30 p75={pct(dollars,75):.0f} p85={pct(dollars,85):.0f} p90={pct(dollars,90):.0f}  "
              f"trades30 p75={pct(trades,75)} p85={pct(trades,85)} p90={pct(trades,90)}")

    Path(args.output).write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    print(f"\nJSON report: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

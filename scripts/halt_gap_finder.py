#!/usr/bin/env python3
"""
Scout Halt-Precursor Audit (v6.7.4 backtest instrumentation, proof-of-concept)

Purpose
-------
Measure whether Scout's HALT_PRESSURE stage actually gives lead time before real trading
halts, using data already on disk from the historical backtest -- no new API calls.

Ground truth here is a proxy, not confirmed exchange halt-status data: a real halt shows up
in tick data as a multi-minute gap with no trades during core session hours, usually after a
sharp upward move (the case the user cares about -- halts during an uptrend). This is a
standard, well-understood technique for inferring halts from trade tapes when a dedicated
halt-status feed isn't being queried, and is honestly reported as a proxy, not ground truth,
in the output.

Usage
-----
python -m scripts.halt_gap_finder --cache-dir data/replay-datasets/backtest \
    --findings data/optimization/backtest/findings-sample-traced.jsonl \
    --output data/optimization/backtest/halt-precursor-report.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.replay import load_events

ET = ZoneInfo("America/New_York")


def in_regular_session(ts: float) -> bool:
    local = datetime.fromtimestamp(ts, timezone.utc).astimezone(ET)
    minutes = local.hour * 60 + local.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60

GAP_SECONDS = 300.0          # minimum silent gap treated as a suspected halt (real LULD pauses are ~5min+)
UPTREND_MIN_GAIN_PCT = 8.0   # price must already be up at least this much before the gap
MIN_PRIOR_TRADES_5MIN = 15   # require real liquidity immediately before the gap, else it's just a thin/illiquid lull
WARNING_LOOKBACK_SECONDS = 900.0   # only count a HALT_PRESSURE finding as "warning" if within 15min of the gap
ET_OPEN_OFFSET_FROM_4AM = 5.5 * 3600   # regular session 9:30 ET = 5.5h after the 4:00 ET file start
ET_CLOSE_OFFSET_FROM_4AM = 12.0 * 3600  # regular session 16:00 ET = 12h after 4:00 ET file start


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def find_gaps(events) -> list[dict[str, Any]]:
    trades = [e for e in events if e.event_type == "trade"]
    trades.sort(key=lambda e: e.source_ts)
    if len(trades) < 2:
        return []
    # Regular-session reference price: first regular-session print, not premarket, so
    # "gain before the gap" reflects the actual regular-session uptrend, not overnight drift.
    regular = [t for t in trades if in_regular_session(t.source_ts)]
    if not regular:
        return []
    first_price = float(regular[0].payload["price"])
    gaps = []
    running_high = first_price
    for prev, cur in zip(trades, trades[1:]):
        if not (in_regular_session(prev.source_ts) and in_regular_session(cur.source_ts)):
            running_high = max(running_high, float(cur.payload["price"]))
            continue
        gain_pct = (running_high / first_price - 1.0) * 100.0 if first_price else 0.0
        dt = cur.source_ts - prev.source_ts
        # Cheap checks first -- only pay for the O(n) liquidity window scan on the rare
        # candidates that already pass gap-size and gain, not on every consecutive pair.
        if dt >= GAP_SECONDS and gain_pct >= UPTREND_MIN_GAIN_PCT:
            prior_window_trades = sum(
                1 for t in trades if prev.source_ts - 300.0 <= t.source_ts <= prev.source_ts
            )
        else:
            prior_window_trades = 0
        if dt >= GAP_SECONDS and gain_pct >= UPTREND_MIN_GAIN_PCT and prior_window_trades >= MIN_PRIOR_TRADES_5MIN:
            gaps.append({
                "gap_start": prev.source_ts, "gap_end": cur.source_ts, "gap_seconds": dt,
                "price_before": float(prev.payload["price"]), "price_after": float(cur.payload["price"]),
                "gain_before_pct": round(gain_pct, 2), "prior_5min_trade_count": prior_window_trades,
            })
        running_high = max(running_high, float(cur.payload["price"]))
    return gaps


def main() -> int:
    p = argparse.ArgumentParser(description="Proxy-based halt precursor lead-time audit from cached tick data")
    p.add_argument("--cache-dir", default="data/replay-datasets/backtest")
    p.add_argument("--findings", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    cache_dir = Path(args.cache_dir)
    findings_by_key: dict[tuple[str, str], list[dict]] = {}
    for row in load_jsonl(Path(args.findings)):
        findings_by_key[(row["ticker"], row["date"])] = row.get("findings") or []

    results = []
    for dataset in sorted(cache_dir.glob("*.ndjson")):
        # filename pattern: TICKER-YYYY-MM-DD-feed.ndjson
        stem = dataset.stem
        parts = stem.split("-")
        if len(parts) < 4:
            continue
        ticker = parts[0]
        date_str = "-".join(parts[1:4])
        try:
            events, _ = load_events(dataset)
        except Exception:
            continue
        gaps = find_gaps(events)
        if not gaps:
            continue
        findings = sorted(findings_by_key.get((ticker, date_str), []), key=lambda f: float(f["detected_at"]))
        for gap in gaps:
            window_start = gap["gap_start"] - WARNING_LOOKBACK_SECONDS
            prior_halt_pressure = [
                f for f in findings
                if f.get("stage") == "HALT_PRESSURE" and window_start <= float(f["detected_at"]) <= gap["gap_start"]
            ]
            prior_any = [f for f in findings if window_start <= float(f["detected_at"]) <= gap["gap_start"]]
            lead = None
            if prior_halt_pressure:
                lead = gap["gap_start"] - float(prior_halt_pressure[0]["detected_at"])
            results.append({
                "ticker": ticker, "date": date_str, **gap,
                "had_halt_pressure_warning": bool(prior_halt_pressure),
                "lead_seconds": lead,
                "any_prior_finding": bool(prior_any),
                "prior_finding_stages": sorted({f.get("stage") for f in prior_any}),
            })

    warned = [r for r in results if r["had_halt_pressure_warning"]]
    unwarned = [r for r in results if not r["had_halt_pressure_warning"]]
    leads = [r["lead_seconds"] for r in warned if r["lead_seconds"] is not None]
    report = {
        "methodology": (
            f"PROXY: suspected halts inferred from >={GAP_SECONDS:.0f}s regular-session trade-print "
            f"gaps after >={UPTREND_MIN_GAIN_PCT:.0f}% intraday gain, requiring >={MIN_PRIOR_TRADES_5MIN} "
            f"trades in the preceding 5 minutes (excludes illiquid lulls). A HALT_PRESSURE finding counts "
            f"as a warning only within {WARNING_LOOKBACK_SECONDS:.0f}s before the gap. NOT confirmed "
            f"exchange halt-status data -- treat as directional, not final."
        ),
        "suspected_halt_events": len(results),
        "warned_by_halt_pressure": len(warned),
        "unwarned": len(unwarned),
        "warn_rate": (len(warned) / len(results)) if results else None,
        "median_lead_seconds": sorted(leads)[len(leads)//2] if leads else None,
        "lead_seconds_all": leads,
        "unwarned_events": unwarned,
        "events": results,
    }
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"events", "unwarned_events", "lead_seconds_all"}}, indent=2))
    print(f"\nReport: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

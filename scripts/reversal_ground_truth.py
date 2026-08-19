#!/usr/bin/env python3
"""
Scout Bearish-to-Bullish Reversal Ground Truth (v6.7.4 backtest instrumentation)

Purpose
-------
Detector-blind ground truth for real intraday reversal episodes -- a meaningful drawdown
from a local peak followed by a meaningful bounce off the low -- to measure whether Scout's
existing REVERSAL_WATCH / RECLAIM / EMA_RECLAIM / VWAP_RECLAIM stages actually catch these
early, the same way historical_mover_finder.py measures upward-move recall.

Ground truth definition mirrors Scout's own reversal math exactly (app/market.py ~1464-1472,
settings.reversal_*) so "did Scout see a real reversal" is judged by the same yardstick Scout
uses internally, applied independently to price action rather than to Scout's own findings:
  - a rolling local peak,
  - a drawdown of at least REVERSAL_MIN_DRAWDOWN_PCT (default 5.0%) from that peak,
  - within REVERSAL_MAX_LOW_AGE_SECONDS (default 900s) of the low being set,
  - a subsequent bounce off that low of at least REVERSAL_RECLAIM_MIN_BOUNCE_PCT (default 2.0%)
    (the "confirmed reclaim" bar) and separately the lower REVERSAL_WATCH_MIN_BOUNCE_PCT
    (default 0.75%) bar (the "early watch" threshold).

Usage
-----
python -m scripts.reversal_ground_truth --start 2026-08-03 --end 2026-08-14 \
    --output data/optimization/backtest/reversals.jsonl
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from app.config import settings
from scripts.historical_mover_finder import session_window, trading_days
from scripts.replay_last10 import chunks, get_assets, get_bars

# Same numeric bars Scout itself uses -- see app/config.py.
MIN_DRAWDOWN_PCT = settings.reversal_min_drawdown_pct
WATCH_BOUNCE_PCT = settings.reversal_watch_min_bounce_pct
RECLAIM_BOUNCE_PCT = settings.reversal_reclaim_min_bounce_pct
MAX_LOW_AGE_SECONDS = settings.reversal_max_low_age_seconds


def bar_ts(row: dict) -> float:
    from datetime import datetime
    return datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).timestamp()


def find_reversals_for_date(target: date, symbols: list[str]) -> list[dict]:
    start, end = session_window(target)
    rows_out: list[dict] = []
    total_batches = (len(symbols) + 199) // 200
    for idx, batch in enumerate(chunks(symbols, 200), 1):
        bars_by_symbol = get_bars(batch, "1Min", start, end, settings.alpaca_feed)
        print(f"[{target.isoformat()}] bars batch {idx}/{total_batches}: {len(bars_by_symbol)} symbols returned")
        for sym, bars in bars_by_symbol.items():
            bars = sorted(bars, key=bar_ts)
            if len(bars) < 5:
                continue
            ref_price = float(bars[0]["c"])
            if not (settings.min_price <= ref_price <= settings.max_price):
                continue

            peak_price, peak_ts = ref_price, bar_ts(bars[0])
            low_since_peak, low_ts = None, None
            episode_open = False
            for row in bars:
                ts, hi, lo, close = bar_ts(row), float(row["h"]), float(row["l"]), float(row["c"])

                if not episode_open:
                    if hi > peak_price:
                        peak_price, peak_ts = hi, ts
                        low_since_peak, low_ts = None, None
                        continue
                    drawdown = max(0.0, (peak_price - lo) / peak_price * 100.0)
                    if drawdown >= MIN_DRAWDOWN_PCT and (low_since_peak is None or lo < low_since_peak):
                        low_since_peak, low_ts = lo, ts
                        episode_open = True
                    continue

                # Episode open: tracking bounce off the established low, within the age window.
                if ts - low_ts > MAX_LOW_AGE_SECONDS:
                    episode_open = False
                    peak_price, peak_ts = hi, ts
                    low_since_peak, low_ts = None, None
                    continue
                if lo < low_since_peak:
                    low_since_peak, low_ts = lo, ts
                    continue
                bounce_pct = (close / low_since_peak - 1.0) * 100.0 if low_since_peak else 0.0
                if bounce_pct >= WATCH_BOUNCE_PCT:
                    row_out = {
                        "ticker": sym, "date": target.isoformat(),
                        "peak_price": peak_price, "peak_at": peak_ts,
                        "low_price": low_since_peak, "low_at": low_ts,
                        "drawdown_pct": round((peak_price - low_since_peak) / peak_price * 100.0, 3),
                        "watch_crossed_at": ts if bounce_pct >= WATCH_BOUNCE_PCT else None,
                        "watch_bounce_pct": round(bounce_pct, 3),
                    }
                    if bounce_pct >= RECLAIM_BOUNCE_PCT:
                        row_out["reclaim_crossed_at"] = ts
                        row_out["reclaim_bounce_pct"] = round(bounce_pct, 3)
                    rows_out.append(row_out)
                    # Episode consumed -- reset to look for the next one from here.
                    episode_open = False
                    peak_price, peak_ts = hi, ts
                    low_since_peak, low_ts = None, None
    return rows_out


def main() -> int:
    p = argparse.ArgumentParser(description="Detector-blind ground truth for bearish-to-bullish reversal episodes")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--max-symbols", type=int, default=None)
    args = p.parse_args()

    if not settings.alpaca_key or not settings.alpaca_secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_API_SECRET are required")

    days = trading_days(date.fromisoformat(args.start), date.fromisoformat(args.end))
    print(f"Scanning {len(days)} trading date(s) for reversal episodes (drawdown>={MIN_DRAWDOWN_PCT}%, "
          f"watch-bounce>={WATCH_BOUNCE_PCT}%, reclaim-bounce>={RECLAIM_BOUNCE_PCT}%)")
    symbols = get_assets()
    if args.max_symbols:
        symbols = symbols[:args.max_symbols]
    print(f"Universe: {len(symbols)} symbols")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for day in days:
            rows = find_reversals_for_date(day, symbols)
            total += len(rows)
            reclaimed = sum(1 for r in rows if "reclaim_crossed_at" in r)
            print(f"[{day.isoformat()}] reversal episodes={len(rows)} (reached reclaim bar: {reclaimed})")
            for row in rows:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    print(f"Wrote {total} reversal episodes -> {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

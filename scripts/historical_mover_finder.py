#!/usr/bin/env python3
"""
Scout Historical Mover Finder (v6.7.4 backtest instrumentation)

Purpose
-------
Build an independent, detector-blind ground truth of which $0.15-$10 tickers
actually had meaningful/explosive bullish moves on past trading dates, using
Alpaca historical 1-minute bars for the full active tradable universe.

This is deliberately NOT filtered through any Scout detection logic -- it
answers "what moved" using only price action, so it can be used to measure
Scout's recall (what fraction of real movers Scout actually caught, and how
early) without the circularity of using Scout's own heuristics to pick which
days/tickers to test.

Output is one JSONL file: one row per (ticker, date) that crossed at least
one of the tracked thresholds (5/10/20/50%), or was sampled as a same-day
non-mover control row for later precision/false-positive measurement.

Usage
-----
python -m scripts.historical_mover_finder --start 2026-08-10 --end 2026-08-14 \
    --output data/optimization/backtest/movers-20260810-20260814.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from scripts.replay_last10 import chunks, get_assets, get_bars

ET = ZoneInfo(settings.timezone)
THRESHOLDS = (5.0, 10.0, 20.0, 50.0)


def trading_days(start: date, end: date) -> list[date]:
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def session_window(target: date) -> tuple[datetime, datetime]:
    # Mirrors build_alpaca_replay.py: pre-market through after-hours SIP.
    start = datetime.combine(target, datetime.min.time(), ET).replace(hour=4)
    end = start.replace(hour=20)
    return start, end


def bar_ts(row: dict) -> float:
    return datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).timestamp()


def find_movers_for_date(target: date, symbols: list[str], control_rate: int) -> list[dict]:
    start, end = session_window(target)
    rows_out: list[dict] = []
    rng = random.Random(f"{target.isoformat()}-scout-backtest")

    total_batches = (len(symbols) + 199) // 200
    for idx, batch in enumerate(chunks(symbols, 200), 1):
        bars_by_symbol = get_bars(batch, "1Min", start, end, settings.alpaca_feed)
        print(f"[{target.isoformat()}] bars batch {idx}/{total_batches}: {len(bars_by_symbol)} symbols returned")
        for sym, bars in bars_by_symbol.items():
            bars = sorted(bars, key=bar_ts)
            if not bars:
                continue
            ref_price = float(bars[0]["c"])
            if not (settings.min_price <= ref_price <= settings.max_price):
                continue
            ref_ts = bar_ts(bars[0])

            crossings: dict[str, dict] = {}
            max_pct = 0.0
            max_price = ref_price
            for row in bars:
                px = float(row["h"])
                pct = (px / ref_price - 1.0) * 100.0
                if px > max_price:
                    max_price = px
                if pct > max_pct:
                    max_pct = pct
                for threshold in THRESHOLDS:
                    key = str(int(threshold))
                    if key not in crossings and pct >= threshold:
                        crossings[key] = {"at": bar_ts(row), "price": px, "pct": pct}

            if crossings:
                rows_out.append({
                    "ticker": sym,
                    "date": target.isoformat(),
                    "is_mover": True,
                    "reference_price": ref_price,
                    "reference_at": ref_ts,
                    "max_pct": round(max_pct, 4),
                    "max_price": max_price,
                    "crossings": crossings,
                    "bar_count": len(bars),
                })
            elif control_rate > 0 and rng.randrange(control_rate) == 0:
                rows_out.append({
                    "ticker": sym,
                    "date": target.isoformat(),
                    "is_mover": False,
                    "reference_price": ref_price,
                    "reference_at": ref_ts,
                    "max_pct": round(max_pct, 4),
                    "max_price": max_price,
                    "crossings": {},
                    "bar_count": len(bars),
                })
    return rows_out


def main() -> int:
    p = argparse.ArgumentParser(description="Build detector-blind historical mover ground truth from Alpaca bars")
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--output", required=True)
    p.add_argument("--control-rate", type=int, default=40,
                    help="Emit ~1/N non-mover symbols per date as a control sample for precision scoring. 0 disables.")
    p.add_argument("--symbols", default=None, help="Comma-separated explicit symbol list; skips the full-universe scan")
    p.add_argument("--max-symbols", type=int, default=None, help="Cap the scanned universe (smoke-testing only)")
    args = p.parse_args()

    if not settings.alpaca_key or not settings.alpaca_secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_API_SECRET are required")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    days = trading_days(start, end)
    print(f"Scanning {len(days)} trading date(s): {[d.isoformat() for d in days]}")

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = get_assets()
        if args.max_symbols:
            symbols = symbols[:args.max_symbols]
    print(f"Scanned universe: {len(symbols)} symbols")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total_movers = 0
    total_controls = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for day in days:
            rows = find_movers_for_date(day, symbols, args.control_rate)
            movers = [r for r in rows if r["is_mover"]]
            controls = [r for r in rows if not r["is_mover"]]
            total_movers += len(movers)
            total_controls += len(controls)
            print(f"[{day.isoformat()}] movers={len(movers)} control_sample={len(controls)}")
            for row in rows:
                fh.write(json.dumps(row, separators=(",", ":")) + "\n")

    print(f"Wrote {total_movers} mover rows + {total_controls} control rows -> {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

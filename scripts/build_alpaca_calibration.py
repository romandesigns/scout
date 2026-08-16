from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from app.config import settings
from app.replay import MarketEvent, SCHEMA_VERSION, write_events
from scripts.replay_last10 import get_assets, get_trades, prefilter, session_windows


def trading_dates(start: date, end: date):
    current = start
    while current <= end:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a multi-session Alpaca replay calibration dataset.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--symbols", help="Optional comma-separated symbols; otherwise prefilter the active universe.")
    parser.add_argument("--max-symbols-per-session", type=int, default=40)
    parser.add_argument("--sessions", choices=["all", "premarket", "regular", "afterhours"], default="all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not settings.alpaca_key or not settings.alpaca_secret:
        raise SystemExit("Existing ALPACA_API_KEY and ALPACA_API_SECRET values are required")

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    if end < start:
        raise SystemExit("end-date must not precede start-date")
    explicit = [item.strip().upper() for item in (args.symbols or "").split(",") if item.strip()]
    universe = explicit or get_assets()
    events: list[MarketEvent] = []

    for target in trading_dates(start, end):
        if explicit:
            candidates = explicit[: args.max_symbols_per_session]
        else:
            candidates = []
            for _, symbol, _ in prefilter(target, universe, args.sessions):
                if symbol not in candidates:
                    candidates.append(symbol)
                if len(candidates) >= args.max_symbols_per_session:
                    break
        print(f"session={target} candidates={len(candidates)}")
        for symbol in candidates:
            for _, feed, window_start, window_end in session_windows(target, args.sessions):
                for row in get_trades(symbol, window_start, window_end, feed):
                    ts = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).timestamp()
                    events.append(MarketEvent(
                        schema=SCHEMA_VERSION, event_type="trade", symbol=symbol,
                        source_ts=ts, received_ts=ts, sequence=0, feed=feed,
                        payload={"price": float(row["p"]), "size": float(row["s"]), "exchange": row.get("x"), "conditions": row.get("c", [])},
                    ))

    events.sort(key=lambda item: (item.source_ts, item.symbol))
    normalized = [MarketEvent(**{**event.__dict__, "sequence": index}) for index, event in enumerate(events, 1)]
    write_events(args.output, normalized)
    print(f"Wrote {len(normalized)} events across {start}..{end} to {args.output}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.replay import MarketEvent, SCHEMA_VERSION, write_events
from scripts.replay_last10 import get_trades


def main() -> None:
    parser = argparse.ArgumentParser(description="Download one symbol/session into Scout's canonical replay format.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--date", required=True, help="U.S. market date YYYY-MM-DD")
    parser.add_argument("--feed", default=settings.alpaca_feed)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not settings.alpaca_key or not settings.alpaca_secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_API_SECRET are required")

    et = ZoneInfo(settings.timezone)
    target = date.fromisoformat(args.date)
    start = datetime.combine(target, datetime.min.time(), et).replace(hour=4)
    end = start.replace(hour=20)
    symbol = args.symbol.upper()
    rows = get_trades(symbol, start, end, args.feed)
    events = []
    for sequence, row in enumerate(rows, 1):
        ts = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).timestamp()
        events.append(MarketEvent(
            schema=SCHEMA_VERSION, event_type="trade", symbol=symbol,
            source_ts=ts, received_ts=ts, sequence=sequence, feed=args.feed,
            payload={"price": float(row["p"]), "size": float(row["s"]), "exchange": row.get("x"), "conditions": row.get("c", [])},
        ))
    output = args.output or settings.data_dir / "replay-datasets" / f"{symbol}-{target.isoformat()}-{args.feed}.ndjson"
    write_events(output, events)
    print(f"Wrote {len(events)} events to {output}")


if __name__ == "__main__":
    main()

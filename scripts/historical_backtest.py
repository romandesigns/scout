#!/usr/bin/env python3
"""
Scout Historical Backtest Runner (v6.7.4 backtest instrumentation)

Purpose
-------
Feed real historical Alpaca trades for each (ticker, date) in a ground-truth
mover file (see historical_mover_finder.py) through Scout's actual live
detector path (app.replay.run_dataset -> the same MarketWatcher used in
production), fully isolated from production storage and notifications.

Raw per-symbol/day NDJSON datasets are cached under
data/replay-datasets/backtest/ so re-running is cheap. Each replay is
isolated in its own throwaway SQLite state file and report directory under
data/replays/backtest/.

Usage
-----
python -m scripts.historical_backtest --movers data/optimization/backtest/movers-*.jsonl \
    --output data/optimization/backtest/findings.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import settings
from app.replay import MarketEvent, SCHEMA_VERSION, run_dataset, write_events
from scripts.historical_mover_finder import session_window
from scripts.replay_last10 import get_trades

ET = ZoneInfo(settings.timezone)


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def build_dataset(ticker: str, target: date, feed: str, cache_dir: Path) -> Path:
    dataset = cache_dir / f"{ticker}-{target.isoformat()}-{feed}.ndjson"
    if dataset.exists() and dataset.stat().st_size > 0:
        return dataset
    start, end = session_window(target)
    rows = get_trades(ticker, start, end, feed)
    events = []
    for sequence, row in enumerate(rows, 1):
        ts = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).timestamp()
        events.append(MarketEvent(
            schema=SCHEMA_VERSION, event_type="trade", symbol=ticker,
            source_ts=ts, received_ts=ts, sequence=sequence, feed=feed,
            payload={"price": float(row["p"]), "size": float(row["s"]), "exchange": row.get("x"), "conditions": row.get("c", [])},
        ))
    write_events(dataset, events)
    return dataset


async def replay_one(ticker: str, target: date, feed: str, cache_dir: Path, output_root: Path) -> dict:
    dataset = build_dataset(ticker, target, feed, cache_dir)
    if dataset.stat().st_size == 0:
        return {"ticker": ticker, "date": target.isoformat(), "status": "NO_TRADES", "findings": []}
    try:
        report = await run_dataset(dataset, output_root)
    except ValueError as exc:
        return {"ticker": ticker, "date": target.isoformat(), "status": f"EMPTY: {exc}", "findings": []}
    findings = [
        {
            "ticker": f["ticker"], "stage": f["stage"], "detected_at": f["detected_at"],
            "price": f["price"], "quality_label": f.get("quality_label"),
            "actionable_rank": f.get("actionable_rank"), "quality_score": f.get("quality_score"),
            "promotion_trace": (f.get("candidate_profile") or {}).get("promotion_trace"),
        }
        for f in report.get("findings", [])
    ]
    return {
        "ticker": ticker, "date": target.isoformat(), "status": "OK",
        "processed_events": report.get("processed_events"),
        "findings_count": len(findings), "findings": findings,
    }


async def run_all(rows: list[dict], cache_dir: Path, output_root: Path, limit: int | None) -> list[dict]:
    out = []
    feed = settings.alpaca_feed
    total = len(rows) if limit is None else min(limit, len(rows))
    for i, row in enumerate(rows[:total], 1):
        ticker = row["ticker"]
        target = date.fromisoformat(row["date"])
        print(f"[{i}/{total}] replaying {ticker} {target.isoformat()} (mover={row.get('is_mover')})")
        try:
            result = await replay_one(ticker, target, feed, cache_dir, output_root)
        except Exception as exc:
            result = {"ticker": ticker, "date": row["date"], "status": f"ERROR: {exc}", "findings": []}
        result["is_mover"] = row.get("is_mover")
        out.append(result)
        print(f"    -> status={result['status']} findings={len(result.get('findings') or [])}")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Replay ground-truth mover/control rows through Scout's real detector")
    p.add_argument("--movers", required=True, help="JSONL from historical_mover_finder.py")
    p.add_argument("--output", required=True, help="Output JSONL of replayed findings per (ticker, date)")
    p.add_argument("--cache-dir", default="data/replay-datasets/backtest")
    p.add_argument("--replay-root", default="data/replays/backtest")
    p.add_argument("--limit", type=int, default=None, help="Only replay the first N rows (for quick smoke tests)")
    args = p.parse_args()

    if not settings.alpaca_key or not settings.alpaca_secret:
        raise SystemExit("ALPACA_API_KEY and ALPACA_API_SECRET are required")

    rows = load_rows(Path(args.movers))
    print(f"Loaded {len(rows)} ground-truth rows ({sum(1 for r in rows if r.get('is_mover'))} movers, "
          f"{sum(1 for r in rows if not r.get('is_mover'))} control)")

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_root = Path(args.replay_root)
    output_root.mkdir(parents=True, exist_ok=True)

    results = asyncio.run(run_all(rows, cache_dir, output_root, args.limit))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in results:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(f"Wrote {len(results)} replay results -> {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Scout True Hybrid Replay (v6.7.4 backtest instrumentation)

Purpose
-------
Milestone 009 found that every backtest replay built earlier today (`historical_backtest.py`
-> `app.replay.run_dataset`) only exercises Python's detector -- it never invokes Rust, even
though production is Rust-primary (`app/main.py` wires a `RustPerceptionBridge` that feeds
`MarketWatcher.handle_rust_candidate`). This script closes that gap: it replays the same
cached tick data through BOTH engines in correct time order, exactly mirroring the live
production data flow, so recall/precision can be measured for the system that's actually
deployed, not just Python's half of it.

Rust's candidates are read from pre-computed reports (scout-market-replay.exe run once per
dataset, see MILESTONES/2026-08-18-009) rather than re-invoked per replay -- cheap, since
Rust's engine is frozen/deterministic and doesn't depend on Python's live state.

Mechanics
---------
For a single (ticker, date):
  1. Load raw trade events (same NDJSON everything else today used).
  2. Load Rust's pre-computed candidates for the same dataset.
  3. Merge into one time-ordered stream tagged by type.
  4. Replay trade events through MarketWatcher exactly as app.replay.run_dataset does.
  5. When a Rust-candidate event's timestamp is reached, call
     MarketWatcher.handle_rust_candidate(candidate) -- the exact same call production makes
     when the live Rust subprocess bridge delivers a candidate.
  6. Capture every finding from either path via one shared ReplayDispatcher.

Usage
-----
python -m scripts.hybrid_replay --movers data/optimization/backtest/movers-sample.jsonl \
    --output data/optimization/backtest/findings-hybrid.jsonl \
    --cache-dir data/replay-datasets/backtest --rust-dir data/replays/rust-batch
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import Store
from app.market import MarketWatcher, trading_session_key
from app.models import SymbolState
from app.replay import ReplayDispatcher, load_events


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


async def replay_hybrid_one(ticker: str, date: str, feed: str, cache_dir: Path, rust_dir: Path) -> dict:
    dataset = cache_dir / f"{ticker}-{date}-{feed}.ndjson"
    rust_report = rust_dir / f"{ticker}-{date}-{feed}.json"
    if not dataset.exists() or dataset.stat().st_size == 0:
        return {"ticker": ticker, "date": date, "status": "NO_TICK_DATA", "findings": []}

    events, _ = load_events(dataset)
    if not events:
        return {"ticker": ticker, "date": date, "status": "EMPTY", "findings": []}

    rust_candidates: list[dict] = []
    if rust_report.exists():
        try:
            rust_candidates = json.loads(rust_report.read_text(encoding="utf-8")).get("candidates") or []
        except Exception:
            rust_candidates = []

    # One merged, time-ordered stream: ('trade', ts, event) | ('rust', ts, candidate)
    timeline: list[tuple[str, float, Any]] = [("trade", e.source_ts, e) for e in events]
    timeline.extend(("rust", float(c["detected_at"]), c) for c in rust_candidates)
    timeline.sort(key=lambda row: (row[1], 0 if row[0] == "trade" else 1))

    store = Store(":memory:")
    try:
        capture = ReplayDispatcher()
        market = MarketWatcher(store, capture)
        rust_calls = 0
        for kind, ts, payload in timeline:
            if kind == "trade":
                event = payload
                state = market.states.get(event.symbol)
                if state is None:
                    state = market.states[event.symbol] = SymbolState(event.symbol, settings.bucket_seconds, settings.keep_buckets)
                price = float(event.payload["price"])
                size = float(event.payload["size"])
                state.update_trade(ts, price, size, trading_session_key(ts))
                now_ms = ts * 1000
                if now_ms - state.last_fast_eval_at * 1000 >= settings.fast_path_min_interval_ms:
                    state.last_fast_eval_at = ts
                    metrics = market._metrics(state, ts)
                    if metrics:
                        await market._maybe_emit(state, metrics, ts, fast=True)
                if ts - state.last_eval_at >= settings.eval_seconds:
                    state.last_eval_at = ts
                    metrics = market._metrics(state, ts)
                    if metrics:
                        await market._maybe_emit(state, metrics, ts, fast=False)
            else:
                # Exactly what app/main.py's live RustPerceptionBridge callback does.
                await market.handle_rust_candidate(payload)
                rust_calls += 1

        findings = [
            {
                "ticker": f.ticker, "stage": f.stage, "detected_at": f.detected_at, "price": f.price,
                "quality_label": f.quality_label, "actionable_rank": f.actionable_rank,
                "quality_score": f.quality_score, "shadow_mode": f.shadow_mode,
                "source": "rust_triggered" if "Rust primary perception" in " ".join(f.evidence or []) else "python_native",
                # Rich feature capture for discriminator analysis (2026-08-19 follow-up):
                # the values the gate actually saw at the moment of this decision.
                "score": f.score, "vol_ratio_15s": f.vol_ratio_15s, "vol_ratio_30s": f.vol_ratio_30s,
                "change_3s_pct": f.change_3s_pct, "change_5s_pct": f.change_5s_pct,
                "change_10s_pct": f.change_10s_pct, "change_15s_pct": f.change_15s_pct,
                "change_30s_pct": f.change_30s_pct, "change_60s_pct": f.change_60s_pct,
                "accel_15s_pp": f.accel_15s_pp, "dollar_volume_15s": f.dollar_volume_15s,
                "dollar_volume_30s": f.dollar_volume_30s, "trades_15s": f.trades_15s, "trades_30s": f.trades_30s,
                "above_vwap": f.above_vwap, "ema9": f.ema9, "ema21": f.ema21, "ema9_slope": f.ema9_slope,
                "quiet_break": f.quiet_break, "extension_pct": f.extension_pct,
                "directional_efficiency": f.directional_efficiency, "active_bucket_ratio": f.active_bucket_ratio,
                "direction_reversals": f.direction_reversals, "rejection_reasons": list(f.rejection_reasons or []),
                "catalyst_headline": f.catalyst_headline, "catalyst_category": f.catalyst_category,
                "evidence": list(f.evidence or []),
            }
            for f in capture.items
        ]
        return {
            "ticker": ticker, "date": date, "status": "OK", "rust_candidates_fed": rust_calls,
            "findings_count": len(findings), "findings": findings,
        }
    finally:
        store.close()


async def run_all(rows: list[dict], cache_dir: Path, rust_dir: Path, limit: int | None) -> list[dict]:
    feed = settings.alpaca_feed
    total = len(rows) if limit is None else min(limit, len(rows))
    out = []
    for i, row in enumerate(rows[:total], 1):
        ticker, date = row["ticker"], row["date"]
        print(f"[{i}/{total}] hybrid replay {ticker} {date} (mover={row.get('is_mover')})")
        try:
            result = await replay_hybrid_one(ticker, date, feed, cache_dir, rust_dir)
        except Exception as exc:
            result = {"ticker": ticker, "date": date, "status": f"ERROR: {exc}", "findings": []}
        result["is_mover"] = row.get("is_mover")
        out.append(result)
        print(f"    -> status={result['status']} findings={len(result.get('findings') or [])} rust_fed={result.get('rust_candidates_fed', 0)}")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Replay cached tick data through BOTH Rust and Python, faithful to production wiring")
    p.add_argument("--movers", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cache-dir", default="data/replay-datasets/backtest")
    p.add_argument("--rust-dir", default="data/replays/rust-batch")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    rows = load_rows(Path(args.movers))
    results = asyncio.run(run_all(rows, Path(args.cache_dir), Path(args.rust_dir), args.limit))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in results:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(f"Wrote {len(results)} hybrid replay results -> {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

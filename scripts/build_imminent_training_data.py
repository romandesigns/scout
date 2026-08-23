#!/usr/bin/env python3
"""Build leakage-safe 15-30 second imminent-move features from Alpaca trades and NBBO quotes."""
from __future__ import annotations

import argparse
import json
import math
import random
from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

from app.config import settings
from scripts.historical_mover_finder import session_window
from scripts.imminent_move_scorer import load_jsonl, objective_moves


FEATURES = (
    "return_3s_pct", "return_5s_pct", "return_15s_pct", "return_30s_pct", "return_60s_pct",
    "accel_5v15_pp", "trades_5s", "trades_15s", "trades_30s", "trades_60s",
    "dollar_5s", "dollar_15s", "dollar_30s", "dollar_60s",
    "trade_rate_accel", "spread_bps", "bid_ask_imbalance", "mid_return_5s_pct",
    "mid_return_15s_pct", "quote_updates_5s", "quote_updates_15s", "spread_change_5s_bps",
)


def _ts(value: str) -> float:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def download_quotes(ticker: str, target: date, cache_dir: Path, feed: str) -> Path:
    path = cache_dir / f"{ticker}-{target.isoformat()}-{feed}-quotes.jsonl"
    if path.exists() and path.stat().st_size:
        return path
    if not settings.alpaca_key or not settings.alpaca_secret:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_API_SECRET are required")
    start, end = session_window(target)
    params: dict[str, Any] = {
        "start": start.isoformat(), "end": end.isoformat(), "feed": feed,
        "sort": "asc", "limit": 10000,
    }
    headers = {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}
    rows: list[dict[str, Any]] = []
    while True:
        response = requests.get(
            f"{settings.alpaca_data_base}/v2/stocks/{ticker}/quotes",
            params=params, headers=headers, timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        for quote in payload.get("quotes") or []:
            rows.append({
                "ts": _ts(quote["t"]), "bid": float(quote.get("bp") or 0),
                "ask": float(quote.get("ap") or 0), "bid_size": float(quote.get("bs") or 0),
                "ask_size": float(quote.get("as") or 0),
            })
        token = payload.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    return path


def load_quotes(path: Path) -> list[dict[str, float]]:
    rows = load_jsonl(path)
    rows = [r for r in rows if float(r.get("ts") or 0) > 0 and float(r.get("ask") or 0) >= float(r.get("bid") or 0) > 0]
    rows.sort(key=lambda r: float(r["ts"]))
    return rows


def load_trade_events(path: Path) -> list[tuple[float, float, float]]:
    rows: list[tuple[float, float, float]] = []
    for value in load_jsonl(path):
        if str(value.get("event_type", "")).lower() != "trade":
            continue
        payload = value.get("payload") or {}
        ts = float(value.get("source_ts") or 0)
        price, size = float(payload.get("price") or 0), float(payload.get("size") or 0)
        if ts > 0 and price > 0 and size >= 0:
            rows.append((ts, price, size))
    rows.sort()
    return rows


def _prior_price(trades: list[tuple[float, float, float]], timestamps: list[float], at: float, seconds: float) -> float | None:
    index = bisect_right(timestamps, at - seconds) - 1
    return trades[index][1] if index >= 0 else None


def _pct(old: float | None, new: float | None) -> float:
    return (new / old - 1.0) * 100.0 if old and new else 0.0


def feature_row(
    ticker: str, session_date: str, at: float,
    trades: list[tuple[float, float, float]], quotes: list[dict[str, float]],
    *, trade_ts: list[float] | None = None, quote_ts: list[float] | None = None,
    dollar_prefix: list[float] | None = None,
) -> dict[str, Any] | None:
    trade_ts = trade_ts if trade_ts is not None else [item[0] for item in trades]
    quote_ts = quote_ts if quote_ts is not None else [float(item["ts"]) for item in quotes]
    if dollar_prefix is None:
        dollar_prefix = [0.0]
        for _, trade_price, trade_size in trades:
            dollar_prefix.append(dollar_prefix[-1] + trade_price * trade_size)
    ti = bisect_right(trade_ts, at) - 1
    qi = bisect_right(quote_ts, at) - 1
    if ti < 0 or qi < 0:
        return None
    price = trades[ti][1]
    quote = quotes[qi]
    bid, ask = float(quote["bid"]), float(quote["ask"])
    midpoint = (bid + ask) / 2.0
    spread_bps = ((ask - bid) / midpoint * 10000.0) if midpoint > 0 else 0.0
    size_total = float(quote["bid_size"]) + float(quote["ask_size"])
    imbalance = ((float(quote["bid_size"]) - float(quote["ask_size"])) / size_total) if size_total > 0 else 0.0

    row: dict[str, Any] = {"ticker": ticker, "date": session_date, "sample_at": at, "price": price}
    for seconds in (3, 5, 15, 30, 60):
        row[f"return_{seconds}s_pct"] = _pct(_prior_price(trades, trade_ts, at, seconds), price)
    for seconds in (5, 15, 30, 60):
        window = trades[bisect_right(trade_ts, at - seconds):ti + 1]
        row[f"trades_{seconds}s"] = len(window)
        left = bisect_right(trade_ts, at - seconds)
        row[f"dollar_{seconds}s"] = dollar_prefix[ti + 1] - dollar_prefix[left]
    row["accel_5v15_pp"] = row["return_5s_pct"] - row["return_15s_pct"]
    row["trade_rate_accel"] = row["trades_5s"] * 3.0 - row["trades_15s"]
    row["spread_bps"] = spread_bps
    row["bid_ask_imbalance"] = imbalance
    for seconds in (5, 15):
        prior_qi = bisect_right(quote_ts, at - seconds) - 1
        prior_mid = None
        if prior_qi >= 0:
            prior = quotes[prior_qi]
            prior_mid = (float(prior["bid"]) + float(prior["ask"])) / 2.0
        row[f"mid_return_{seconds}s_pct"] = _pct(prior_mid, midpoint)
        row[f"quote_updates_{seconds}s"] = qi - bisect_right(quote_ts, at - seconds) + 1
    prior_qi = bisect_right(quote_ts, at - 5) - 1
    prior_spread = spread_bps
    if prior_qi >= 0:
        prior = quotes[prior_qi]
        prior_mid = (float(prior["bid"]) + float(prior["ask"])) / 2.0
        prior_spread = ((float(prior["ask"]) - float(prior["bid"])) / prior_mid * 10000.0) if prior_mid > 0 else spread_bps
    row["spread_change_5s_bps"] = spread_bps - prior_spread
    return row


def build_rows(
    ticker: str, session_date: str, trades: list[tuple[float, float, float]], quotes: list[dict[str, float]],
    *, sample_seconds: float, expansion_pct: float, horizon_seconds: float, lead_min: float,
    lead_max: float, max_pre_move_extension_pct: float, negative_ratio: int, seed: str,
) -> list[dict[str, Any]]:
    moves = objective_moves(ticker, [(ts, price) for ts, price, _ in trades], expansion_pct=expansion_pct, horizon_seconds=horizon_seconds, dedupe_seconds=horizon_seconds)
    completions = [float(move["completed_at"]) for move in moves]
    if not trades:
        return []
    rng = random.Random(f"{seed}-{ticker}-{session_date}")
    trade_ts = [item[0] for item in trades]
    quote_ts = [float(item["ts"]) for item in quotes]
    dollar_prefix = [0.0]
    for _, trade_price, trade_size in trades:
        dollar_prefix.append(dollar_prefix[-1] + trade_price * trade_size)
    start = math.ceil(trades[0][0] / sample_seconds) * sample_seconds
    end = trades[-1][0]
    positives: list[dict[str, Any]] = []
    negatives: list[dict[str, Any]] = []
    at = start
    while at <= end:
        row = feature_row(
            ticker, session_date, at, trades, quotes,
            trade_ts=trade_ts, quote_ts=quote_ts, dollar_prefix=dollar_prefix,
        )
        if row is not None:
            first = bisect_left(completions, at + lead_min)
            last = bisect_right(completions, at + lead_max)
            target = None
            pre_move_extension = None
            # More than one deduplicated move can complete inside the target
            # window.  Choose the first genuinely predictable move instead of
            # incorrectly labeling the row negative when only the first move
            # started after the sample time.
            for candidate in moves[first:last]:
                base_at = float(candidate["base_at"])
                base_price = float(candidate["base_price"])
                if base_at > at or base_price <= 0:
                    continue
                extension = (float(row["price"]) / base_price - 1.0) * 100.0
                if extension <= max_pre_move_extension_pct:
                    target = candidate
                    pre_move_extension = extension
                    break
            label = target is not None
            row["label"] = int(label)
            row["target_completion_at"] = float(target["completed_at"]) if label else None
            row["pre_move_extension_pct"] = pre_move_extension if label else None
            (positives if label else negatives).append(row)
        at += sample_seconds
    rng.shuffle(negatives)
    keep_negatives = (
        len(negatives) if negative_ratio <= 0
        else min(len(negatives), max(negative_ratio * len(positives), negative_ratio * 10))
    )
    return positives + negatives[:keep_negatives]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build imminent-move ML rows from cached Alpaca trades and historical quotes")
    parser.add_argument("--population", required=True, help="Mover/control JSONL")
    parser.add_argument("--output", required=True)
    parser.add_argument("--trade-cache", default="data/replay-datasets/backtest")
    parser.add_argument("--quote-cache", default="data/replay-datasets/quotes")
    parser.add_argument("--feed", default="sip")
    parser.add_argument("--max-movers-per-date", type=int, default=10)
    parser.add_argument("--max-controls-per-date", type=int, default=10)
    parser.add_argument("--max-trade-file-mb", type=float, default=15.0)
    parser.add_argument("--sample-seconds", type=float, default=5.0)
    parser.add_argument("--expansion-pct", type=float, default=2.0)
    parser.add_argument("--horizon-seconds", type=float, default=60.0)
    parser.add_argument("--lead-min", type=float, default=15.0)
    parser.add_argument("--lead-max", type=float, default=30.0)
    parser.add_argument("--max-pre-move-extension-pct", type=float, default=0.5)
    parser.add_argument("--negative-ratio", type=int, default=10,
                        help="Negatives retained per positive; 0 retains all negatives for unbiased evaluation")
    parser.add_argument("--seed", default="scout-imminent-v1")
    args = parser.parse_args()

    population = load_jsonl(Path(args.population))
    trade_cache, quote_cache = Path(args.trade_cache), Path(args.quote_cache)
    grouped: dict[tuple[str, bool], list[dict[str, Any]]] = defaultdict(list)
    for row in population:
        path = trade_cache / f"{row['ticker']}-{row['date']}-{args.feed}.ndjson"
        if path.exists() and path.stat().st_size <= args.max_trade_file_mb * 1024 * 1024:
            grouped[(str(row["date"]), bool(row.get("is_mover")))].append(row)
    selected: list[dict[str, Any]] = []
    rng = random.Random(args.seed)
    for (session_date, is_mover), rows in sorted(grouped.items()):
        rng.shuffle(rows)
        cap = args.max_movers_per_date if is_mover else args.max_controls_per_date
        selected.extend(rows[:cap])
    print(f"Selected {len(selected)} symbol-days across {len({row['date'] for row in selected})} dates")

    output_rows: list[dict[str, Any]] = []
    for index, item in enumerate(selected, 1):
        ticker, session_date = str(item["ticker"]), str(item["date"])
        print(f"[{index}/{len(selected)}] {ticker} {session_date} mover={bool(item.get('is_mover'))}")
        trades = load_trade_events(trade_cache / f"{ticker}-{session_date}-{args.feed}.ndjson")
        quote_path = download_quotes(ticker, date.fromisoformat(session_date), quote_cache, args.feed)
        quotes = load_quotes(quote_path)
        rows = build_rows(
            ticker, session_date, trades, quotes, sample_seconds=args.sample_seconds,
            expansion_pct=args.expansion_pct, horizon_seconds=args.horizon_seconds,
            lead_min=args.lead_min, lead_max=args.lead_max,
            max_pre_move_extension_pct=args.max_pre_move_extension_pct,
            negative_ratio=args.negative_ratio, seed=args.seed,
        )
        for row in rows:
            row["population_is_mover"] = bool(item.get("is_mover"))
        output_rows.extend(rows)
        print(f"  trades={len(trades)} quotes={len(quotes)} rows={len(rows)} positives={sum(r['label'] for r in rows)}")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in output_rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(f"Wrote {len(output_rows)} rows, positives={sum(row['label'] for row in output_rows)} -> {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

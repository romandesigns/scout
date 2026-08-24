#!/usr/bin/env python3
"""Label scripts/historical_backtest.py findings with the same forward-price
outcome definition Scout's live `outcomes` table uses (see
app/market.py::_update_outcomes), so backtest-generated findings can be pooled
with live findings in scripts/train_outcome_gate.py's training set.

Usage:
    python -m scripts.label_backtest_outcomes \
        --findings H:/scout-backtest/output/findings-pilot.jsonl \
        --cache-dir H:/scout-backtest/cache \
        --output H:/scout-backtest/output/labeled-findings-pilot.jsonl
"""
from __future__ import annotations

import argparse
import json
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from pathlib import Path

from scripts.imminent_move_scorer import load_trades


def pct_change(a: float, b: float) -> float:
    if not a:
        return 0.0
    return (b / a - 1.0) * 100.0


def label_finding(
    finding: dict, times: list[float], prices: list[float],
    suffix_max_price: list[float], suffix_argmax_idx: list[int],
) -> dict | None:
    detected_at = float(finding.get("detected_at") or 0.0)
    entry_price = float(finding.get("price") or 0.0)
    if detected_at <= 0 or entry_price <= 0:
        return None
    start = bisect_left(times, detected_at)
    if start >= len(times):
        return None

    def window_max_pct(seconds: float) -> float:
        end = bisect_right(times, detected_at + seconds)
        if end <= start:
            return 0.0
        return pct_change(entry_price, max(prices[start:end]))

    max_1m = window_max_pct(60)
    max_5m = window_max_pct(300)
    max_15m = window_max_pct(900)
    max_session = pct_change(entry_price, suffix_max_price[start])
    time_to_peak = times[suffix_argmax_idx[start]] - detected_at

    row = dict(finding)
    row["engine_source"] = row.pop("source", None) or "python"
    row["shadow_mode"] = bool(row.get("shadow_mode", False))
    row["candidate_profile"] = row.get("candidate_profile") or {}
    row["above_vwap"] = bool(row.get("above_vwap"))
    row["quiet_break"] = bool(row.get("quiet_break"))
    row["vwap"] = float(row.get("vwap") or 0.0)
    row["float_turnover"] = float(row.get("float_turnover") or 0.0)
    row["max_1m_pct"] = max_1m
    row["max_5m_pct"] = max_5m
    row["max_15m_pct"] = max_15m
    row["max_session_pct"] = max(max_session, 0.0)
    row["time_to_peak_seconds"] = time_to_peak
    row["date"] = datetime.fromtimestamp(detected_at, tz=timezone.utc).strftime("%Y-%m-%d")
    return row


def build_suffix_max(prices: list[float]) -> tuple[list[float], list[int]]:
    """Backward pass: suffix_max_price[i] / suffix_argmax_idx[i] give the max price
    (and its first index) over prices[i:], so a per-finding session-max query is O(1)
    instead of re-scanning the remainder of the file for every finding."""
    n = len(prices)
    suffix_max_price = [0.0] * n
    suffix_argmax_idx = [0] * n
    if n:
        suffix_max_price[-1] = prices[-1]
        suffix_argmax_idx[-1] = n - 1
        for i in range(n - 2, -1, -1):
            if prices[i] >= suffix_max_price[i + 1]:
                suffix_max_price[i] = prices[i]
                suffix_argmax_idx[i] = i
            else:
                suffix_max_price[i] = suffix_max_price[i + 1]
                suffix_argmax_idx[i] = suffix_argmax_idx[i + 1]
    return suffix_max_price, suffix_argmax_idx


def main() -> int:
    parser = argparse.ArgumentParser(description="Label backtest findings with forward-price outcomes")
    parser.add_argument("--findings", required=True, help="findings JSONL from scripts.historical_backtest")
    parser.add_argument("--cache-dir", required=True, help="Directory of cached {ticker}-{date}-{feed}.ndjson tick files")
    parser.add_argument("--feed", default="sip")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    cache_dir = Path(args.cache_dir)
    labeled: list[dict] = []
    skipped_no_trades = 0
    total_findings = 0
    with open(args.findings, encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]

    for row in rows:
        findings = row.get("findings") or []
        if not findings:
            continue
        ticker = str(row["ticker"]).upper()
        session_date = str(row["date"])
        dataset = cache_dir / f"{ticker}-{session_date}-{args.feed}.ndjson"
        if not dataset.exists():
            skipped_no_trades += len(findings)
            continue
        trades = load_trades(dataset)
        if not trades:
            skipped_no_trades += len(findings)
            continue
        times = [ts for ts, _ in trades]
        prices = [price for _, price in trades]
        suffix_max_price, suffix_argmax_idx = build_suffix_max(prices)
        for finding in findings:
            total_findings += 1
            labeled_row = label_finding(finding, times, prices, suffix_max_price, suffix_argmax_idx)
            if labeled_row is not None:
                labeled.append(labeled_row)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        for row in labeled:
            handle.write(json.dumps(row) + "\n")

    print(json.dumps({
        "input_ticker_days": len(rows), "total_findings": total_findings,
        "labeled_findings": len(labeled), "skipped_no_trades": skipped_no_trades,
        "output": str(output_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

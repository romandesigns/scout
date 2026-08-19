#!/usr/bin/env python3
"""
Scout Discriminator Analysis (2026-08-19 follow-up)

Purpose
-------
Experiment #3 (unified participation gate) admits 271 more actionable findings than the
true-hybrid baseline. Rather than guess at a smarter threshold shape, find out directly what
separates the incremental catches that turn out to be real winners from the ones that are
noise -- using the same forward-return classification already used all week.

Method
------
1. A finding is "incremental" if experiment #3 marks it actionable and there is no baseline
   finding for the same (ticker, date) actionable within a `--match-window` of the same
   detected_at -- i.e. baseline genuinely never caught anything comparable at that moment,
   not just a timing/labeling difference.
2. Each incremental finding is classified by real forward return (USEFUL/EARLY = winner,
   FALSE_POSITIVE/FADE = junk, MIXED = ambiguous) using the same candle-rebuild +
   forward_metrics + classification pipeline as scripts/backtest_scorer.py.
3. Feature distributions (volume ratios, price velocity, structure, quality_score, rejection
   reasons that still fired) are compared between winners and junk to look for a real
   discriminator -- not assumed, tested.

Usage
-----
python -m scripts.discriminator_analysis --baseline data/optimization/backtest/findings-rich-baseline.jsonl \
    --exp3 data/optimization/backtest/findings-rich-exp3.jsonl \
    --cache-dir data/replay-datasets/backtest --output data/optimization/backtest/discriminator-report.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts.backtest_scorer import build_candles
from scripts.detection_quality import actionable, classification, coverage, forward_metrics, safe_float
from app.config import settings


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def group(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[(r["ticker"], r["date"])].append(r)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Find what distinguishes exp3's real winners from its noise")
    p.add_argument("--baseline", required=True)
    p.add_argument("--exp3", required=True)
    p.add_argument("--cache-dir", default="data/replay-datasets/backtest")
    p.add_argument("--output", required=True)
    p.add_argument("--match-window", type=float, default=30.0, help="seconds within which a baseline actionable finding counts as 'already caught this'")
    args = p.parse_args()

    baseline_rows = load_jsonl(Path(args.baseline))
    exp3_rows = load_jsonl(Path(args.exp3))
    baseline_by_key = group(baseline_rows)
    cache_dir = Path(args.cache_dir)

    incremental: list[dict[str, Any]] = []
    for row in exp3_rows:
        ticker, date = row["ticker"], row["date"]
        key = (ticker, date)
        for f in row.get("findings") or []:
            if not actionable(f):
                continue
            baseline_findings = baseline_by_key.get(key) or []
            baseline_actionable_near = [
                bf for br in baseline_findings for bf in (br.get("findings") or [])
                if actionable(bf) and abs(float(bf["detected_at"]) - float(f["detected_at"])) <= args.match_window
            ]
            if baseline_actionable_near:
                continue  # baseline already caught something comparable here -- not incremental
            incremental.append({"ticker": ticker, "date": date, "finding": f})

    print(f"Incremental actionable findings (exp3 admits, baseline never caught nearby): {len(incremental)}")

    candle_cache: dict[tuple[str, str], list] = {}
    labeled: list[dict[str, Any]] = []
    for row in incremental:
        ticker, date, f = row["ticker"], row["date"], row["finding"]
        key = (ticker, date)
        if key not in candle_cache:
            candle_cache[key] = build_candles(cache_dir, ticker, date, settings.alpaca_feed)
        candles = candle_cache[key]
        if not candles:
            continue
        metrics = forward_metrics(candles, float(f["detected_at"]), float(f["price"]))
        cov = coverage(metrics)
        if cov == "UNMATURED":
            continue
        cls = classification(metrics)
        family = str(cls).removeprefix("PROVISIONAL_")
        bucket = "WINNER" if family in {"EARLY", "USEFUL"} else "JUNK" if family in {"FALSE_POSITIVE", "FADE"} else "AMBIGUOUS"
        labeled.append({**f, "ticker": ticker, "date": date, "outcome_bucket": bucket, "classification": cls, **metrics})

    winners = [r for r in labeled if r["outcome_bucket"] == "WINNER"]
    junk = [r for r in labeled if r["outcome_bucket"] == "JUNK"]
    ambiguous = [r for r in labeled if r["outcome_bucket"] == "AMBIGUOUS"]
    print(f"Labeled: {len(labeled)} (winners={len(winners)} junk={len(junk)} ambiguous={len(ambiguous)})")

    numeric_features = [
        "quality_score", "vol_ratio_15s", "vol_ratio_30s", "change_5s_pct", "change_15s_pct",
        "change_30s_pct", "accel_15s_pp", "dollar_volume_15s", "dollar_volume_30s",
        "trades_15s", "trades_30s", "directional_efficiency", "active_bucket_ratio", "direction_reversals",
    ]

    def stats(rows: list[dict], field: str) -> dict:
        vals = [safe_float(r.get(field)) for r in rows]
        vals = [v for v in vals if v is not None]
        if not vals:
            return {"n": 0, "mean": None, "median": None}
        return {"n": len(vals), "mean": round(statistics.mean(vals), 4), "median": round(statistics.median(vals), 4)}

    comparison = {}
    for feat in numeric_features:
        w = stats(winners, feat)
        j = stats(junk, feat)
        comparison[feat] = {"winners": w, "junk": j}

    bool_features = ["above_vwap", "quiet_break"]
    for feat in bool_features:
        w_rate = sum(1 for r in winners if r.get(feat)) / len(winners) if winners else None
        j_rate = sum(1 for r in junk if r.get(feat)) / len(junk) if junk else None
        comparison[feat] = {"winners_rate": w_rate, "junk_rate": j_rate}

    winner_rejections = Counter(reason for r in winners for reason in (r.get("rejection_reasons") or []))
    junk_rejections = Counter(reason for r in junk for reason in (r.get("rejection_reasons") or []))
    winner_stages = Counter(r.get("stage") for r in winners)
    junk_stages = Counter(r.get("stage") for r in junk)

    report = {
        "incremental_total": len(incremental),
        "labeled_total": len(labeled),
        "winners": len(winners), "junk": len(junk), "ambiguous": len(ambiguous),
        "feature_comparison": comparison,
        "winner_rejection_reasons_still_present": dict(winner_rejections),
        "junk_rejection_reasons_still_present": dict(junk_rejections),
        "winner_stages": dict(winner_stages), "junk_stages": dict(junk_stages),
    }
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\n=== FEATURE COMPARISON: winners vs junk (mean / median) ===")
    for feat, vals in comparison.items():
        if "winners" in vals and isinstance(vals["winners"], dict) and "mean" in vals["winners"]:
            w, j = vals["winners"], vals["junk"]
            print(f"  {feat:28s} winners: mean={w['mean']} median={w['median']} (n={w['n']})  |  junk: mean={j['mean']} median={j['median']} (n={j['n']})")
        else:
            print(f"  {feat:28s} winners_rate={vals.get('winners_rate')} junk_rate={vals.get('junk_rate')}")
    print(f"\nJSON report: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

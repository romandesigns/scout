#!/usr/bin/env python3
"""
Scout Historical Backtest Scorer (v6.7.4 backtest instrumentation)

Purpose
-------
Joins:
  1. historical_mover_finder.py's detector-blind ground truth (which tickers
     really moved, and when each +5/10/20/50% threshold was first crossed),
  2. historical_backtest.py's replay of Scout's real detector against the
     matching historical tick data,
to answer two questions with an actual sample size:
  - RECALL: of tickers that really moved, what fraction did Scout see at
    all, and see/promote to actionable *before* each threshold crossed?
  - PRECISION: of everything Scout's replayed detector actually flagged
    (movers + control/non-mover sample), what fraction of forward outcomes
    were clean/useful vs false-positive/fade vs ambiguous, using the same
    classification as scripts/detection_quality.py?

Usage
-----
python -m scripts.backtest_scorer --movers data/optimization/backtest/movers-*.jsonl \
    --findings data/optimization/backtest/findings.jsonl \
    --output data/optimization/backtest/backtest-report.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.config import settings
from app.replay import load_events
from scripts.detection_quality import (
    actionable, classification, coverage, forward_metrics, safe_float,
)

THRESHOLDS = (5.0, 10.0, 20.0, 50.0)
CANDLE_SECONDS = 15


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def build_candles(cache_dir: Path, ticker: str, day: str, feed: str) -> list[dict[str, Any]]:
    """Bucket the cached tick-level replay dataset into CANDLE_SECONDS candles
    so we can reuse detection_quality.forward_metrics unmodified."""
    dataset = cache_dir / f"{ticker}-{day}-{feed}.ndjson"
    if not dataset.exists():
        return []
    events, _ = load_events(dataset)
    buckets: dict[int, list[float]] = defaultdict(list)
    for event in events:
        if event.event_type != "trade":
            continue
        bucket = int(event.source_ts // CANDLE_SECONDS) * CANDLE_SECONDS
        buckets[bucket].append(float(event.payload["price"]))
    candles = []
    for start_ts in sorted(buckets):
        prices = buckets[start_ts]
        candles.append({
            "start_ts": float(start_ts), "open": prices[0], "high": max(prices),
            "low": min(prices), "close": prices[-1],
        })
    return candles


def score_precision(replay_rows: list[dict[str, Any]], cache_dir: Path, feed: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for row in replay_rows:
        findings = row.get("findings") or []
        if not findings:
            continue
        candles = build_candles(cache_dir, row["ticker"], row["date"], feed)
        if not candles:
            continue
        for f in findings:
            metrics = forward_metrics(candles, float(f["detected_at"]), float(f["price"]))
            cov = coverage(metrics)
            cls = classification(metrics) if cov != "UNMATURED" else "UNMATURED"
            rows.append({
                "ticker": row["ticker"], "date": row["date"], "stage": f.get("stage"),
                "actionable_rank": f.get("actionable_rank"), "quality_label": f.get("quality_label"),
                "is_actionable": actionable(f), "coverage": cov, "classification": cls,
                **{k: v for k, v in metrics.items() if k not in {"first_ts", "last_ts"}},
            })

    actionable_rows = [r for r in rows if r["is_actionable"] and r["coverage"] != "UNMATURED"]
    families = Counter(str(r["classification"]).removeprefix("PROVISIONAL_").split("_")[0] for r in actionable_rows)
    useful = sum(1 for r in actionable_rows if str(r["classification"]).removeprefix("PROVISIONAL_") in {"EARLY", "USEFUL"})
    false_fade = sum(1 for r in actionable_rows if str(r["classification"]).removeprefix("PROVISIONAL_") in {"FALSE_POSITIVE", "FADE"})
    n = len(actionable_rows)
    return {
        "total_findings_scored": len(rows),
        "actionable_findings_scored": n,
        "useful_rate": (useful / n) if n else None,
        "false_or_fade_rate": (false_fade / n) if n else None,
        "ambiguous_rate": ((n - useful - false_fade) / n) if n else None,
        "classification_family_counts": dict(families),
        "rows": rows,
    }


def score_recall(mover_rows: list[dict[str, Any]], replay_by_key: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    detail: list[dict[str, Any]] = []
    not_attempted = 0
    for m in mover_rows:
        if not m.get("is_mover"):
            continue
        key = (m["ticker"], m["date"])
        replay = replay_by_key.get(key)
        if replay is None:
            # Never replayed (e.g. excluded by a sampling pass) -- must NOT be
            # scored as "missed", or recall would be silently deflated by
            # whatever fraction of ground truth was never attempted.
            not_attempted += 1
            continue
        findings = sorted((replay or {}).get("findings") or [], key=lambda f: float(f["detected_at"]))
        first_seen = findings[0] if findings else None
        first_actionable = next((f for f in findings if actionable(f)), None)

        row_detail = {
            "ticker": m["ticker"], "date": m["date"], "max_pct": m.get("max_pct"),
            "reference_price": m.get("reference_price"),
            "first_scout_at": first_seen["detected_at"] if first_seen else None,
            "first_scout_stage": first_seen["stage"] if first_seen else None,
            "first_actionable_at": first_actionable["detected_at"] if first_actionable else None,
            "thresholds": {},
        }
        for threshold in THRESHOLDS:
            crossing = (m.get("crossings") or {}).get(str(int(threshold)))
            if not crossing:
                continue
            c_at = float(crossing["at"])
            row_detail["thresholds"][str(int(threshold))] = {
                "crossed_at": c_at,
                "seen_before_cross": bool(first_seen and float(first_seen["detected_at"]) <= c_at),
                "actionable_before_cross": bool(first_actionable and float(first_actionable["detected_at"]) <= c_at),
            }
        detail.append(row_detail)

    summary: dict[str, Any] = {}
    for threshold in THRESHOLDS:
        key = str(int(threshold))
        cohort = [r for r in detail if key in r["thresholds"]]
        seen = [r for r in cohort if r["first_scout_at"] is not None]
        seen_before = [r for r in cohort if r["thresholds"][key]["seen_before_cross"]]
        actionable_before = [r for r in cohort if r["thresholds"][key]["actionable_before_cross"]]
        summary[key] = {
            "movers": len(cohort),
            "scout_seen_rate": (len(seen) / len(cohort)) if cohort else None,
            "seen_before_cross_rate": (len(seen_before) / len(cohort)) if cohort else None,
            "actionable_before_cross_rate": (len(actionable_before) / len(cohort)) if cohort else None,
        }

    missed = [r for r in detail if r["first_scout_at"] is None]
    missed.sort(key=lambda r: r.get("max_pct") or 0, reverse=True)
    return {"threshold_recall": summary, "mover_count": len(detail), "not_attempted": not_attempted,
            "largest_missed_movers": missed[:25], "rows": detail}


def main() -> int:
    p = argparse.ArgumentParser(description="Score Scout's historical backtest recall + precision")
    p.add_argument("--movers", required=True)
    p.add_argument("--findings", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cache-dir", default="data/replay-datasets/backtest")
    args = p.parse_args()

    mover_rows = load_jsonl(Path(args.movers))
    replay_rows = load_jsonl(Path(args.findings))
    replay_by_key = {(r["ticker"], r["date"]): r for r in replay_rows}

    recall = score_recall(mover_rows, replay_by_key)
    precision = score_precision(replay_rows, Path(args.cache_dir), settings.alpaca_feed)

    report = {
        "movers_scanned": sum(1 for r in mover_rows if r.get("is_mover")),
        "control_scanned": sum(1 for r in mover_rows if not r.get("is_mover")),
        "replayed": len(replay_rows),
        "recall": recall,
        "precision": precision,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("=" * 100)
    print("SCOUT HISTORICAL BACKTEST REPORT")
    print("=" * 100)
    print(f"movers_scanned={report['movers_scanned']} control_scanned={report['control_scanned']} replayed={report['replayed']} "
          f"movers_not_attempted={recall['not_attempted']}")
    print("\nRECALL (real historical movers, detector-blind ground truth; denominator = attempted/replayed movers only)")
    for threshold in THRESHOLDS:
        s = recall["threshold_recall"].get(str(int(threshold)), {})
        def pct(v): return "n/a" if v is None else f"{v:.1%}"
        print(f"  +{int(threshold):>2}% movers={s.get('movers',0):>4} seen={pct(s.get('scout_seen_rate')):>7} "
              f"seen-before-cross={pct(s.get('seen_before_cross_rate')):>7} actionable-before-cross={pct(s.get('actionable_before_cross_rate')):>7}")
    print("\nPRECISION (actionable-rank findings from the same replayed sample)")
    print(f"  n={precision['actionable_findings_scored']} useful_rate={precision['useful_rate']} "
          f"false_or_fade_rate={precision['false_or_fade_rate']} ambiguous_rate={precision['ambiguous_rate']}")
    print(f"\nJSON report: {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

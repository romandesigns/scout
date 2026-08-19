#!/usr/bin/env python3
"""
Scout Reversal Detection Scorer (v6.7.4 backtest instrumentation)

Joins reversal_ground_truth.py's detector-blind episodes with historical_backtest.py's
replay of Scout's real detector, to measure whether REVERSAL_WATCH / RECLAIM / EMA_RECLAIM /
VWAP_RECLAIM actually fire -- and how early relative to the ground-truth watch/reclaim
crossings -- mirroring backtest_scorer.py's recall methodology for upward moves.

Usage
-----
python -m scripts.reversal_scorer --reversals data/optimization/backtest/reversals-sample.jsonl \
    --findings data/optimization/backtest/findings-reversal-sample.jsonl \
    --output data/optimization/backtest/reversal-report.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REVERSAL_STAGES = {"REVERSAL_WATCH", "RECLAIM", "EMA_RECLAIM", "VWAP_RECLAIM", "REARM", "FIRST_PULLBACK"}


def actionable(f: dict) -> bool:
    return str(f.get("actionable_rank") or "").upper() in {"A", "B"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    p = argparse.ArgumentParser(description="Score Scout's reversal-family stages against detector-blind ground truth")
    p.add_argument("--reversals", required=True)
    p.add_argument("--findings", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    episodes = load_jsonl(Path(args.reversals))
    replay_by_key = {(r["ticker"], r["date"]): r for r in load_jsonl(Path(args.findings))}

    detail = []
    not_attempted = 0
    for ep in episodes:
        key = (ep["ticker"], ep["date"])
        replay = replay_by_key.get(key)
        if replay is None:
            not_attempted += 1
            continue
        findings = sorted(replay.get("findings") or [], key=lambda f: float(f["detected_at"]))
        reversal_findings = [f for f in findings if f.get("stage") in REVERSAL_STAGES]
        watch_ts = float(ep["watch_crossed_at"])
        reclaim_ts = float(ep["reclaim_crossed_at"]) if "reclaim_crossed_at" in ep else None

        first_any = reversal_findings[0] if reversal_findings else None
        first_actionable = next((f for f in reversal_findings if actionable(f)), None)

        row = {
            "ticker": ep["ticker"], "date": ep["date"], "drawdown_pct": ep["drawdown_pct"],
            "has_reclaim_bar": reclaim_ts is not None,
            "first_reversal_finding_at": first_any["detected_at"] if first_any else None,
            "first_reversal_finding_stage": first_any["stage"] if first_any else None,
            "first_actionable_reversal_at": first_actionable["detected_at"] if first_actionable else None,
            "seen_before_watch_cross": bool(first_any and float(first_any["detected_at"]) <= watch_ts),
            "actionable_before_watch_cross": bool(first_actionable and float(first_actionable["detected_at"]) <= watch_ts),
        }
        if reclaim_ts is not None:
            row["seen_before_reclaim_cross"] = bool(first_any and float(first_any["detected_at"]) <= reclaim_ts)
            row["actionable_before_reclaim_cross"] = bool(first_actionable and float(first_actionable["detected_at"]) <= reclaim_ts)
        detail.append(row)

    n = len(detail)
    reclaim_rows = [r for r in detail if r["has_reclaim_bar"]]
    seen = sum(1 for r in detail if r["first_reversal_finding_at"] is not None)
    seen_before_watch = sum(1 for r in detail if r["seen_before_watch_cross"])
    actionable_before_watch = sum(1 for r in detail if r["actionable_before_watch_cross"])
    seen_before_reclaim = sum(1 for r in reclaim_rows if r.get("seen_before_reclaim_cross"))
    actionable_before_reclaim = sum(1 for r in reclaim_rows if r.get("actionable_before_reclaim_cross"))

    summary = {
        "episodes_scanned": len(episodes), "episodes_replayed": n, "not_attempted": not_attempted,
        "watch_tier": {
            "n": n,
            "scout_seen_rate": seen / n if n else None,
            "seen_before_cross_rate": seen_before_watch / n if n else None,
            "actionable_before_cross_rate": actionable_before_watch / n if n else None,
        },
        "reclaim_tier": {
            "n": len(reclaim_rows),
            "seen_before_cross_rate": seen_before_reclaim / len(reclaim_rows) if reclaim_rows else None,
            "actionable_before_cross_rate": actionable_before_reclaim / len(reclaim_rows) if reclaim_rows else None,
        },
        "missed_entirely": [r for r in detail if r["first_reversal_finding_at"] is None][:25],
        "rows": detail,
    }
    Path(args.output).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("=" * 100)
    print("SCOUT REVERSAL DETECTION REPORT")
    print("=" * 100)
    print(f"episodes_scanned={summary['episodes_scanned']} replayed={n} not_attempted={not_attempted}")
    def pct(v): return "n/a" if v is None else f"{v:.1%}"
    w = summary["watch_tier"]
    print(f"WATCH bar (>=0.75% bounce)   n={w['n']:4d} seen={pct(w['scout_seen_rate'])} seen-before-cross={pct(w['seen_before_cross_rate'])} actionable-before-cross={pct(w['actionable_before_cross_rate'])}")
    rc = summary["reclaim_tier"]
    print(f"RECLAIM bar (>=2.0% bounce)  n={rc['n']:4d} seen-before-cross={pct(rc['seen_before_cross_rate'])} actionable-before-cross={pct(rc['actionable_before_cross_rate'])}")
    print(f"\nJSON report: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

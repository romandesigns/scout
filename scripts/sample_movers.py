#!/usr/bin/env python3
"""
Scout Backtest Sampler (v6.7.4 backtest instrumentation)

Purpose
-------
Stage 1 (historical_mover_finder.py) is cheap: it scans the full tradable
universe for a whole date range in roughly a minute per trading day. Stage 2
(historical_backtest.py) is expensive: it replays real tick-level trades
through the actual detector for every candidate, which does not scale to
"every mover, every day."

This draws an unbiased, seeded, stratified random sample from a ground-truth
movers file -- capping how many rows get the expensive replay treatment per
magnitude tier (so rare +20%/+50% events aren't drowned out by the much more
common +5% ones) while keeping the sample statistically fair to replay.

Rows that are NOT selected are simply absent from the output; the scorer
distinguishes "not attempted" from "attempted but not seen" and only computes
recall over rows that were actually replayed.

Usage
-----
python -m scripts.sample_movers --input data/optimization/backtest/movers-all.jsonl \
    --output data/optimization/backtest/movers-sample.jsonl \
    --cap-per-tier 60 --control-cap 150
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

TIERS = (50, 20, 10, 5)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def highest_tier(row: dict[str, Any]) -> int | None:
    crossings = row.get("crossings") or {}
    for tier in TIERS:
        if str(tier) in crossings:
            return tier
    return None


def main() -> int:
    p = argparse.ArgumentParser(description="Stratified random sample of a ground-truth movers file, bounded for replay cost")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--cap-per-tier", type=int, default=60, help="Max sampled movers per highest-tier-reached bucket")
    p.add_argument("--control-cap", type=int, default=150, help="Max sampled non-mover control rows")
    p.add_argument("--seed", default="scout-backtest-sample")
    args = p.parse_args()

    rows = load_jsonl(Path(args.input))
    movers = [r for r in rows if r.get("is_mover")]
    controls = [r for r in rows if not r.get("is_mover")]

    by_tier: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in movers:
        tier = highest_tier(row)
        if tier is not None:
            by_tier[tier].append(row)

    rng = random.Random(args.seed)
    sampled: list[dict[str, Any]] = []
    for tier in TIERS:
        pool = by_tier.get(tier, [])
        take = pool if len(pool) <= args.cap_per_tier else rng.sample(pool, args.cap_per_tier)
        sampled.extend(take)
        print(f"tier +{tier}%: population={len(pool)} sampled={len(take)}")

    control_take = controls if len(controls) <= args.control_cap else rng.sample(controls, args.control_cap)
    print(f"control: population={len(controls)} sampled={len(control_take)}")
    sampled.extend(control_take)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in sampled:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(f"Wrote {len(sampled)} sampled rows ({len(sampled) - len(control_take)} movers + {len(control_take)} control) -> {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

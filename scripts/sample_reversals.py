#!/usr/bin/env python3
"""Stratified bounded sample of reversal ground truth, mirroring sample_movers.py.
Prioritizes episodes that reached the confirmed-reclaim bar (more meaningful test of
whether Scout's RECLAIM stages fire) while keeping some watch-only episodes for a fuller
picture, bounded so the expensive replay stage stays tractable."""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, nargs="+")
    p.add_argument("--output", required=True)
    p.add_argument("--reclaim-cap", type=int, default=150)
    p.add_argument("--watch-only-cap", type=int, default=90)
    p.add_argument("--seed", default="scout-reversal-sample")
    args = p.parse_args()

    rows = []
    for path in args.input:
        rows.extend(json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip())

    reclaim = [r for r in rows if "reclaim_crossed_at" in r]
    watch_only = [r for r in rows if "reclaim_crossed_at" not in r]
    rng = random.Random(args.seed)
    seen_keys = set()
    sampled = []
    for pool, cap in ((reclaim, args.reclaim_cap), (watch_only, args.watch_only_cap)):
        take = pool if len(pool) <= cap else rng.sample(pool, cap)
        for row in take:
            key = (row["ticker"], row["date"])
            if key in seen_keys:
                continue  # one episode per (ticker, date) for replay purposes
            seen_keys.add(key)
            sampled.append(row)

    print(f"reclaim population={len(reclaim)} watch-only population={len(watch_only)} sampled={len(sampled)} unique tickers/days")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in sampled:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    print(f"Wrote {len(sampled)} rows -> {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

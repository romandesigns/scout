#!/usr/bin/env python3
"""
Scout EXPERIMENT_REENTRY_VWAP_SAFETY_GATE Retroactive Validation (2026-08-19)

Purpose
-------
Can't re-run today's already-closed session with the new flag enabled, so instead: re-fetch
today's actionable (rank A/B) findings with full fields (vwap, stage), identify which ones
the new gate WOULD have blocked (REARM/VWAP_RECLAIM/EMA_RECLAIM stage + meaningfully below
VWAP), and join that against the already-computed real forward outcomes in
live-full-day-report.json to see whether removing those specific findings measurably
improves the aggregate precision number. Uses today's actual live data, not a hypothesis.

Usage
-----
python -m scripts.validate_vwap_safety_gate --report data/optimization/backtest/live-full-day-report.json
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://srv1170872.tail86523.ts.net:8444"
REENTRY_STAGES = {"REARM", "VWAP_RECLAIM", "EMA_RECLAIM"}
MAX_BELOW_VWAP_PCT = 2.0  # matches settings.reentry_max_below_vwap_pct default
MAX_ABOVE_VWAP_PCT = 3.0  # matches settings.reentry_max_above_vwap_pct default


def fetch_findings_for_ids(ids: set[int]) -> dict[int, dict]:
    """/api/findings/{id} gives full fields including vwap/above_vwap; batch via individual
    calls since there's no bulk-by-id endpoint. ids come from the precision report so the
    count is bounded to today's actionable findings only (a few hundred, not the full day)."""
    out = {}
    for i, fid in enumerate(sorted(ids), 1):
        r = requests.get(f"{BASE}/api/findings/{fid}", timeout=15)
        if r.status_code == 200:
            out[fid] = r.json()
        if i % 50 == 0:
            print(f"  fetched {i}/{len(ids)}")
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report", required=True)
    args = p.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    rows = report["precision"]["rows"]
    ids = {r["finding_id"] for r in rows if r.get("finding_id") is not None}
    print(f"Precision report has {len(rows)} scored actionable findings ({len(ids)} unique ids)")
    print("Re-fetching full finding records (need vwap/above_vwap, not in the summary rows)...")
    full = fetch_findings_for_ids(ids)
    print(f"Fetched {len(full)} full records")

    blocked_net = []
    kept_net = []
    reentry_net = []
    for r in rows:
        fid = r.get("finding_id")
        full_rec = full.get(fid)
        if not full_rec:
            continue
        stage = str(full_rec.get("stage") or "").upper()
        vwap = full_rec.get("vwap")
        price = full_rec.get("price")
        net = r["net_opportunity_pct"]
        if stage in REENTRY_STAGES:
            reentry_net.append(net)
        if stage in REENTRY_STAGES and vwap and price:
            vwap_gap_pct = (float(price) / float(vwap) - 1.0) * 100.0
            if vwap_gap_pct < -MAX_BELOW_VWAP_PCT or vwap_gap_pct > MAX_ABOVE_VWAP_PCT:
                reason = "deeply_below_vwap" if vwap_gap_pct < 0 else "chasing_above_vwap"
                blocked_net.append({"ticker": r["ticker"], "stage": stage, "vwap_gap_pct": round(vwap_gap_pct, 2), "net_opportunity_pct": net, "reason": reason})
                continue
        kept_net.append(net)

    print(f"\nAll REARM/VWAP_RECLAIM/EMA_RECLAIM findings today: {len(reentry_net)}")
    if reentry_net:
        print(f"  mean={statistics.mean(reentry_net):.3f} sum={sum(reentry_net):.1f} positive_rate={sum(1 for v in reentry_net if v>0)/len(reentry_net):.1%}")

    print(f"\nFindings the new gate WOULD block (stage in reentry set AND >{MAX_BELOW_VWAP_PCT}% below VWAP): {len(blocked_net)}")
    if blocked_net:
        vals = [b["net_opportunity_pct"] for b in blocked_net]
        print(f"  their mean net_opportunity_pct={statistics.mean(vals):.3f}  sum={sum(vals):.1f}  positive_rate={sum(1 for v in vals if v>0)/len(vals):.1%}")
        print("  full blocked list, sorted by net_opportunity_pct:")
        for b in sorted(blocked_net, key=lambda x: x["net_opportunity_pct"]):
            print(f"    {b['ticker']:8s} {b['stage']:14s} {b['reason']:20s} vwap_gap={b['vwap_gap_pct']:+.2f}%  net_opp={b['net_opportunity_pct']:+.3f}")

    all_net = [r["net_opportunity_pct"] for r in rows]
    print(f"\n--- Aggregate comparison ---")
    print(f"BEFORE (all {len(all_net)} actionable findings today):  mean={statistics.mean(all_net):.3f}  sum={sum(all_net):.1f}  positive_rate={sum(1 for v in all_net if v>0)/len(all_net):.1%}")
    if kept_net:
        print(f"AFTER  (excluding the {len(blocked_net)} blocked):        mean={statistics.mean(kept_net):.3f}  sum={sum(kept_net):.1f}  positive_rate={sum(1 for v in kept_net if v>0)/len(kept_net):.1%}  n={len(kept_net)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

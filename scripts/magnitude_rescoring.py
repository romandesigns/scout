#!/usr/bin/env python3
"""
Scout Magnitude-Weighted Rescoring (2026-08-19 follow-up)

Purpose
-------
Every precision number reported this week used a flat classification (USEFUL/FALSE_POSITIVE/
FADE/MIXED) that treats a +50% monster caught at +8% identically to a +2% pop that barely
clears the bar. This re-scores the SAME already-computed outcome data (no new data
collection -- every report already has mfe_300s_pct/mae_300s_pct per actionable finding)
against a magnitude-aware objective, to see whether any of this week's conclusions change
under a better yardstick.

Objective: net_opportunity_pct = mfe_300s_pct + mae_300s_pct (mae is already signed negative,
so this is "upside available minus downside risk taken" in percentage points) -- a simple,
interpretable per-finding value that a flat classification collapses away.

Usage
-----
python -m scripts.magnitude_rescoring
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

REPORTS_DIR = Path("data/optimization/backtest")
REPORTS = [
    "report-6day-sample.json", "report-hybrid.json",
    "report-coord-v1.json", "report-coord-v2.json", "report-hybrid-v2.json",
    "report-exp1.json", "report-exp2.json", "report-exp3.json", "report-exp4.json", "report-exp123.json",
    "report-exp5.json", "report-exp6.json",
]


def load(name: str) -> dict | None:
    path = REPORTS_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    print(f"{'report':30s} {'n':>5s} {'useful%':>8s} {'ambig%':>8s} | {'net_opp_mean':>13s} {'net_opp_median':>15s} {'net_opp_sum':>12s} {'positive_net_rate':>18s}")
    results = []
    for name in REPORTS:
        data = load(name)
        if not data:
            continue
        rows = data.get("precision", {}).get("rows", [])
        actionable_rows = [r for r in rows if r.get("is_actionable") and r.get("coverage") != "UNMATURED"]
        net_vals = []
        for r in actionable_rows:
            mfe = r.get("mfe_300s_pct")
            mae = r.get("mae_300s_pct")
            if mfe is None or mae is None:
                continue
            net_vals.append(mfe + mae)
        if not net_vals:
            continue
        useful = data.get("precision", {}).get("useful_rate")
        ambiguous = data.get("precision", {}).get("ambiguous_rate")
        mean_v = statistics.mean(net_vals)
        median_v = statistics.median(net_vals)
        sum_v = sum(net_vals)
        pos_rate = sum(1 for v in net_vals if v > 0) / len(net_vals)
        results.append({
            "report": name, "n": len(net_vals),
            "useful_rate": useful, "ambiguous_rate": ambiguous,
            "net_opportunity_mean_pct": round(mean_v, 3), "net_opportunity_median_pct": round(median_v, 3),
            "net_opportunity_sum_pct": round(sum_v, 1), "positive_net_rate": round(pos_rate, 3),
        })
        u = f"{useful:.1%}" if useful is not None else "n/a"
        a = f"{ambiguous:.1%}" if ambiguous is not None else "n/a"
        print(f"{name:30s} {len(net_vals):5d} {u:>8s} {a:>8s} | {mean_v:13.3f} {median_v:15.3f} {sum_v:12.1f} {pos_rate:18.1%}")

    out = REPORTS_DIR / "magnitude-rescoring-report.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nJSON report: {out.resolve()}")


if __name__ == "__main__":
    main()

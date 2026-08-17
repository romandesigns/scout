from __future__ import annotations

import argparse
import json
import os
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def fetch_json(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def promotion_trace(row: dict[str, Any]) -> dict[str, Any] | None:
    profile = row.get("candidate_profile") or {}
    trace = profile.get("promotion_trace") if isinstance(profile, dict) else None
    return trace if isinstance(trace, dict) else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    blockers = Counter()
    stages = Counter()
    labels = Counter()
    traced = []
    late_risk = []
    promoted = []
    delays: list[float] = []
    rejection = Counter()

    for row in rows:
        trace = promotion_trace(row)
        if not trace:
            continue
        traced.append(row)
        stages[str(row.get("stage") or "UNKNOWN")] += 1
        labels[str(row.get("quality_label") or "UNKNOWN")] += 1
        for blocker in trace.get("blockers") or []:
            blockers[str(blocker)] += 1
        for reason in trace.get("rejection_reasons") or row.get("rejection_reasons") or []:
            rejection[str(reason)] += 1
        if trace.get("late_risk"):
            late_risk.append(row)
        if trace.get("promoted") or str(row.get("actionable_rank") or "C").upper() in {"A", "B"}:
            promoted.append(row)
            delay = trace.get("promotion_delay_seconds")
            try:
                if delay is not None:
                    delays.append(float(delay))
            except Exception:
                pass

    avg_delay = sum(delays) / len(delays) if delays else None
    return {
        "rows": len(rows),
        "traced": len(traced),
        "untraced": len(rows) - len(traced),
        "promoted": len(promoted),
        "late_risk": len(late_risk),
        "average_promotion_delay_seconds": avg_delay,
        "top_blockers": blockers.most_common(15),
        "top_rejection_reasons": rejection.most_common(15),
        "stages": dict(stages),
        "quality_labels": dict(labels),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Audit Scout Developing -> actionable promotion gates")
    p.add_argument("--api-base", default=os.getenv("SCOUT_API_BASE", "https://srv1170872.tail86523.ts.net:8444"))
    p.add_argument("--limit", type=int, default=300)
    p.add_argument("--output-prefix", default="promotion-trace")
    args = p.parse_args()

    base = args.api_base.rstrip("/")
    payload = fetch_json(f"{base}/api/findings?limit={max(20, min(500, args.limit))}", timeout=40)
    rows = payload.get("items", payload if isinstance(payload, list) else [])
    summary = summarize(rows)

    print("=" * 94)
    print("SCOUT PROMOTION GATE TRACE")
    print(f"API={base} rows={summary['rows']} traced={summary['traced']} promoted={summary['promoted']}")
    print("=" * 94)
    if not summary["traced"]:
        print("No v6.6.0 promotion traces found yet. Let production accumulate new findings, then rerun.")
    else:
        print(f"late-risk traces={summary['late_risk']} average promotion delay={summary['average_promotion_delay_seconds']}")
        print("\nTOP BLOCKERS")
        for name, count in summary["top_blockers"]:
            print(f"  {count:4d}  {name}")
        print("\nQUALITY REJECTION REASONS")
        for name, count in summary["top_rejection_reasons"]:
            print(f"  {count:4d}  {name}")
        print("\nSTAGES")
        for name, count in sorted(summary["stages"].items(), key=lambda item: (-item[1], item[0])):
            print(f"  {count:4d}  {name}")

        print("\nLATE-RISK / BLOCKED CANDIDATES")
        shown = 0
        for row in rows:
            trace = promotion_trace(row)
            if not trace or not trace.get("late_risk"):
                continue
            print(
                f"  {row.get('ticker','?'):6} {str(row.get('stage') or ''):18} rank={row.get('actionable_rank')} "
                f"base_ext={trace.get('base_extension_pct')} ext={trace.get('extension_pct')} "
                f"age={trace.get('candidate_age_seconds')} blocker={trace.get('next_blocker')}"
            )
            shown += 1
            if shown >= 25:
                break

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api_base": base,
        "summary": summary,
        "rows": [
            {
                "id": row.get("id"), "ticker": row.get("ticker"), "stage": row.get("stage"),
                "detected_at": row.get("detected_at"), "price": row.get("price"),
                "actionable_rank": row.get("actionable_rank"), "quality_label": row.get("quality_label"),
                "quality_score": row.get("quality_score"), "timeliness_label": row.get("timeliness_label"),
                "promotion_trace": promotion_trace(row),
            }
            for row in rows if promotion_trace(row)
        ],
    }
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(f"{args.output_prefix}-{stamp}.json")
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("=" * 94)
    print(f"Report: {path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

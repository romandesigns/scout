#!/usr/bin/env python3
"""Score Scout alerts against short-horizon objective price expansions.

An objective move is a configurable percentage rise from a rolling local low,
completed inside the rolling horizon.  A hit requires an actionable Scout
finding in the requested lead-time window before that completion.  This keeps
old, unrelated findings from receiving "early" credit.
"""
from __future__ import annotations

import argparse
import json
from bisect import bisect_left, bisect_right
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_trades(path: Path) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    for value in load_jsonl(path):
        if str(value.get("event_type", "")).lower() != "trade":
            continue
        ts = float(value.get("source_ts") or 0)
        price = float((value.get("payload") or {}).get("price") or 0)
        if ts > 0 and price > 0:
            rows.append((ts, price))
    rows.sort()
    return rows


def objective_moves(
    ticker: str,
    trades: list[tuple[float, float]],
    *,
    expansion_pct: float = 2.0,
    horizon_seconds: float = 60.0,
    dedupe_seconds: float = 60.0,
) -> list[dict[str, Any]]:
    """Find detector-independent expansion completions from a rolling local low."""
    # Monotonic queue: the left edge is always the earliest lowest price in
    # the active horizon.  This preserves min(rolling)'s tie behavior while
    # reducing high-volume sessions from O(trades * window_trades) to O(trades).
    rolling_min: deque[tuple[float, float]] = deque()
    last_completion = float("-inf")
    moves: list[dict[str, Any]] = []
    multiplier = 1.0 + expansion_pct / 100.0
    for ts, price in trades:
        while rolling_min and rolling_min[-1][1] > price:
            rolling_min.pop()
        rolling_min.append((ts, price))
        cutoff = ts - horizon_seconds
        while rolling_min and rolling_min[0][0] < cutoff:
            rolling_min.popleft()
        base_at, base_price = rolling_min[0]
        if price >= base_price * multiplier and ts - last_completion >= dedupe_seconds:
            moves.append({
                "ticker": ticker,
                "base_at": base_at,
                "base_price": base_price,
                "completed_at": ts,
                "completed_price": price,
                "duration_seconds": round(ts - base_at, 6),
            })
            last_completion = ts
    return moves


def is_actionable(finding: dict[str, Any]) -> bool:
    if finding.get("shadow_mode"):
        return False
    if str(finding.get("actionable_rank") or "C").upper() != "A":
        return False
    if str(finding.get("quality_label") or "").upper() != "CLEAN":
        return False
    profile = finding.get("candidate_profile") or {}
    multi_timeframe = profile.get("multi_timeframe") or {}
    if multi_timeframe and multi_timeframe.get("qualified") is not True:
        return False
    timely = str(finding.get("timeliness_label") or "").upper()
    extension = max(
        float(finding.get("extension_pct") or 0.0),
        float(finding.get("base_extension_at_detection_pct") or 0.0),
    )
    return timely not in {"LATE", "TOO_LATE", "EXTENDED", "LATE_RISK"} and extension < 8.0


def score(
    replay_rows: list[dict[str, Any]],
    cache_dir: Path,
    *,
    feed: str = "sip",
    expansion_pct: float = 2.0,
    horizon_seconds: float = 60.0,
    lead_min_seconds: float = 15.0,
    lead_max_seconds: float = 30.0,
    dedupe_seconds: float = 60.0,
) -> dict[str, Any]:
    findings_by_symbol_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in replay_rows:
        ticker = str(row["ticker"]).upper()
        session_date = str(row["date"])
        findings_by_symbol_day[(ticker, session_date)].extend(row.get("findings") or [])

    moves: list[dict[str, Any]] = []
    actionable_by_symbol_day: dict[tuple[str, str], list[float]] = {}
    for (ticker, session_date), findings in findings_by_symbol_day.items():
        dataset = cache_dir / f"{ticker}-{session_date}-{feed}.ndjson"
        if not dataset.exists():
            continue
        ticker_moves = objective_moves(
            ticker, load_trades(dataset), expansion_pct=expansion_pct,
            horizon_seconds=horizon_seconds, dedupe_seconds=dedupe_seconds,
        )
        # A move that completes sooner than the requested minimum lead cannot
        # possibly be predicted after its measured base.  Keeping those moves
        # in the recall denominator makes the target mathematically impossible
        # and disagrees with the leakage-safe training labels.
        moves.extend(
            {**move, "date": session_date} for move in ticker_moves
            if float(move["duration_seconds"]) >= lead_min_seconds
        )
        actionable_by_symbol_day[(ticker, session_date)] = sorted(
            float(f["detected_at"]) for f in findings if is_actionable(f)
        )

    hits = 0
    details = []
    matched_alerts: set[tuple[str, float]] = set()
    for move in moves:
        key = (str(move["ticker"]), str(move["date"]))
        alert_times = actionable_by_symbol_day.get(key) or []
        earliest = max(float(move["base_at"]), float(move["completed_at"]) - lead_max_seconds)
        latest = float(move["completed_at"]) - lead_min_seconds
        left, right = bisect_left(alert_times, earliest), bisect_right(alert_times, latest)
        candidates = [(key, detected_at) for detected_at in alert_times[left:right]]
        hit = bool(candidates)
        if hit:
            hits += 1
            matched_alerts.update(candidates)
        details.append({
            **move,
            "hit": hit,
            "alert_at": max((item[1] for item in candidates), default=None),
            "lead_seconds": (
                round(move["completed_at"] - max(item[1] for item in candidates), 6)
                if candidates else None
            ),
        })

    total = len(moves)
    alert_total = sum(len(values) for values in actionable_by_symbol_day.values())
    return {
        "definition": {
            "expansion_pct": expansion_pct,
            "horizon_seconds": horizon_seconds,
            "lead_min_seconds": lead_min_seconds,
            "lead_max_seconds": lead_max_seconds,
            "move_dedupe_seconds": dedupe_seconds,
        },
        "objective_moves": total,
        "moves_hit": hits,
        "recall": hits / total if total else None,
        "actionable_findings": alert_total,
        "actionable_findings_matched": len(matched_alerts),
        "strict_window_precision": len(matched_alerts) / alert_total if alert_total else None,
        "moves": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score fresh Scout alerts 15-30 seconds before short-horizon expansions")
    parser.add_argument(
        "--findings", required=True, action="append",
        help="Replay findings JSONL; repeat for deterministic replay shards",
    )
    parser.add_argument("--cache-dir", default="data/replay-datasets/backtest")
    parser.add_argument("--feed", default="sip")
    parser.add_argument("--output", required=True)
    parser.add_argument("--expansion-pct", type=float, default=2.0)
    parser.add_argument("--horizon-seconds", type=float, default=60.0)
    parser.add_argument("--lead-min-seconds", type=float, default=15.0)
    parser.add_argument("--lead-max-seconds", type=float, default=30.0)
    parser.add_argument("--dedupe-seconds", type=float, default=60.0)
    args = parser.parse_args()
    if args.lead_min_seconds < 0 or args.lead_max_seconds < args.lead_min_seconds:
        raise SystemExit("lead window must satisfy 0 <= min <= max")
    replay_rows = []
    for findings_path in args.findings:
        replay_rows.extend(load_jsonl(Path(findings_path)))
    report = score(
        replay_rows, Path(args.cache_dir), feed=args.feed,
        expansion_pct=args.expansion_pct, horizon_seconds=args.horizon_seconds,
        lead_min_seconds=args.lead_min_seconds, lead_max_seconds=args.lead_max_seconds,
        dedupe_seconds=args.dedupe_seconds,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"objective_moves={report['objective_moves']} hits={report['moves_hit']} recall={report['recall']}")
    print(f"actionable_findings={report['actionable_findings']} matched={report['actionable_findings_matched']} strict_window_precision={report['strict_window_precision']}")
    print(f"JSON report: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

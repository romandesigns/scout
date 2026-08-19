#!/usr/bin/env python3
"""
Scout Recall / Opportunity / Profitability Audit (v6.7.2)

Purpose
-------
Measure the side of Scout that ordinary precision audits cannot see:
    * which large movers Scout missed,
    * how early Scout first saw eventual movers,
    * how much of the observed move was already consumed,
    * which stages/ranks/quality labels delayed escalation,
    * whether a naive top-gainer baseline would have entered earlier/later,
    * and whether later detector changes preserve right-tail opportunities.

The monitor is intentionally observational. It does NOT change detector gates,
promotion logic, notifications, or trading behavior.

Data model
----------
Each collection sample appends one JSON object to a JSONL file:
  {
    "sampled_at": <unix seconds>,
    "engine_version": "...",
    "gainers": [...],
    "twenty_four_hour": [...],
    "findings": [...]
  }

Repeated samples allow the report to reconstruct first-seen timestamps/prices
without needing to modify production storage.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

DEFAULT_API = os.getenv("SCOUT_API_BASE", "https://srv1170872.tail86523.ts.net:8444")
THRESHOLDS = (5.0, 10.0, 20.0, 50.0)


def fetch_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "ScoutRecallAudit/6.7.2"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        value = payload.get("items")
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def fnum(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def ticker_of(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("symbol") or "").strip().upper()


def percent_of(row: dict[str, Any]) -> float | None:
    for key in ("percent_change", "change_pct", "percentChange", "change_percent"):
        value = fnum(row.get(key))
        if value is not None:
            return value
    return None


def price_of(row: dict[str, Any]) -> float | None:
    return fnum(row.get("price") or row.get("last") or row.get("last_price"))


def detected_at(row: dict[str, Any]) -> float:
    return fnum(row.get("detected_at")) or 0.0


def is_actionable(row: dict[str, Any]) -> bool:
    return str(row.get("actionable_rank") or "C").upper() in {"A", "B"} and str(
        row.get("quality_label") or ""
    ).upper() in {"CLEAN", "ACTIONABLE"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True))
        fh.write("\n")


def collect_once(api_base: str, top: int, findings_limit: int) -> dict[str, Any]:
    base = api_base.rstrip("/")
    health: dict[str, Any] = {}
    try:
        raw_health = fetch_json(f"{base}/healthz", timeout=20)
        if isinstance(raw_health, dict):
            health = raw_health
    except Exception as exc:
        health = {"error": str(exc)}

    gainers: list[dict[str, Any]] = []
    gainer_error = None
    try:
        gainers = items(fetch_json(f"{base}/api/market/gainers?top={max(1, min(50, top))}", timeout=30))
    except Exception as exc:
        gainer_error = str(exc)

    twenty_four_hour: list[dict[str, Any]] = []
    h24_error = None
    try:
        twenty_four_hour = items(
            fetch_json(f"{base}/api/market/24h?limit={max(20, min(500, top * 4))}", timeout=30)
        )
    except Exception as exc:
        # v6.7.2 stays compatible with installations where 24H endpoint is disabled.
        h24_error = str(exc)

    findings: list[dict[str, Any]] = []
    findings_error = None
    try:
        query = urllib.parse.urlencode({"limit": max(50, min(500, findings_limit)), "episodes": 1})
        findings = items(fetch_json(f"{base}/api/findings?{query}", timeout=40))
    except Exception as exc:
        findings_error = str(exc)

    return {
        "sampled_at": time.time(),
        "sampled_at_iso": datetime.now(timezone.utc).isoformat(),
        "api_base": base,
        "engine_version": health.get("version"),
        "health": {
            "ok": health.get("ok"),
            "hybrid_ready": health.get("hybrid_ready"),
            "error": health.get("error"),
        },
        "gainers": gainers,
        "twenty_four_hour": twenty_four_hour,
        "findings": findings,
        "errors": {
            "gainers": gainer_error,
            "twenty_four_hour": h24_error,
            "findings": findings_error,
        },
    }


def unique_findings(samples: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_ticker: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for sample in samples:
        for row in sample.get("findings") or []:
            if not isinstance(row, dict):
                continue
            ticker = ticker_of(row)
            if not ticker:
                continue
            identity = str(row.get("id") or row.get("finding_id") or (
                ticker,
                row.get("stage"),
                row.get("detected_at"),
                row.get("price"),
            ))
            by_ticker[ticker][identity] = row
    return {
        ticker: sorted(rows.values(), key=detected_at)
        for ticker, rows in by_ticker.items()
    }


def mover_history(samples: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        ts = fnum(sample.get("sampled_at")) or 0.0
        rows = []
        rows.extend(x for x in (sample.get("gainers") or []) if isinstance(x, dict))
        rows.extend(x for x in (sample.get("twenty_four_hour") or []) if isinstance(x, dict))
        seen: set[str] = set()
        for row in rows:
            ticker = ticker_of(row)
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            history[ticker].append({
                "at": ts,
                "price": price_of(row),
                "percent_change": percent_of(row),
                "source": "24h" if row in (sample.get("twenty_four_hour") or []) else "gainers",
            })
    return history


def first_stage(rows: list[dict[str, Any]], stages: set[str]) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("stage") or "").upper() in stages:
            return row
    return None


def first_actionable(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((row for row in rows if is_actionable(row)), None)


def observed_high(history: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    priced = [(fnum(x.get("price")), fnum(x.get("at"))) for x in history]
    priced = [(p, t) for p, t in priced if p is not None]
    if not priced:
        return None, None
    p, t = max(priced, key=lambda pair: pair[0])
    return p, t


def price_near(history: list[dict[str, Any]], at: float, tolerance: float = 90.0) -> float | None:
    candidates = [(abs((fnum(x.get("at")) or 0.0) - at), fnum(x.get("price"))) for x in history]
    candidates = [(d, p) for d, p in candidates if p is not None]
    if not candidates:
        return None
    d, p = min(candidates, key=lambda pair: pair[0])
    return p if d <= tolerance else None


def forward_return(history: list[dict[str, Any]], at: float, entry_price: float | None, seconds: int) -> float | None:
    if entry_price is None or entry_price <= 0:
        return None
    target = at + seconds
    candidates = [
        (abs((fnum(x.get("at")) or 0.0) - target), fnum(x.get("price")))
        for x in history
        if (fnum(x.get("at")) or 0.0) >= at
    ]
    candidates = [(d, p) for d, p in candidates if p is not None]
    if not candidates:
        return None
    d, p = min(candidates, key=lambda pair: pair[0])
    if d > max(90.0, seconds * 0.35):
        return None
    return (p / entry_price - 1.0) * 100.0


def threshold_crossing(history: list[dict[str, Any]], threshold: float) -> dict[str, Any] | None:
    """Return a fresh within-monitor crossing only.

    Session percent-change may already be +20/+50% when monitoring begins.
    Those are PREEXISTING movers and must not be credited as a fresh crossing.
    """
    ordered = sorted(history, key=lambda x: fnum(x.get("at")) or 0.0)
    if not ordered:
        return None
    initial = fnum(ordered[0].get("percent_change"))
    if initial is None or initial >= threshold:
        return None
    eligible = [x for x in ordered[1:] if (fnum(x.get("percent_change")) or -1e9) >= threshold]
    return min(eligible, key=lambda x: fnum(x.get("at")) or 0.0) if eligible else None


def preexisting_threshold(history: list[dict[str, Any]], threshold: float) -> bool:
    if not history:
        return False
    initial = fnum(sorted(history, key=lambda x: fnum(x.get("at")) or 0.0)[0].get("percent_change"))
    return initial is not None and initial >= threshold


def blocker_forensics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    next_blockers: Counter[str] = Counter()
    gate_failures: Counter[str] = Counter()
    for row in rows:
        for reason in row.get("rejection_reasons") or []:
            reasons[str(reason)] += 1
        profile = row.get("candidate_profile") if isinstance(row.get("candidate_profile"), dict) else {}
        trace = profile.get("promotion_trace") if isinstance(profile.get("promotion_trace"), dict) else {}
        if trace.get("next_blocker"):
            next_blockers[str(trace["next_blocker"])] += 1
        gates = trace.get("gates") if isinstance(trace.get("gates"), dict) else {}
        for gate, passed in gates.items():
            if passed is False:
                gate_failures[str(gate)] += 1
    return {
        "rejection_reasons": dict(reasons.most_common()),
        "next_blockers": dict(next_blockers.most_common()),
        "gate_failures": dict(gate_failures.most_common()),
    }


def consumption(first_market_at: float, event_at: float | None, first_market_price: float | None, event_price: float | None, high_price: float | None) -> float | None:
    if event_at is None or event_at < first_market_at:
        return None
    if first_market_price is None or event_price is None or high_price is None:
        return None
    denominator = high_price - first_market_price
    if denominator <= 0:
        return None
    return (event_price - first_market_price) / denominator * 100.0


def report(samples: list[dict[str, Any]]) -> dict[str, Any]:
    findings = unique_findings(samples)
    movers = mover_history(samples)
    rows: list[dict[str, Any]] = []

    for ticker, hist in movers.items():
        hist = sorted(hist, key=lambda x: fnum(x.get("at")) or 0.0)
        if not hist:
            continue
        frows = findings.get(ticker, [])
        first_market = hist[0]
        high_price, high_at = observed_high(hist)
        first_scout = frows[0] if frows else None
        pre = first_stage(frows, {"PRE_IGNITION", "ACTIVITY_WATCH"})
        early = first_stage(frows, {"EARLY", "FIRST_LEG"})
        actionable = first_actionable(frows)

        max_pct = max(
            [fnum(x.get("percent_change")) for x in hist if fnum(x.get("percent_change")) is not None]
            or [float("-inf")]
        )
        crossings: dict[str, Any] = {}
        preexisting: dict[str, bool] = {}
        first_monitor_at = fnum(first_market.get("at")) or 0.0
        for threshold in THRESHOLDS:
            preexisting[str(int(threshold))] = preexisting_threshold(hist, threshold)
            crossing = threshold_crossing(hist, threshold)
            if crossing:
                c_at = fnum(crossing.get("at")) or 0.0
                scout_before = next((r for r in frows if first_monitor_at <= detected_at(r) <= c_at), None)
                actionable_before = next((r for r in frows if first_monitor_at <= detected_at(r) <= c_at and is_actionable(r)), None)
                crossings[str(int(threshold))] = {
                    "crossed": True,
                    "crossed_at": c_at,
                    "crossed_price": fnum(crossing.get("price")),
                    "scout_seen_before_cross": bool(scout_before),
                    "actionable_before_cross": bool(actionable_before),
                }

        fmp = fnum(first_market.get("price"))
        scout_price = fnum(first_scout.get("price")) if first_scout else None
        act_price = fnum(actionable.get("price")) if actionable else None
        act_at = detected_at(actionable) if actionable else None

        baseline_at = fnum(first_market.get("at")) or 0.0
        baseline_price = fmp

        rows.append({
            "ticker": ticker,
            "max_percent_change_observed": None if max_pct == float("-inf") else max_pct,
            "first_market_at": baseline_at,
            "first_market_price": fmp,
            "observed_high_price": high_price,
            "observed_high_at": high_at,
            "first_scout_at": detected_at(first_scout) if first_scout else None,
            "first_scout_price": scout_price,
            "first_scout_stage": first_scout.get("stage") if first_scout else None,
            "first_pre_ignition_at": detected_at(pre) if pre else None,
            "first_pre_ignition_price": fnum(pre.get("price")) if pre else None,
            "first_early_at": detected_at(early) if early else None,
            "first_early_price": fnum(early.get("price")) if early else None,
            "first_actionable_at": act_at,
            "first_actionable_price": act_price,
            "first_actionable_stage": actionable.get("stage") if actionable else None,
            "first_actionable_rank": actionable.get("actionable_rank") if actionable else None,
            "first_actionable_quality": actionable.get("quality_label") if actionable else None,
            "scout_history_predates_monitor": bool(first_scout and detected_at(first_scout) < baseline_at),
            "move_consumed_at_first_scout_pct": consumption(baseline_at, detected_at(first_scout) if first_scout else None, fmp, scout_price, high_price),
            "move_consumed_at_first_actionable_pct": consumption(baseline_at, act_at, fmp, act_price, high_price),
            "scout_5m_return_pct": forward_return(hist, act_at, act_price, 300) if act_at else None,
            "scout_15m_return_pct": forward_return(hist, act_at, act_price, 900) if act_at else None,
            "baseline_5m_return_pct": forward_return(hist, baseline_at, baseline_price, 300),
            "baseline_15m_return_pct": forward_return(hist, baseline_at, baseline_price, 900),
            "thresholds": crossings,
            "preexisting_thresholds": preexisting,
            "blocker_forensics": blocker_forensics(frows),
            "finding_count": len(frows),
        })

    rows.sort(key=lambda r: (r.get("max_percent_change_observed") is not None, r.get("max_percent_change_observed") or -1e9), reverse=True)

    threshold_summary: dict[str, Any] = {}
    for threshold in THRESHOLDS:
        key = str(int(threshold))
        fresh = [r for r in rows if (r.get("thresholds") or {}).get(key, {}).get("crossed")]
        preexisting = [r for r in rows if (r.get("preexisting_thresholds") or {}).get(key)]
        seen_before = [r for r in fresh if (r.get("thresholds") or {}).get(key, {}).get("scout_seen_before_cross")]
        actionable_before = [r for r in fresh if (r.get("thresholds") or {}).get(key, {}).get("actionable_before_cross")]
        threshold_summary[key] = {
            # Corrected semantics.
            "fresh_crossings": len(fresh),
            "preexisting_at_monitor_start": len(preexisting),
            "seen_before_fresh_cross": len(seen_before),
            "seen_before_fresh_cross_rate": len(seen_before) / len(fresh) if fresh else None,
            "actionable_before_fresh_cross": len(actionable_before),
            "actionable_before_fresh_cross_rate": len(actionable_before) / len(fresh) if fresh else None,
            # Backward-compatible names now explicitly mean fresh crossings only.
            "movers": len(fresh),
            "scout_seen": len(seen_before),
            "scout_seen_rate": len(seen_before) / len(fresh) if fresh else None,
            "seen_before_threshold": len(seen_before),
            "seen_before_threshold_rate": len(seen_before) / len(fresh) if fresh else None,
            "actionable_before_threshold": len(actionable_before),
            "actionable_before_threshold_rate": len(actionable_before) / len(fresh) if fresh else None,
        }

    actionable_consumption = [
        r["move_consumed_at_first_actionable_pct"] for r in rows
        if r.get("move_consumed_at_first_actionable_pct") is not None
    ]
    scout5 = [r["scout_5m_return_pct"] for r in rows if r.get("scout_5m_return_pct") is not None]
    base5 = [r["baseline_5m_return_pct"] for r in rows if r.get("baseline_5m_return_pct") is not None]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(samples),
        "first_sample_at": min((fnum(x.get("sampled_at")) or 0.0 for x in samples), default=None),
        "last_sample_at": max((fnum(x.get("sampled_at")) or 0.0 for x in samples), default=None),
        "mover_count": len(rows),
        "threshold_recall": threshold_summary,
        "median_move_consumed_at_first_actionable_pct": (
            statistics.median(actionable_consumption) if actionable_consumption else None
        ),
        "baseline_comparison": {
            "scout_actionable_5m_average_pct": statistics.mean(scout5) if scout5 else None,
            "naive_first_gainer_5m_average_pct": statistics.mean(base5) if base5 else None,
            "scout_5m_n": len(scout5),
            "baseline_5m_n": len(base5),
        },
        "largest_missed_movers": [
            r for r in rows
            if r.get("first_scout_at") is None
        ][:20],
        "top_blockers_on_seen_nonactionable": blocker_forensics([
            finding
            for ticker, finding_rows in findings.items()
            for finding in finding_rows
            if ticker in {r["ticker"] for r in rows if r.get("first_scout_at") is not None and r.get("first_actionable_at") is None}
        ]),
        "largest_late_actionable_movers": [
            r for r in rows
            if r.get("first_scout_at") is not None and r.get("first_actionable_at") is not None
            and (r.get("move_consumed_at_first_actionable_pct") or 0) >= 50
        ][:20],
        "rows": rows,
    }


def print_report(data: dict[str, Any]) -> None:
    print("=" * 118)
    print("SCOUT RECALL / OPPORTUNITY / PROFITABILITY AUDIT")
    print("=" * 118)
    print(f"samples={data.get('sample_count')} movers={data.get('mover_count')}")
    print("RIGHT-TAIL RECALL")
    for threshold in THRESHOLDS:
        s = data.get("threshold_recall", {}).get(str(int(threshold)), {})
        def pct(v: Any) -> str:
            return "n/a" if v is None else f"{float(v):.1%}"
        print(
            f"  +{int(threshold):>2}% fresh={s.get('fresh_crossings',0):>3} "
            f"preexisting={s.get('preexisting_at_monitor_start',0):>3} "
            f"seen-before-fresh={pct(s.get('seen_before_fresh_cross_rate')):>7} "
            f"actionable-before-fresh={pct(s.get('actionable_before_fresh_cross_rate')):>7}"
        )
    consumed = data.get("median_move_consumed_at_first_actionable_pct")
    print(f"median move consumed at first actionable: {'n/a' if consumed is None else f'{consumed:.1f}%'}")
    b = data.get("baseline_comparison", {})
    print(
        "5m baseline comparison: "
        f"Scout={b.get('scout_actionable_5m_average_pct')} (n={b.get('scout_5m_n')}) "
        f"naive-gainer={b.get('naive_first_gainer_5m_average_pct')} (n={b.get('baseline_5m_n')})"
    )
    if data.get("largest_missed_movers"):
        print("\nLARGEST MISSED MOVERS")
        for row in data["largest_missed_movers"][:10]:
            print(f"  {row['ticker']:6} max_change={row.get('max_percent_change_observed')} first_price={row.get('first_market_price')}")
    if data.get("largest_late_actionable_movers"):
        print("\nLARGEST LATE-ACTIONABLE MOVERS")
        for row in data["largest_late_actionable_movers"][:10]:
            print(
                f"  {row['ticker']:6} max_change={row.get('max_percent_change_observed')} "
                f"first={row.get('first_market_price')} actionable={row.get('first_actionable_price')} "
                f"consumed={row.get('move_consumed_at_first_actionable_pct'):.1f}%"
            )
    print("=" * 118)


def command_sample(args: argparse.Namespace) -> int:
    row = collect_once(args.api_base, args.top, args.findings_limit)
    append_jsonl(Path(args.dataset), row)
    print(json.dumps({
        "dataset": str(Path(args.dataset).resolve()),
        "sampled_at": row["sampled_at_iso"],
        "engine_version": row.get("engine_version"),
        "gainers": len(row.get("gainers") or []),
        "twenty_four_hour": len(row.get("twenty_four_hour") or []),
        "findings": len(row.get("findings") or []),
        "errors": row.get("errors"),
    }, indent=2))
    return 0


def command_run(args: argparse.Namespace) -> int:
    dataset = Path(args.dataset)
    started = time.time()
    stop_at = started + max(1.0, args.duration_minutes * 60.0)
    interval = max(15.0, args.interval_seconds)
    samples = 0
    print(f"Collecting Scout recall samples -> {dataset.resolve()}")
    print(f"interval={interval:.0f}s duration={args.duration_minutes:.1f}m")
    try:
        while time.time() < stop_at:
            row = collect_once(args.api_base, args.top, args.findings_limit)
            append_jsonl(dataset, row)
            samples += 1
            print(
                f"[{row['sampled_at_iso']}] version={row.get('engine_version')} "
                f"gainers={len(row.get('gainers') or [])} 24h={len(row.get('twenty_four_hour') or [])} "
                f"findings={len(row.get('findings') or [])}"
            )
            sleep_for = min(interval, max(0.0, stop_at - time.time()))
            if sleep_for > 0:
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        print("\nCollection interrupted by user; saved samples remain valid.")
    print(f"samples_written={samples}")
    return 0


def command_report(args: argparse.Namespace) -> int:
    samples = load_jsonl(Path(args.dataset))
    if not samples:
        print(f"No samples found in {args.dataset}", file=sys.stderr)
        return 2
    data = report(samples)
    print_report(data)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"JSON report: {out.resolve()}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Scout right-tail recall and opportunity audit")
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--api-base", default=DEFAULT_API)
    common.add_argument("--dataset", default="data/optimization/recall-opportunity.jsonl")
    common.add_argument("--top", type=int, default=50)
    common.add_argument("--findings-limit", type=int, default=500)

    s = sub.add_parser("sample", parents=[common], help="append one production snapshot")
    s.set_defaults(func=command_sample)

    r = sub.add_parser("run", parents=[common], help="collect snapshots repeatedly")
    r.add_argument("--interval-seconds", type=float, default=30.0)
    r.add_argument("--duration-minutes", type=float, default=480.0)
    r.set_defaults(func=command_run)

    q = sub.add_parser("report", help="build recall/profitability report from accumulated samples")
    q.add_argument("--dataset", default="data/optimization/recall-opportunity.jsonl")
    q.add_argument("--output", default="data/optimization/recall-opportunity-report.json")
    q.set_defaults(func=command_report)

    args = p.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

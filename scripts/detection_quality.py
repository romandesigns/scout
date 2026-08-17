from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HORIZONS = (30, 60, 120, 300, 900)


def fetch_json(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def safe_float(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def pct_change(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return (b / a - 1.0) * 100.0


def actionable(f: dict[str, Any]) -> bool:
    """Strict production-actionable cohort: rank A or B only."""
    return str(f.get("actionable_rank") or "").strip().upper() in {"A", "B"}


def developing(f: dict[str, Any]) -> bool:
    rank = str(f.get("actionable_rank") or "").strip().upper()
    label = str(f.get("quality_label") or "").strip().upper()
    return rank == "C" or label in {"DEVELOPING", "ILLIQUID"}


def select_findings(
    findings: list[dict[str, Any]],
    *,
    now: float,
    min_age_seconds: float,
    include_developing: bool,
) -> tuple[list[tuple[str, dict[str, Any]]], Counter[str]]:
    selected: list[tuple[str, dict[str, Any]]] = []
    excluded: Counter[str] = Counter()
    for f in findings:
        cohort = "ACTIONABLE" if actionable(f) else ("DEVELOPING" if include_developing and developing(f) else None)
        if cohort is None:
            excluded["not_in_requested_cohort"] += 1
            continue
        detected_at = safe_float(f.get("detected_at"))
        price = safe_float(f.get("price"))
        if detected_at is None:
            excluded["missing_detected_at"] += 1
            continue
        if price is None or price <= 0:
            excluded["invalid_price"] += 1
            continue
        if now - detected_at < min_age_seconds:
            excluded["too_young"] += 1
            continue
        selected.append((cohort, f))
    return selected, excluded


def nearest_index(rows: list[dict[str, Any]], target: float, tolerance: float = 45.0) -> int | None:
    if not rows:
        return None
    idx = min(range(len(rows)), key=lambda i: abs(float(rows[i].get("start_ts", 0.0)) - target))
    return idx if abs(float(rows[idx]["start_ts"]) - target) <= tolerance else None


def forward_metrics(rows: list[dict[str, Any]], detected_at: float, detection_price: float) -> dict[str, Any]:
    rows = sorted(
        [r for r in rows if safe_float(r.get("start_ts")) is not None and safe_float(r.get("close")) is not None],
        key=lambda r: float(r["start_ts"]),
    )
    result: dict[str, Any] = {"first_ts": None, "last_ts": None}
    if not rows:
        for horizon in HORIZONS:
            result[f"return_{horizon}s_pct"] = None
            result[f"mfe_{horizon}s_pct"] = None
            result[f"mae_{horizon}s_pct"] = None
        return result

    result["first_ts"] = float(rows[0]["start_ts"])
    result["last_ts"] = float(rows[-1]["start_ts"])
    for horizon in HORIZONS:
        target = detected_at + horizon
        idx = nearest_index(rows, target)
        result[f"return_{horizon}s_pct"] = (
            pct_change(detection_price, safe_float(rows[idx]["close"])) if idx is not None else None
        )
        window = [r for r in rows if detected_at <= float(r["start_ts"]) <= target]
        highs = [safe_float(r.get("high")) for r in window]
        lows = [safe_float(r.get("low")) for r in window]
        highs = [x for x in highs if x is not None]
        lows = [x for x in lows if x is not None]
        result[f"mfe_{horizon}s_pct"] = pct_change(detection_price, max(highs)) if highs else None
        result[f"mae_{horizon}s_pct"] = pct_change(detection_price, min(lows)) if lows else None
    return result


def coverage(metrics: dict[str, Any]) -> str:
    if safe_float(metrics.get("return_300s_pct")) is None or safe_float(metrics.get("mfe_300s_pct")) is None:
        return "UNMATURED"
    if safe_float(metrics.get("return_900s_pct")) is None or safe_float(metrics.get("mfe_900s_pct")) is None:
        return "PROVISIONAL_5M"
    return "FINAL_15M"


def base_classification(metrics: dict[str, Any]) -> str:
    r30 = safe_float(metrics.get("return_30s_pct"))
    r120 = safe_float(metrics.get("return_120s_pct"))
    r300 = safe_float(metrics.get("return_300s_pct"))
    mfe300 = safe_float(metrics.get("mfe_300s_pct"))
    mae300 = safe_float(metrics.get("mae_300s_pct"))
    if None in (r300, mfe300, mae300):
        return "UNMATURED"
    if mfe300 >= 3.0 and (r30 or 0) < 2.0:
        return "EARLY"
    if mfe300 >= 1.5 and (r120 or 0) >= 0:
        return "USEFUL"
    if (r30 or 0) >= 2.5 and (mfe300 - (r30 or 0)) < 1.0:
        return "LATE"
    if mfe300 < 1.0 and mae300 <= -1.0:
        return "FALSE_POSITIVE"
    if r300 < -1.0:
        return "FADE"
    return "MIXED"


def classification(metrics: dict[str, Any]) -> str:
    cov = coverage(metrics)
    if cov == "UNMATURED":
        return "UNMATURED"
    label = base_classification(metrics)
    if cov == "PROVISIONAL_5M":
        return f"PROVISIONAL_{label}"
    return label


def mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def fmt(v: Any) -> str:
    x = safe_float(v)
    return "-" if x is None else f"{x:+.2f}%"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    final_rows = [r for r in rows if r.get("coverage") == "FINAL_15M"]
    provisional_rows = [r for r in rows if r.get("coverage") == "PROVISIONAL_5M"]
    classifications = Counter(str(r.get("classification")) for r in rows)
    summary: dict[str, Any] = {
        "sample_count": len(rows),
        "final_count": len(final_rows),
        "provisional_count": len(provisional_rows),
        "unmatured_count": sum(1 for r in rows if r.get("coverage") == "UNMATURED"),
        "classifications": dict(sorted(classifications.items())),
        "averages": {},
    }
    for field in ("return_30s_pct", "return_60s_pct", "return_120s_pct", "return_300s_pct", "return_900s_pct", "mfe_300s_pct", "mae_300s_pct"):
        vals = [safe_float(r.get(field)) for r in final_rows]
        summary["averages"][field] = mean([v for v in vals if v is not None])
    if final_rows:
        useful = sum(1 for r in final_rows if r["classification"] in {"EARLY", "USEFUL"})
        late = sum(1 for r in final_rows if r["classification"] == "LATE")
        falsey = sum(1 for r in final_rows if r["classification"] in {"FALSE_POSITIVE", "FADE"})
        summary["useful_rate"] = useful / len(final_rows)
        summary["late_rate"] = late / len(final_rows)
        summary["false_or_fade_rate"] = falsey / len(final_rows)
    else:
        summary["useful_rate"] = summary["late_rate"] = summary["false_or_fade_rate"] = None
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description="Scout forward detection-quality audit v3")
    p.add_argument("--api-base", default=os.getenv("SCOUT_API_BASE", "https://srv1170872.tail86523.ts.net:8444"))
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--include-developing", action="store_true")
    p.add_argument("--min-age-seconds", type=int, default=300)
    p.add_argument("--output-prefix", default="detection-quality")
    args = p.parse_args()

    base = args.api_base.rstrip("/")
    now = time.time()
    limit = max(20, min(500, args.limit))
    # v3: ask production for rows that are already old enough to evaluate.
    # This avoids the former starvation bug where the latest 100/500 findings were
    # dominated by fresh C-rank observations and every A/B finding was filtered out.
    before = now - args.min_age_seconds
    query = {
        "limit": limit,
        "before": f"{before:.6f}",
    }
    if not args.include_developing:
        query["actionable_only"] = "1"
    data = fetch_json(f"{base}/api/findings?{urllib.parse.urlencode(query)}", timeout=40)
    findings = data.get("items", data if isinstance(data, list) else [])
    validation_data = fetch_json(f"{base}/api/validation?limit=500", timeout=40)
    validations = {int(x["id"]): x for x in validation_data.get("items", []) if x.get("id") is not None}

    selected, excluded = select_findings(
        findings,
        now=now,
        min_age_seconds=args.min_age_seconds,
        include_developing=args.include_developing,
    )

    rows_out: list[dict[str, Any]] = []
    errors: list[str] = []
    print("=" * 126)
    print("SCOUT FORWARD DETECTION-QUALITY AUDIT v3")
    print(f"API={base} fetched={len(findings)} selected={len(selected)} strict_actionable=A/B include_developing={args.include_developing} min_age={args.min_age_seconds}s")
    print("=" * 126)
    print(f"{'COHORT':11} {'TICKER':7} {'STAGE':18} {'RANK':5} {'PRICE':9} {'30s':8} {'1m':8} {'2m':8} {'5m':8} {'15m':8} {'MFE5':8} {'MAE5':8} {'COVERAGE':15} LABEL")

    for cohort, f in selected:
        ticker = str(f.get("ticker") or "").upper(); detected_at = float(f["detected_at"]); price = float(f["price"])
        bucket = int(f.get("detection_timeframe_seconds") or 15)
        q = urllib.parse.urlencode({"detected_at": detected_at, "bucket_seconds": bucket, "finding_id": f.get("id") or "", "historical": 1})
        try:
            snap = fetch_json(f"{base}/api/market/snapshot/{urllib.parse.quote(ticker)}?{q}", timeout=60)
            metrics = forward_metrics(snap.get("buckets", []), detected_at, price)
        except Exception as exc:
            errors.append(f"{ticker} finding={f.get('id')}: {exc}"); metrics = forward_metrics([], detected_at, price)
        cov = coverage(metrics); label = classification(metrics); persisted = validations.get(int(f.get("id") or 0), {})
        out = {
            "cohort": cohort, "id": f.get("id"), "ticker": ticker, "stage": f.get("stage"), "detected_at": detected_at,
            "detected_at_iso": datetime.fromtimestamp(detected_at, timezone.utc).isoformat(), "price": price,
            "actionable_rank": f.get("actionable_rank"), "quality_label": f.get("quality_label"), "quality_score": f.get("quality_score"),
            "ross_score": f.get("ross_score"), "rv15": f.get("vol_ratio_15s"), "change_5s_pct": f.get("change_5s_pct"),
            "change_15s_pct": f.get("change_15s_pct"), "change_30s_pct": f.get("change_30s_pct"), "timeliness_label": f.get("timeliness_label"),
            "coverage": cov, "classification": label,
            "stored_max_1m_pct": persisted.get("max_1m_pct"), "stored_max_5m_pct": persisted.get("max_5m_pct"),
            "stored_max_15m_pct": persisted.get("max_15m_pct"), "stored_time_to_peak_seconds": persisted.get("time_to_peak_seconds"),
            **metrics,
        }
        rows_out.append(out)
        print(f"{cohort:11} {ticker:7} {str(f.get('stage') or '')[:18]:18} {str(f.get('actionable_rank') or '-'):5} {price:9.4f} "
              f"{fmt(metrics.get('return_30s_pct')):8} {fmt(metrics.get('return_60s_pct')):8} {fmt(metrics.get('return_120s_pct')):8} "
              f"{fmt(metrics.get('return_300s_pct')):8} {fmt(metrics.get('return_900s_pct')):8} {fmt(metrics.get('mfe_300s_pct')):8} "
              f"{fmt(metrics.get('mae_300s_pct')):8} {cov:15} {label}")

    cohorts: dict[str, Any] = {}
    for name in ("ACTIONABLE", "DEVELOPING"):
        cohort_rows = [r for r in rows_out if r["cohort"] == name]
        if cohort_rows or name == "ACTIONABLE":
            cohorts[name] = summarize(cohort_rows)
    stages: dict[str, Any] = {}
    for stage in sorted({str(r.get("stage") or "UNKNOWN") for r in rows_out}):
        stages[stage] = summarize([r for r in rows_out if str(r.get("stage") or "UNKNOWN") == stage])

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "api_base": base, "strict_actionable": True,
        "include_developing": args.include_developing, "min_age_seconds": args.min_age_seconds,
        "fetched_count": len(findings), "selected_count": len(selected),
        "excluded": dict(sorted(excluded.items())),
        "cohorts": cohorts, "stages": stages, "errors": errors,
    }
    print("\n" + "=" * 126 + "\nSUMMARY")
    print(f"fetched={len(findings)} selected={len(selected)} excluded={dict(sorted(excluded.items()))}")
    for name, s in cohorts.items():
        print(f"{name}: sample={s['sample_count']} final15m={s['final_count']} provisional5m={s['provisional_count']} unmatured={s['unmatured_count']} classes={s['classifications']}")
        if s["final_count"]:
            print(f"  useful/early={s['useful_rate']:.1%} late={s['late_rate']:.1%} false/fade={s['false_or_fade_rate']:.1%}; "
                  f"avg 30s={fmt(s['averages']['return_30s_pct'])} 1m={fmt(s['averages']['return_60s_pct'])} 2m={fmt(s['averages']['return_120s_pct'])} "
                  f"5m={fmt(s['averages']['return_300s_pct'])} 15m={fmt(s['averages']['return_900s_pct'])}")
    if errors: print(f"errors={len(errors)} (see JSON)")
    print("=" * 126)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S"); prefix = Path(f"{args.output_prefix}-{stamp}")
    json_path = prefix.with_suffix(".json"); csv_path = prefix.with_suffix(".csv")
    json_path.write_text(json.dumps({"summary": summary, "rows": rows_out}, indent=2), encoding="utf-8")
    fields = list(rows_out[0].keys()) if rows_out else ["cohort", "id", "ticker", "stage", "actionable_rank", "coverage", "classification"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields); writer.writeheader(); writer.writerows(rows_out)
    print(f"JSON report: {json_path.resolve()}\nCSV report:  {csv_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

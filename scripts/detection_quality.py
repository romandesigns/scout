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

try:
    # Package import path used by pytest/tests importing scripts.detection_quality.
    from .independent_market_data import IndependentCrossChecker, make_provider
except ImportError:
    # Direct-script path used by: python scripts/detection_quality.py
    from independent_market_data import IndependentCrossChecker, make_provider

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


def mixed_breakdown(metrics: dict[str, Any]) -> dict[str, str | None]:
    """Explain the former flat MIXED bucket by direction, magnitude and resolution."""
    r300 = safe_float(metrics.get("return_300s_pct"))
    r900 = safe_float(metrics.get("return_900s_pct"))
    mfe300 = safe_float(metrics.get("mfe_300s_pct"))
    mae300 = safe_float(metrics.get("mae_300s_pct"))
    if r300 is None:
        return {"direction_5m": None, "magnitude_5m": None, "resolution_15m": None}

    direction = "POSITIVE" if r300 >= 0.25 else ("NEGATIVE" if r300 <= -0.25 else "FLAT")
    ar = abs(r300)
    magnitude = "TINY" if ar < 0.25 else ("SMALL" if ar < 0.75 else ("MEDIUM" if ar < 1.5 else "LARGE"))

    if r900 is None:
        resolution = "PENDING_15M"
    elif direction == "POSITIVE":
        if r900 >= max(0.25, r300 + 0.25):
            resolution = "CONTINUATION"
        elif r900 <= 0:
            resolution = "FADE"
        else:
            resolution = "HELD"
    elif direction == "NEGATIVE":
        if r900 >= 0.25:
            resolution = "RECOVERY"
        elif r900 >= r300 + 0.50:
            resolution = "PARTIAL_RECOVERY"
        elif r900 <= r300 - 0.25:
            resolution = "DETERIORATION"
        else:
            resolution = "HELD_NEGATIVE"
    else:
        if r900 >= 0.50:
            resolution = "RESOLVED_UP"
        elif r900 <= -0.50:
            resolution = "RESOLVED_DOWN"
        else:
            resolution = "CHOP"

    # Path-shape override: large two-sided excursion with little net progress.
    if mfe300 is not None and mae300 is not None and abs(r300) < 0.5 and mfe300 >= 0.75 and mae300 <= -0.75:
        resolution = "CHOP"

    return {"direction_5m": direction, "magnitude_5m": magnitude, "resolution_15m": resolution}


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
    detail = mixed_breakdown(metrics)
    return "MIXED_" + str(detail["direction_5m"]) + "_" + str(detail["resolution_15m"])


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
    matured5 = [r for r in rows if r.get("coverage") in {"PROVISIONAL_5M", "FINAL_15M"}]
    final15 = [r for r in rows if r.get("coverage") == "FINAL_15M"]
    classifications = Counter(str(r.get("classification")) for r in rows)

    def normalized_label(r: dict[str, Any]) -> str:
        label = str(r.get("classification") or "").removeprefix("PROVISIONAL_")
        return "MIXED" if label.startswith("MIXED_") else label

    summary: dict[str, Any] = {
        "sample_count": len(rows),
        "matured_5m_count": len(matured5),
        "final_15m_count": len(final15),
        # Backward-compatible names.
        "final_count": len(final15),
        "provisional_count": sum(1 for r in rows if r.get("coverage") == "PROVISIONAL_5M"),
        "unmatured_count": sum(1 for r in rows if r.get("coverage") == "UNMATURED"),
        "classifications": dict(sorted(classifications.items())),
        "averages_5m": {},
        "averages_15m": {},
    }
    for field in ("return_30s_pct", "return_60s_pct", "return_120s_pct", "return_300s_pct", "mfe_300s_pct", "mae_300s_pct"):
        vals = [safe_float(r.get(field)) for r in matured5]
        summary["averages_5m"][field] = mean([v for v in vals if v is not None])
    for field in ("return_30s_pct", "return_60s_pct", "return_120s_pct", "return_300s_pct", "return_900s_pct", "mfe_300s_pct", "mae_300s_pct"):
        vals = [safe_float(r.get(field)) for r in final15]
        summary["averages_15m"][field] = mean([v for v in vals if v is not None])
    # Backward-compatible averages remain the 15m-final cohort.
    summary["averages"] = dict(summary["averages_15m"])

    if matured5:
        labels = [normalized_label(r) for r in matured5]
        summary["useful_rate_5m"] = sum(x in {"EARLY", "USEFUL"} for x in labels) / len(labels)
        summary["late_rate_5m"] = sum(x == "LATE" for x in labels) / len(labels)
        summary["false_or_fade_rate_5m"] = sum(x in {"FALSE_POSITIVE", "FADE"} for x in labels) / len(labels)
    else:
        summary["useful_rate_5m"] = summary["late_rate_5m"] = summary["false_or_fade_rate_5m"] = None

    if final15:
        labels = [normalized_label(r) for r in final15]
        summary["useful_rate_15m"] = sum(x in {"EARLY", "USEFUL"} for x in labels) / len(labels)
        summary["late_rate_15m"] = sum(x == "LATE" for x in labels) / len(labels)
        summary["false_or_fade_rate_15m"] = sum(x in {"FALSE_POSITIVE", "FADE"} for x in labels) / len(labels)
    else:
        summary["useful_rate_15m"] = summary["late_rate_15m"] = summary["false_or_fade_rate_15m"] = None
    # Backward compatibility.
    summary["useful_rate"] = summary["useful_rate_15m"]
    summary["late_rate"] = summary["late_rate_15m"]
    summary["false_or_fade_rate"] = summary["false_or_fade_rate_15m"]
    return summary


def main() -> int:
    p = argparse.ArgumentParser(description="Scout forward detection-quality audit v6")
    p.add_argument("--api-base", default=os.getenv("SCOUT_API_BASE", "https://srv1170872.tail86523.ts.net:8444"))
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--include-developing", action="store_true")
    p.add_argument("--min-age-seconds", type=int, default=300)
    p.add_argument("--output-prefix", default="detection-quality")
    p.add_argument("--engine-version", default=None, help="Only audit findings produced by this Scout version; defaults to /healthz version")
    p.add_argument("--independent-provider", default=os.getenv("SCOUT_INDEPENDENT_PROVIDER", "none"), choices=("none", "alphavantage"))
    p.add_argument("--alpha-vantage-api-key", default=os.getenv("ALPHAVANTAGE_API_KEY"))
    p.add_argument("--independent-tolerance-pct", type=float, default=float(os.getenv("SCOUT_INDEPENDENT_TOLERANCE_PCT", "0.75")))
    p.add_argument("--independent-max-symbols", type=int, default=int(os.getenv("SCOUT_INDEPENDENT_MAX_SYMBOLS", "20")))
    args = p.parse_args()

    base = args.api_base.rstrip("/")
    now = time.time()
    limit = max(20, min(500, args.limit))
    engine_version = args.engine_version
    if not engine_version:
        try:
            engine_version = str(fetch_json(f"{base}/healthz", timeout=20).get("version") or "").strip() or None
        except Exception:
            engine_version = None
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
    if engine_version:
        query["engine_version"] = engine_version
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

    provider = make_provider(args.independent_provider, api_key=args.alpha_vantage_api_key)
    crosschecker = IndependentCrossChecker(provider, tolerance_pct=args.independent_tolerance_pct)
    independent_symbols_used: set[str] = set()

    rows_out: list[dict[str, Any]] = []
    errors: list[str] = []
    print("=" * 126)
    print("SCOUT FORWARD DETECTION-QUALITY AUDIT v6")
    print(f"API={base} version={engine_version or 'ANY'} fetched={len(findings)} selected={len(selected)} strict_actionable=A/B include_developing={args.include_developing} min_age={args.min_age_seconds}s")
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
        mixed = mixed_breakdown(metrics) if label.removeprefix("PROVISIONAL_").startswith("MIXED_") else {
            "direction_5m": None, "magnitude_5m": None, "resolution_15m": None
        }

        independent: dict[str, Any]
        independent_comparison: dict[str, Any]
        if provider.configured and (ticker in independent_symbols_used or len(independent_symbols_used) < max(0, args.independent_max_symbols)):
            independent_symbols_used.add(ticker)
            independent = crosschecker.metrics(ticker, detected_at, price)
            independent_comparison = crosschecker.compare(metrics, independent)
        else:
            independent = {
                "provider": provider.name,
                "status": "LIMIT_REACHED" if provider.configured else "NOT_CONFIGURED",
                "configured": provider.configured,
            }
            independent_comparison = {"status": independent["status"], "within_tolerance": None, "deltas": {}}

        out = {
            "cohort": cohort, "id": f.get("id"), "ticker": ticker, "stage": f.get("stage"), "detected_at": detected_at,
            "detected_at_iso": datetime.fromtimestamp(detected_at, timezone.utc).isoformat(), "price": price,
            "actionable_rank": f.get("actionable_rank"), "quality_label": f.get("quality_label"), "quality_score": f.get("quality_score"),
            "ross_score": f.get("ross_score"), "rv15": f.get("vol_ratio_15s"), "change_5s_pct": f.get("change_5s_pct"),
            "change_15s_pct": f.get("change_15s_pct"), "change_30s_pct": f.get("change_30s_pct"), "timeliness_label": f.get("timeliness_label"),
            "coverage": cov, "classification": label,
            "classification_family": "MIXED" if label.removeprefix("PROVISIONAL_").startswith("MIXED_") else label.removeprefix("PROVISIONAL_"),
            "mixed_direction_5m": mixed["direction_5m"], "mixed_magnitude_5m": mixed["magnitude_5m"],
            "mixed_resolution_15m": mixed["resolution_15m"],
            "independent_provider": independent.get("provider"), "independent_status": independent.get("status"),
            "independent_entry_price": independent.get("entry_price"),
            "independent_entry_price_delta_pct": independent.get("entry_price_delta_pct"),
            "independent_return_300s_pct": independent.get("return_300s_pct"),
            "independent_return_900s_pct": independent.get("return_900s_pct"),
            "independent_mfe_300s_pct": independent.get("mfe_300s_pct"),
            "independent_mae_300s_pct": independent.get("mae_300s_pct"),
            "independent_within_tolerance": independent_comparison.get("within_tolerance"),
            "independent_deltas": independent_comparison.get("deltas"),
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

    independent_rows = [r for r in rows_out if r.get("independent_status") == "OK"]
    independent_compared = [r for r in independent_rows if r.get("independent_within_tolerance") is not None]
    independent_summary = {
        "provider": provider.name,
        "configured": provider.configured,
        "rows_ok": len(independent_rows),
        "rows_compared": len(independent_compared),
        "within_tolerance_count": sum(1 for r in independent_compared if r.get("independent_within_tolerance") is True),
        "outside_tolerance_count": sum(1 for r in independent_compared if r.get("independent_within_tolerance") is False),
        "tolerance_pct": args.independent_tolerance_pct,
    }

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "api_base": base, "strict_actionable": True,
        "include_developing": args.include_developing, "min_age_seconds": args.min_age_seconds,
        "engine_version": engine_version,
        "fetched_count": len(findings), "selected_count": len(selected),
        "excluded": dict(sorted(excluded.items())),
        "cohorts": cohorts, "stages": stages, "independent_validation": independent_summary, "errors": errors,
    }
    print("\n" + "=" * 126 + "\nSUMMARY")
    print(f"fetched={len(findings)} selected={len(selected)} excluded={dict(sorted(excluded.items()))}")
    for name, s in cohorts.items():
        print(f"{name}: sample={s['sample_count']} matured5m={s['matured_5m_count']} final15m={s['final_15m_count']} provisional5m={s['provisional_count']} unmatured={s['unmatured_count']} classes={s['classifications']}")
        if s["matured_5m_count"]:
            a = s["averages_5m"]
            print(f"  5M: useful/early={s['useful_rate_5m']:.1%} late={s['late_rate_5m']:.1%} false/fade={s['false_or_fade_rate_5m']:.1%}; "
                  f"avg 30s={fmt(a['return_30s_pct'])} 1m={fmt(a['return_60s_pct'])} 2m={fmt(a['return_120s_pct'])} "
                  f"5m={fmt(a['return_300s_pct'])} MFE5={fmt(a['mfe_300s_pct'])} MAE5={fmt(a['mae_300s_pct'])}")
        if s["final_15m_count"]:
            a = s["averages_15m"]
            print(f"  15M FINAL: useful/early={s['useful_rate_15m']:.1%} late={s['late_rate_15m']:.1%} false/fade={s['false_or_fade_rate_15m']:.1%}; "
                  f"avg 5m={fmt(a['return_300s_pct'])} 15m={fmt(a['return_900s_pct'])}")
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

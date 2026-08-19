#!/usr/bin/env python3
"""
Scout Full-Day Regular-Hours Live Verification (2026-08-19)

Purpose
-------
Answers, with an actual sample size instead of spot checks: how effective was Scout at
detecting real regular-hours opportunities today? Applies the exact same detector-blind
recall/precision methodology used all week for historical backtests, but against Scout's
REAL production findings from today's live session and REAL independent forward price data
-- not a replay.

RECALL: of real regular-hours movers (independent Alpaca ground truth, built by
scripts.historical_mover_finder --regular-hours-only), what fraction did Scout ever see,
and see/promote to actionable *before* each +5/10/20/50% threshold crossed?

PRECISION: of every actionable (rank A/B) finding Scout fired during regular hours today,
what was the real forward outcome (net_opportunity_pct = mfe_300s_pct + mae_300s_pct,
independently computed from Alpaca 1-min bars, not Scout's own self-reported numbers)?

Usage
-----
python -m scripts.live_full_day_scorer --movers data/optimization/backtest/movers-today-regular.jsonl \
    --output data/optimization/backtest/live-full-day-report.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = "https://srv1170872.tail86523.ts.net:8444"
THRESHOLDS = (5.0, 10.0, 20.0, 50.0)
ET = ZoneInfo("America/New_York")


def load_env() -> dict[str, str]:
    env = {}
    path = Path(".env")
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def regular_hours_window(target_date: str) -> tuple[float, float]:
    d = datetime.fromisoformat(target_date).date()
    start = datetime.combine(d, datetime.min.time(), ET).replace(hour=9, minute=30)
    end = datetime.combine(d, datetime.min.time(), ET).replace(hour=16, minute=0)
    return start.astimezone(timezone.utc).timestamp(), end.astimezone(timezone.utc).timestamp()


def fetch_all_findings(day_start_ts: float, day_end_ts: float) -> list[dict]:
    """Paginate /api/findings (ALL tickers) backward via `before` until fully covering
    [day_start_ts, day_end_ts]. One comprehensive pull beats N per-ticker queries."""
    out: list[dict] = []
    seen_ids: set[int] = set()
    before: float | None = None
    page_num = 0
    while True:
        page_num += 1
        params = {"limit": 500}
        if before is not None:
            params["before"] = before
        r = requests.get(f"{BASE}/api/findings", params=params, timeout=30)
        r.raise_for_status()
        page = r.json().get("items", [])
        if not page:
            break
        oldest = min((f.get("detected_at") or 0) for f in page)
        for f in page:
            fid = f.get("id")
            ts = f.get("detected_at") or 0
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            if day_start_ts <= ts <= day_end_ts:
                out.append(f)
        print(f"  findings page {page_num}: {len(page)} rows, oldest={datetime.fromtimestamp(oldest, timezone.utc).strftime('%H:%M:%S')} UTC, kept so far={len(out)}")
        # NOTE: /api/findings applies a server-side min_price/max_price post-filter after
        # the DB query, so a returned page can be smaller than `limit` even when older
        # history still exists -- "page smaller than limit" is NOT a reliable end-of-data
        # signal here (this caused a real bug: a 499-row first page was misread as "done"
        # when the actual regular-hours window was still ~8 hours further back). Only stop
        # once we've paginated back before the window start, or the server returns nothing.
        if oldest < day_start_ts:
            break
        before = oldest - 0.001
    return out


def alpaca_bars_batch(env: dict, symbols: list[str], start_iso: str, end_iso: str) -> dict[str, list[dict]]:
    headers = {"APCA-API-KEY-ID": env["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET"]}
    base = env.get("ALPACA_DATA_BASE", "https://data.alpaca.markets")
    out: dict[str, list[dict]] = {s: [] for s in symbols}
    token = None
    while True:
        params = {"symbols": ",".join(symbols), "timeframe": "1Min", "start": start_iso, "end": end_iso,
                   "feed": env.get("ALPACA_FEED", "sip"), "limit": 10000, "sort": "asc"}
        if token:
            params["page_token"] = token
        r = requests.get(f"{base}/v2/stocks/bars", params=params, headers=headers, timeout=60)
        r.raise_for_status()
        body = r.json()
        for sym, rows in (body.get("bars") or {}).items():
            out.setdefault(sym, []).extend(rows)
        token = body.get("next_page_token")
        if not token:
            break
    return out


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def score_recall(movers: list[dict], findings_by_ticker: dict[str, list[dict]]) -> dict:
    counts = {str(int(t)): {"movers": 0, "seen": 0, "seen_before": 0, "actionable_before": 0} for t in THRESHOLDS}
    per_ticker_detail = []
    for row in movers:
        if not row.get("is_mover"):
            continue
        ticker = row["ticker"]
        f_list = sorted(findings_by_ticker.get(ticker, []), key=lambda f: f.get("detected_at") or 0)
        first_seen = f_list[0]["detected_at"] if f_list else None
        actionable_list = [f for f in f_list if str(f.get("actionable_rank") or "").upper() in ("A", "B")]
        first_actionable = actionable_list[0]["detected_at"] if actionable_list else None
        detail = {"ticker": ticker, "max_pct": row.get("max_pct"), "first_seen": first_seen, "first_actionable": first_actionable, "crossings": {}}
        for t in THRESHOLDS:
            key = str(int(t))
            crossing = row.get("crossings", {}).get(key)
            if not crossing:
                continue
            counts[key]["movers"] += 1
            cross_at = crossing["at"]
            seen = first_seen is not None
            seen_before = first_seen is not None and first_seen < cross_at
            actionable_before = first_actionable is not None and first_actionable < cross_at
            if seen:
                counts[key]["seen"] += 1
            if seen_before:
                counts[key]["seen_before"] += 1
            if actionable_before:
                counts[key]["actionable_before"] += 1
            detail["crossings"][key] = {"at": cross_at, "seen_before": seen_before, "actionable_before": actionable_before}
        per_ticker_detail.append(detail)

    summary = {}
    for t in THRESHOLDS:
        key = str(int(t))
        c = counts[key]
        n = c["movers"] or 1
        summary[key] = {
            "movers": c["movers"],
            "seen_pct": round(100.0 * c["seen"] / n, 1),
            "seen_before_cross_pct": round(100.0 * c["seen_before"] / n, 1),
            "actionable_before_cross_pct": round(100.0 * c["actionable_before"] / n, 1),
        }
    return {"by_threshold": summary, "detail": per_ticker_detail}


def score_precision(env: dict, actionable_findings: list[dict]) -> dict:
    by_ticker: dict[str, list[dict]] = {}
    for f in actionable_findings:
        by_ticker.setdefault(f["ticker"], []).append(f)

    tickers = list(by_ticker.keys())
    print(f"  fetching forward bars for {len(tickers)} tickers with actionable findings...")
    bars_by_ticker: dict[str, list[dict]] = {}
    for batch in chunks(tickers, 50):
        min_ts = min(f["detected_at"] for t in batch for f in by_ticker[t])
        max_ts = max(f["detected_at"] for t in batch for f in by_ticker[t]) + 310
        start_iso = datetime.fromtimestamp(min_ts, timezone.utc).isoformat().replace("+00:00", "Z")
        end_iso = datetime.fromtimestamp(max_ts, timezone.utc).isoformat().replace("+00:00", "Z")
        got = alpaca_bars_batch(env, batch, start_iso, end_iso)
        bars_by_ticker.update(got)
        time.sleep(0.2)

    rows = []
    for ticker, f_list in by_ticker.items():
        bars = sorted(bars_by_ticker.get(ticker, []), key=lambda b: b["t"])
        if not bars:
            continue
        parsed = [(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp(), b["h"], b["l"]) for b in bars]
        for f in f_list:
            entry_ts = f["detected_at"]
            entry_px = float(f.get("price") or 0)
            if entry_px <= 0:
                continue
            window = [(ts, h, l) for ts, h, l in parsed if entry_ts <= ts <= entry_ts + 300]
            if not window:
                continue
            peak_h = max(h for _, h, _ in window)
            trough_l = min(l for _, _, l in window)
            mfe = (peak_h / entry_px - 1.0) * 100.0
            mae = (trough_l / entry_px - 1.0) * 100.0
            rows.append({
                "ticker": ticker, "finding_id": f.get("id"), "detected_at": entry_ts, "price": entry_px,
                "stage": f.get("stage"), "mfe_300s_pct": round(mfe, 3), "mae_300s_pct": round(mae, 3),
                "net_opportunity_pct": round(mfe + mae, 3),
            })

    net_vals = [r["net_opportunity_pct"] for r in rows]
    summary = {}
    if net_vals:
        summary = {
            "n": len(net_vals),
            "mean": round(statistics.mean(net_vals), 3),
            "median": round(statistics.median(net_vals), 3),
            "sum": round(sum(net_vals), 1),
            "positive_rate": round(sum(1 for v in net_vals if v > 0) / len(net_vals), 3),
        }
    return {"summary": summary, "rows": rows}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--movers", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--date", default=None, help="YYYY-MM-DD, default today (ET)")
    args = p.parse_args()

    env = load_env()
    if not env.get("ALPACA_API_KEY"):
        raise SystemExit("ALPACA_API_KEY not found in .env")

    target_date = args.date or datetime.now(ET).strftime("%Y-%m-%d")
    day_start_ts, day_end_ts = regular_hours_window(target_date)
    print(f"Regular-hours window for {target_date}: "
          f"{datetime.fromtimestamp(day_start_ts, timezone.utc).strftime('%H:%M')} - "
          f"{datetime.fromtimestamp(day_end_ts, timezone.utc).strftime('%H:%M')} UTC")

    movers = [json.loads(l) for l in Path(args.movers).read_text(encoding="utf-8").splitlines() if l.strip()]
    mover_rows = [m for m in movers if m.get("is_mover")]
    print(f"Ground truth: {len(mover_rows)} regular-hours movers, {len(movers) - len(mover_rows)} control rows")

    print("Fetching all of today's Scout findings (comprehensive pull, all tickers)...")
    all_findings = fetch_all_findings(day_start_ts, day_end_ts)
    print(f"Total findings in regular-hours window: {len(all_findings)}")

    findings_by_ticker: dict[str, list[dict]] = {}
    for f in all_findings:
        findings_by_ticker.setdefault(f["ticker"], []).append(f)

    print("Scoring recall...")
    recall = score_recall(mover_rows, findings_by_ticker)
    for t in THRESHOLDS:
        s = recall["by_threshold"][str(int(t))]
        print(f"  +{int(t):>3}%: movers={s['movers']:4d}  seen={s['seen_pct']:5.1f}%  "
              f"seen_before_cross={s['seen_before_cross_pct']:5.1f}%  actionable_before_cross={s['actionable_before_cross_pct']:5.1f}%")

    actionable_findings = [f for f in all_findings if str(f.get("actionable_rank") or "").upper() in ("A", "B")]
    print(f"\nActionable (A/B) findings during regular hours: {len(actionable_findings)}")
    print("Scoring precision (independent Alpaca forward bars)...")
    precision = score_precision(env, actionable_findings)
    if precision["summary"]:
        s = precision["summary"]
        print(f"  n={s['n']}  mean={s['mean']}  median={s['median']}  sum={s['sum']}  positive_rate={s['positive_rate']:.1%}")

    report = {
        "date": target_date, "window": "regular_hours",
        "movers_total": len(mover_rows), "control_total": len(movers) - len(mover_rows),
        "findings_total": len(all_findings), "actionable_total": len(actionable_findings),
        "recall": recall, "precision": precision,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nJSON report: {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

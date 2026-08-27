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

from app.significance_tier import would_notify

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


SESSION_HOURS = {
    "premarket": ((4, 0), (9, 30)),
    "regular": ((9, 30), (16, 0)),
    "afterhours": ((16, 0), (20, 0)),
}


def session_window(target_date: str, session: str) -> tuple[float, float]:
    d = datetime.fromisoformat(target_date).date()
    (start_hour, start_minute), (end_hour, end_minute) = SESSION_HOURS[session]
    start = datetime.combine(d, datetime.min.time(), ET).replace(hour=start_hour, minute=start_minute)
    end = datetime.combine(d, datetime.min.time(), ET).replace(hour=end_hour, minute=end_minute)
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


def notification_eligible(finding: dict) -> bool:
    """Evaluate the production opportunity contract, not the old A/B proxy."""
    # Always recompute. Stored previews describe the code deployed when the
    # finding was captured and would contaminate a post-change replay.
    return would_notify(finding).get("would_notify") is True


def delivered(finding: dict) -> bool:
    return finding.get("notification_delivered_at") is not None


def qualified(finding: dict) -> bool:
    return bool(
        not finding.get("shadow_mode")
        and str(finding.get("actionable_rank") or "").upper() == "A"
        and str(finding.get("quality_label") or "").upper() == "CLEAN"
    )


def episode_phase(finding: dict) -> tuple[str, str]:
    stage = str(finding.get("stage") or "").upper()
    phase = "setup" if stage == "EARLY" else "confirmed" if stage in {"IGNITION", "BREAKOUT", "SURGE"} else stage.lower()
    episode = str(finding.get("hybrid_key") or f"{finding.get('ticker')}:{finding.get('episode_id', 0)}")
    return episode, phase


def first_per_episode_phase(findings: list[dict]) -> list[dict]:
    selected: dict[tuple[str, str], dict] = {}
    for finding in sorted(findings, key=lambda f: (float(f.get("detected_at") or 0), int(f.get("id") or 0))):
        selected.setdefault(episode_phase(finding), finding)
    return list(selected.values())


def score_recall(movers: list[dict], findings_by_ticker: dict[str, list[dict]]) -> dict:
    counts = {str(int(t)): {"movers": 0, "seen": 0, "seen_before": 0, "qualified_before": 0,
                            "eligible_before": 0, "delivered_before": 0} for t in THRESHOLDS}
    per_ticker_detail = []
    for row in movers:
        if not row.get("is_mover"):
            continue
        ticker = row["ticker"]
        f_list = sorted(findings_by_ticker.get(ticker, []), key=lambda f: f.get("detected_at") or 0)
        first_seen = f_list[0]["detected_at"] if f_list else None
        qualified_list = first_per_episode_phase([f for f in f_list if qualified(f)])
        eligible_list = first_per_episode_phase([f for f in f_list if notification_eligible(f)])
        delivered_list = first_per_episode_phase([f for f in f_list if delivered(f)])
        first_qualified = qualified_list[0]["detected_at"] if qualified_list else None
        first_eligible = eligible_list[0]["detected_at"] if eligible_list else None
        first_delivered = delivered_list[0]["notification_delivered_at"] if delivered_list else None
        detail = {"ticker": ticker, "max_pct": row.get("max_pct"), "first_seen": first_seen,
                  "first_qualified": first_qualified, "first_notification_eligible": first_eligible,
                  "first_delivered": first_delivered, "crossings": {}}
        for t in THRESHOLDS:
            key = str(int(t))
            crossing = row.get("crossings", {}).get(key)
            if not crossing:
                continue
            counts[key]["movers"] += 1
            cross_at = crossing["at"]
            seen = first_seen is not None
            seen_before = first_seen is not None and first_seen < cross_at
            qualified_before = first_qualified is not None and first_qualified < cross_at
            eligible_before = first_eligible is not None and first_eligible < cross_at
            delivered_before = first_delivered is not None and first_delivered < cross_at
            if seen:
                counts[key]["seen"] += 1
            if seen_before:
                counts[key]["seen_before"] += 1
            if qualified_before:
                counts[key]["qualified_before"] += 1
            if eligible_before:
                counts[key]["eligible_before"] += 1
            if delivered_before:
                counts[key]["delivered_before"] += 1
            detail["crossings"][key] = {"at": cross_at, "seen_before": seen_before,
                "qualified_before": qualified_before, "notification_eligible_before": eligible_before,
                "delivered_before": delivered_before}
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
            "qualified_before_cross_pct": round(100.0 * c["qualified_before"] / n, 1),
            "notification_eligible_before_cross_pct": round(100.0 * c["eligible_before"] / n, 1),
            "delivered_before_cross_pct": round(100.0 * c["delivered_before"] / n, 1),
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
    p.add_argument("--session", choices=sorted(SESSION_HOURS), default="regular")
    p.add_argument("--findings", help="Optional local JSON findings export; avoids querying production")
    args = p.parse_args()

    env = load_env()
    if not env.get("ALPACA_API_KEY"):
        raise SystemExit("ALPACA_API_KEY not found in .env")

    target_date = args.date or datetime.now(ET).strftime("%Y-%m-%d")
    day_start_ts, day_end_ts = session_window(target_date, args.session)
    print(f"{args.session} window for {target_date}: "
          f"{datetime.fromtimestamp(day_start_ts, timezone.utc).strftime('%H:%M')} - "
          f"{datetime.fromtimestamp(day_end_ts, timezone.utc).strftime('%H:%M')} UTC")

    movers = [json.loads(l) for l in Path(args.movers).read_text(encoding="utf-8").splitlines() if l.strip()]
    mover_rows = [m for m in movers if m.get("is_mover")]
    print(f"Ground truth: {len(mover_rows)} regular-hours movers, {len(movers) - len(mover_rows)} control rows")

    if args.findings:
        exported = json.loads(Path(args.findings).read_text(encoding="utf-8"))
        all_findings = [f for f in exported if day_start_ts <= float(f.get("detected_at") or 0) <= day_end_ts]
        print(f"Loaded local findings export: {args.findings}")
    else:
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
              f"seen_before_cross={s['seen_before_cross_pct']:5.1f}%  eligible_before_cross={s['notification_eligible_before_cross_pct']:5.1f}%  "
              f"delivered_before_cross={s['delivered_before_cross_pct']:5.1f}%")

    qualified_findings = first_per_episode_phase([f for f in all_findings if qualified(f)])
    actionable_findings = first_per_episode_phase([f for f in all_findings if notification_eligible(f)])
    delivered_findings = first_per_episode_phase([f for f in all_findings if delivered(f)])
    print(f"\nQualified={len(qualified_findings)} eligible={len(actionable_findings)} delivered={len(delivered_findings)} (episode/phase deduplicated)")
    print("Scoring precision (independent Alpaca forward bars)...")
    precision = score_precision(env, actionable_findings)
    if precision["summary"]:
        s = precision["summary"]
        print(f"  n={s['n']}  mean={s['mean']}  median={s['median']}  sum={s['sum']}  positive_rate={s['positive_rate']:.1%}")

    report = {
        "date": target_date, "window": args.session,
        "movers_total": len(mover_rows), "control_total": len(movers) - len(mover_rows),
        "findings_total": len(all_findings), "qualified_total": len(qualified_findings),
        "notification_eligible_total": len(actionable_findings), "delivered_total": len(delivered_findings),
        "recall": recall, "precision": precision,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nJSON report: {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

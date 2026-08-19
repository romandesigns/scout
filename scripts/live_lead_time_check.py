#!/usr/bin/env python3
"""
Scout Live Lead-Time Check (2026-08-19)

Answers a direct question: for a given list of tickers (e.g. today's real top movers from
an external screener), did Scout notice them BEFORE the move, or only after -- and by how
much lead or lag time? This is the live analog of the historical backtest's "seen-before-
cross" / "actionable-before-cross" metrics, applied to real tickers on today's actual session
instead of a replayed historical day.

Independent of Scout's own reporting: pulls real 1-min bars directly from Alpaca (not from
Scout's database) to establish, from raw tape data, when each ticker's move actually started
-- the same detector-blind-ground-truth discipline used by every offline backtest this week.

Usage:
  python -m scripts.live_lead_time_check YJ RDAC TNON EHGO BIVI MRVI ARCT VRAX MSS
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://srv1170872.tail86523.ts.net:8444"
THRESHOLDS = (10.0, 20.0, 50.0, 100.0)


def load_env() -> dict[str, str]:
    env = {}
    path = Path(".env")
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def fmt(ts: float | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S")


def scout_findings_today(ticker: str, day_start_ts: float) -> list[dict]:
    """Paginate backward with `before` until results fall entirely before today's session
    start -- /api/findings has no date filter and returns most-recent-first, so a single
    limit=500 page silently drops into prior days for high-frequency tickers (BIVI alone
    produced 500+ findings within a few hours)."""
    out: list[dict] = []
    before: float | None = None
    for _ in range(20):  # hard cap on pages, avoid runaway pagination
        params = {"ticker": ticker, "limit": 500}
        if before is not None:
            params["before"] = before
        r = requests.get(f"{BASE}/api/findings", params=params, timeout=15)
        r.raise_for_status()
        page = r.json().get("items", [])
        if not page:
            break
        todays = [f for f in page if (f.get("detected_at") or 0) >= day_start_ts]
        out.extend(todays)
        oldest = min((f.get("detected_at") or 0) for f in page)
        if oldest < day_start_ts or len(page) < 500:
            break
        before = oldest
    return out


def alpaca_bars(env: dict, ticker: str, day_start_iso: str) -> list[dict]:
    headers = {"APCA-API-KEY-ID": env["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET"]}
    base = env.get("ALPACA_DATA_BASE", "https://data.alpaca.markets")
    r = requests.get(f"{base}/v2/stocks/{ticker}/bars",
                      params={"timeframe": "1Min", "start": day_start_iso, "limit": 2000, "feed": "sip"},
                      headers=headers, timeout=20)
    r.raise_for_status()
    return r.json().get("bars", [])


def prev_close(env: dict, ticker: str) -> float | None:
    headers = {"APCA-API-KEY-ID": env["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET"]}
    base = env.get("ALPACA_DATA_BASE", "https://data.alpaca.markets")
    r = requests.get(f"{base}/v2/stocks/{ticker}/snapshot", params={"feed": "sip"}, headers=headers, timeout=15)
    if r.status_code != 200:
        return None
    snap = r.json()
    prev = snap.get("prevDailyBar") or {}
    return float(prev.get("c")) if prev.get("c") else None


def analyze(ticker: str, env: dict, day_start_iso: str, day_start_ts: float) -> None:
    print(f"\n=== {ticker} ===")
    try:
        base_price = prev_close(env, ticker)
    except Exception as exc:
        print(f"  prev close fetch failed: {exc}")
        base_price = None

    try:
        bars = alpaca_bars(env, ticker, day_start_iso)
    except Exception as exc:
        print(f"  bar fetch failed: {exc}")
        bars = []

    if not bars:
        print("  no bar data returned for today")
        return
    if base_price is None:
        base_price = bars[0]["o"]
        print(f"  (no prevDailyBar available, using first bar open ${base_price} as baseline)")
    else:
        print(f"  prior close baseline: ${base_price}")

    crossings: dict[float, str | None] = {t: None for t in THRESHOLDS}
    peak_pct = 0.0
    peak_ts = None
    for bar in bars:
        pct = (bar["h"] / base_price - 1.0) * 100.0
        if pct > peak_pct:
            peak_pct = pct
            peak_ts = bar["t"]
        for t in THRESHOLDS:
            if crossings[t] is None and pct >= t:
                crossings[t] = bar["t"]

    print(f"  peak so far: +{peak_pct:.1f}% at {peak_ts}")
    for t in THRESHOLDS:
        c = crossings[t]
        print(f"  crossed +{t:>5.0f}%: {c or 'not yet'}")

    try:
        findings = scout_findings_today(ticker, day_start_ts)
    except Exception as exc:
        print(f"  Scout findings fetch failed: {exc}")
        findings = []

    if not findings:
        print("  Scout: NO findings recorded for this ticker TODAY (may have prior-day history, not counted)")
        return

    findings_sorted = sorted(findings, key=lambda f: f.get("detected_at") or 0)
    first = findings_sorted[0]
    actionable = [f for f in findings_sorted if str(f.get("actionable_rank") or "").upper() in ("A", "B")]
    first_actionable = actionable[0] if actionable else None

    print(f"  Scout: {len(findings)} findings recorded, first stage='{first.get('stage')}' "
          f"rank={first.get('actionable_rank')} at {fmt(first.get('detected_at'))} UTC (price ${first.get('price')})")
    if first_actionable:
        print(f"  Scout: first ACTIONABLE (rank A/B) '{first_actionable.get('stage')}' at "
              f"{fmt(first_actionable.get('detected_at'))} UTC (price ${first_actionable.get('price')})")
    else:
        print("  Scout: never reached an actionable (A/B) rank today")

    # Compare against the earliest meaningful threshold crossing (+10%) as "the move started"
    first_cross_iso = crossings[10.0]
    if first_cross_iso and first.get("detected_at"):
        cross_ts = datetime.fromisoformat(first_cross_iso.replace("Z", "+00:00")).timestamp()
        delta = first.get("detected_at") - cross_ts
        verb = "BEFORE" if delta < 0 else "AFTER"
        print(f"  -> Scout's first detection was {abs(delta)/60:.1f} min {verb} the move crossed +10%")
    if first_cross_iso and first_actionable:
        cross_ts = datetime.fromisoformat(first_cross_iso.replace("Z", "+00:00")).timestamp()
        delta = first_actionable.get("detected_at") - cross_ts
        verb = "BEFORE" if delta < 0 else "AFTER"
        print(f"  -> Scout's first ACTIONABLE detection was {abs(delta)/60:.1f} min {verb} the move crossed +10%")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("tickers", nargs="+")
    args = p.parse_args()
    env = load_env()
    if not env.get("ALPACA_API_KEY"):
        print("ALPACA_API_KEY not found in .env", file=sys.stderr)
        return 1
    day_start_dt = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today = day_start_dt.strftime("%Y-%m-%dT00:00:00Z")
    day_start_ts = day_start_dt.timestamp()
    for t in args.tickers:
        analyze(t.upper(), env, today, day_start_ts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

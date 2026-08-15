from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from app.charts import render_detection_chart
from app.config import settings
from app.db import Store
from app.indicators import pct_change
from app.market import MarketWatcher, ALLOWED_EXCHANGES, trading_session_key
from app.models import Bucket, Finding, SymbolState

ET = ZoneInfo(settings.timezone)


def headers():
    return {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i+n]


def get_assets() -> list[str]:
    r = requests.get(
        f"{settings.alpaca_trading_base}/v2/assets",
        params={"status": "active", "asset_class": "us_equity"},
        headers=headers(), timeout=30,
    )
    r.raise_for_status()
    return [
        str(a["symbol"]).upper()
        for a in r.json()
        if a.get("tradable")
        and str(a.get("exchange", "")).upper() in ALLOWED_EXCHANGES
        and a.get("symbol")
    ]


def get_bars(symbols: list[str], timeframe: str, start: datetime, end: datetime, feed: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    token = None
    while True:
        params = {
            "symbols": ",".join(symbols),
            "timeframe": timeframe,
            "start": start.astimezone(timezone.utc).isoformat(),
            "end": end.astimezone(timezone.utc).isoformat(),
            "feed": feed,
            "limit": 10000,
            "sort": "asc",
        }
        if token:
            params["page_token"] = token
        r = requests.get(f"{settings.alpaca_data_base}/v2/stocks/bars", params=params, headers=headers(), timeout=60)
        r.raise_for_status()
        body = r.json()
        for sym, rows in (body.get("bars") or {}).items():
            out[sym].extend(rows)
        token = body.get("next_page_token")
        if not token:
            return out


def get_trades(symbol: str, start: datetime, end: datetime, feed: str) -> list[dict]:
    rows: list[dict] = []
    token = None
    while True:
        params = {
            "symbols": symbol,
            "start": start.astimezone(timezone.utc).isoformat(),
            "end": end.astimezone(timezone.utc).isoformat(),
            "feed": feed,
            "limit": 10000,
            "sort": "asc",
        }
        if token:
            params["page_token"] = token
        r = requests.get(f"{settings.alpaca_data_base}/v2/stocks/trades", params=params, headers=headers(), timeout=60)
        r.raise_for_status()
        body = r.json()
        rows.extend((body.get("trades") or {}).get(symbol, []))
        token = body.get("next_page_token")
        if not token:
            return rows


def previous_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def session_windows(target: date, sessions: str) -> list[tuple[str, str, datetime, datetime]]:
    """Return feed windows for one U.S. equity trade date.

    The overnight portion begins at 8 PM ET on the prior calendar day and
    belongs to target's trade date. SIP covers pre-market, regular, and
    after-hours from 4 AM through 8 PM ET.
    """
    target_midnight = datetime.combine(target, datetime.min.time(), ET)
    prior = target_midnight - timedelta(days=1)
    windows: list[tuple[str, str, datetime, datetime]] = []

    if sessions in {"all", "overnight"} and settings.enable_overnight_stream:
        windows.append(("overnight", settings.alpaca_overnight_feed, prior.replace(hour=20), target_midnight.replace(hour=4)))

    sip_ranges = {
        "premarket": (4, 9, 30),
        "regular": (9, 16, 0),
        "afterhours": (16, 20, 0),
    }
    if sessions == "all":
        windows.append(("sip", settings.alpaca_feed, target_midnight.replace(hour=4), target_midnight.replace(hour=20)))
    elif sessions in sip_ranges:
        start_hour, end_hour, end_min = sip_ranges[sessions]
        start_min = 30 if sessions == "regular" else 0
        windows.append((sessions, settings.alpaca_feed, target_midnight.replace(hour=start_hour, minute=start_min), target_midnight.replace(hour=end_hour, minute=end_min)))
    return windows


def prefilter(target: date, symbols: list[str], sessions: str) -> list[tuple[datetime, str, str]]:
    # One-minute cross-session prefilter. Exact 15-second qualification happens in refine().
    hits: list[tuple[datetime, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, feed, start, end in session_windows(target, sessions):
        print(f"prefilter session={label} feed={feed} window={start.isoformat()}..{end.isoformat()}")
        for idx, batch in enumerate(chunks(symbols, 200), 1):
            bars = get_bars(batch, "1Min", start, end, feed)
            for sym, rows in bars.items():
                if (label, sym) in seen:
                    continue
                vols: list[float] = []
                closes: list[float] = []
                for row in rows:
                    ts = datetime.fromisoformat(row["t"].replace("Z", "+00:00")).astimezone(ET)
                    px = float(row["c"])
                    vol = float(row["v"])
                    baseline = max(1.0, statistics.median(vols[-10:])) if len(vols) >= 6 else None
                    one_min = pct_change(closes[-1], px) if closes else 0.0
                    if settings.min_price <= px <= settings.max_price and baseline and vol / baseline >= 2.5 and one_min >= 1.0:
                        hits.append((ts, sym, feed))
                        seen.add((label, sym))
                        break
                    vols.append(vol)
                    closes.append(px)
            print(f"prefilter {label} batch {idx}: total candidates {len(hits)}")
    # Most recent first across all enabled sessions.
    return sorted(hits, key=lambda x: x[0], reverse=True)


class ReplayDispatcher:
    """Capture findings without sending notifications, email, or writing live findings."""

    def __init__(self):
        self.items: list[Finding] = []

    async def emit(self, finding: Finding, buckets=None, current=None) -> int:
        self.items.append(finding)
        finding.finding_id = len(self.items)
        return finding.finding_id


async def refine(sym: str, seed_time: datetime, feed: str, store: Store) -> tuple[Finding, list[Bucket], Bucket | None] | None:
    """Replay raw trades through the same V5 metric + emission path used live.

    The prefilter only narrows REST workload. Qualification is performed by
    MarketWatcher._metrics/_maybe_emit, including EARLY, SURGE, BREAKOUT,
    STAIRCASE, IGNITION, and REARM rules. Notifications are disabled by using
    ReplayDispatcher.
    """
    start = seed_time - timedelta(minutes=4)
    end = seed_time + timedelta(minutes=5)
    trades = get_trades(sym, start, end, feed)
    capture = ReplayDispatcher()
    market = MarketWatcher(store, capture)
    state = SymbolState(sym, settings.bucket_seconds, settings.keep_buckets)
    market.states[sym] = state

    first_snapshot: tuple[list[Bucket], Bucket | None] | None = None

    for tr in trades:
        ts = datetime.fromisoformat(tr["t"].replace("Z", "+00:00")).timestamp()
        px = float(tr["p"])
        size = float(tr["s"])
        state.update_trade(ts, px, size, trading_session_key(ts))

        now_ms = ts * 1000
        if now_ms - state.last_fast_eval_at * 1000 >= settings.fast_path_min_interval_ms:
            state.last_fast_eval_at = ts
            metrics = market._metrics(state, ts)
            if metrics:
                await market._maybe_emit(state, metrics, ts, fast=True)

        if ts - state.last_eval_at >= settings.eval_seconds:
            state.last_eval_at = ts
            metrics = market._metrics(state, ts)
            if metrics:
                await market._maybe_emit(state, metrics, ts, fast=False)

        if capture.items and first_snapshot is None:
            first_snapshot = market.snapshot(sym)
            break

    if not capture.items:
        return None

    finding = capture.items[0]
    buckets, current = first_snapshot or ([], None)
    return finding, buckets, current


def bars_for_trade_date(symbol: str, target: date) -> list[dict]:
    combined: list[dict] = []
    for _, feed, start, end in session_windows(target, "all"):
        combined.extend(get_bars([symbol], "1Min", start, end, feed).get(symbol, []))
    return sorted(combined, key=lambda row: row["t"])


def outcome_from_bars(f: Finding, rows: list[dict], session_end: datetime) -> dict:
    horizons = {"max_1m_pct": 60, "max_5m_pct": 300, "max_15m_pct": 900}
    out: dict[str, float | None] = {}
    peak = f.price
    peak_t = f.detected_at
    for name, seconds in horizons.items():
        vals = []
        for row in rows:
            ts = datetime.fromisoformat(row["t"].replace("Z", "+00:00")).timestamp()
            if f.detected_at <= ts <= f.detected_at + seconds:
                vals.append((float(row["h"]), ts))
        if vals:
            hi, ts = max(vals)
            out[name] = pct_change(f.price, hi)
            if hi > peak:
                peak, peak_t = hi, ts
        else:
            out[name] = None

    session_vals = []
    end_ts = session_end.timestamp()
    for row in rows:
        ts = datetime.fromisoformat(row["t"].replace("Z", "+00:00")).timestamp()
        if f.detected_at <= ts <= end_ts:
            session_vals.append((float(row["h"]), ts))
    if session_vals:
        hi, ts = max(session_vals)
        out["max_session_pct"] = pct_change(f.price, hi)
        if hi > peak:
            peak, peak_t = hi, ts
    else:
        out["max_session_pct"] = None
    out["time_to_peak_seconds"] = peak_t - f.detected_at
    return out


def main():
    ap = argparse.ArgumentParser(description="Replay all enabled market sessions and return the last actual Scout detections.")
    ap.add_argument("--date", help="U.S. equity trade date YYYY-MM-DD; defaults to previous weekday")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--max-prefilter", type=int, default=100, help="How many recent 1m candidates to refine with trade data")
    ap.add_argument(
        "--sessions",
        choices=["all", "overnight", "premarket", "regular", "afterhours"],
        default="all",
        help="Session scope. Default: all enabled sessions.",
    )
    args = ap.parse_args()

    target = date.fromisoformat(args.date) if args.date else previous_weekday(datetime.now(ET).date())
    out_dir = settings.data_dir / f"replay-{target.isoformat()}-{args.sessions}"
    out_dir.mkdir(parents=True, exist_ok=True)

    symbols = get_assets()
    print("active listed symbols:", len(symbols))
    candidates = prefilter(target, symbols, args.sessions)
    print("prefilter candidates:", len(candidates))

    store = Store(settings.data_dir / "replay-state.db")
    detections: list[Finding] = []
    seen_symbols: set[str] = set()
    for seed_time, sym, feed in candidates[:args.max_prefilter]:
        if sym in seen_symbols:
            continue
        seen_symbols.add(sym)
        print("refining", sym, seed_time, "feed", feed)
        hit = asyncio.run(refine(sym, seed_time, feed, store))
        if hit:
            f, buckets, current = hit
            path = render_detection_chart(f, buckets, current, out_dir)
            f.chart_path = path
            detections.append(f)
            if len(detections) >= args.limit:
                break

    report = []
    session_end = datetime.combine(target, datetime.min.time(), ET).replace(hour=20)
    for f in detections:
        det = datetime.fromtimestamp(f.detected_at, ET)
        rows = bars_for_trade_date(f.ticker, target)
        outcome = outcome_from_bars(f, rows, session_end)
        report.append({
            "ticker": f.ticker,
            "stage": f.stage,
            "detected_at_et": det.isoformat(),
            "price": f.price,
            "score": f.score,
            "signals": f.signals,
            "vol_ratio_15s": f.vol_ratio_15s,
            "vol_ratio_30s": f.vol_ratio_30s,
            "change_3s_pct": f.change_3s_pct,
            "change_5s_pct": f.change_5s_pct,
            "change_10s_pct": f.change_10s_pct,
            "change_15s_pct": f.change_15s_pct,
            "change_30s_pct": f.change_30s_pct,
            "change_60s_pct": f.change_60s_pct,
            "extension_pct": f.extension_pct,
            "breakout_level": f.breakout_level,
            "breakout_window": f.breakout_window,
            "ema9": f.ema9,
            "ema21": f.ema21,
            "vwap": f.vwap,
            "evidence": f.evidence,
            "chart": f.chart_path,
            **outcome,
        })

    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    with (out_dir / "report.csv").open("w", newline="") as fh:
        fields = [
            "ticker", "stage", "detected_at_et", "price", "score", "signals",
            "vol_ratio_15s", "vol_ratio_30s", "change_3s_pct", "change_5s_pct",
            "change_10s_pct", "change_15s_pct", "change_30s_pct", "change_60s_pct", "extension_pct",
            "breakout_level", "breakout_window",
            "max_1m_pct", "max_5m_pct", "max_15m_pct", "max_session_pct",
            "time_to_peak_seconds", "chart",
        ]
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in report:
            w.writerow({k: r.get(k) for k in fields})

    print(json.dumps(report, indent=2))
    print("Replay output:", out_dir)


if __name__ == "__main__":
    main()

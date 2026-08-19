#!/usr/bin/env python3
"""
Scout Live Observer (2026-08-19)

Built in direct response to: "can you watch for all the live incoming to better diagnose
whether the app is doing what it's supposed to do?" Watches Scout's live PRODUCTION output
continuously and keeps a running, on-disk record so detection accuracy can be judged from
real evidence instead of eyeballing the dashboard -- the same discipline this project has
applied to every offline backtest this week, now applied to the live system.

Read-only. Makes no changes to Scout itself -- only GETs and one SSE subscription against
the already-existing production API.

Runs four concurrent loops against the production API:
  1. SSE event logger    -- subscribes to /api/events, logs every event verbatim as it
                             happens (findings that were dispatched, halts, etc).
  2. Findings poller      -- polls /api/findings?limit=200 every 30s and logs anything not
                             already seen, by finding id. This is the comprehensive source
                             (includes shadow-mode / non-actionable findings that never get
                             dispatched and so never appear on the SSE "finding" event).
  3. Health monitor       -- polls /api/status every 30s, logs Rust-bridge queue health,
                             feed connection state, and tracked_states/universe size.
                             Prints an immediate console ALERT the moment backpressure
                             turns "saturated" or a feed disconnects -- this closes exactly
                             the alerting gap that let 2026-08-19's queue-stall incident go
                             undetected until someone manually polled the endpoint (see
                             MILESTONES/2026-08-19-006).
  4. Recall check         -- polls /api/market/gainers every 3 min. That endpoint already
                             embeds Scout's own tracking status per gainer (a ready-made,
                             zero-extra-cost ground-truth cross-reference) -- any gainer
                             with no "scout" entry is a live miss: a real mover Scout is not
                             tracking at all, structurally worse than any gate-tuning
                             precision/recall trade-off, since an untracked symbol can never
                             be found regardless of how any quality gate is configured.

Output: NDJSON logs under data/live-observer/, one file per stream per UTC date (a session
spanning midnight rolls to a new file automatically, it doesn't corrupt one growing file).
A separate `--summarize` mode reads the logs and prints a scorecard without needing the
watcher itself running -- point it at any date already collected.

Usage:
  python -m scripts.live_observer --base https://srv1170872.tail86523.ts.net:8444
  python -m scripts.live_observer --summarize                     # today's report
  python -m scripts.live_observer --summarize --date 2026-08-19   # a specific day
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

DEFAULT_BASE = "https://srv1170872.tail86523.ts.net:8444"
DEFAULT_OUT_DIR = Path("data/live-observer")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def log_path(out_dir: Path, stream: str, date: str) -> Path:
    return out_dir / f"{stream}-{date}.ndjson"


def append_ndjson(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def log_line(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# 1. SSE event logger
# ---------------------------------------------------------------------------

async def sse_logger(session: aiohttp.ClientSession, base: str, out_dir: Path) -> None:
    backoff = 2.0
    while True:
        try:
            async with session.get(f"{base}/api/events", timeout=aiohttp.ClientTimeout(total=None)) as resp:
                resp.raise_for_status()
                log_line("SSE connected")
                backoff = 2.0
                event_type, data_lines = None, []
                async for raw in resp.content:
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                    if line.startswith("event:"):
                        event_type = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:"):].strip())
                    elif line == "":
                        if event_type and data_lines:
                            try:
                                payload = json.loads("".join(data_lines))
                            except json.JSONDecodeError:
                                payload = {"raw": "".join(data_lines)}
                            if event_type not in ("ready",):
                                record = {"received_at": time.time(), "sse_event": event_type, **payload}
                                append_ndjson(log_path(out_dir, "sse", today_utc()), record)
                                if event_type == "finding":
                                    p = payload.get("payload", {})
                                    log_line(f"SSE finding: {p.get('ticker')} {p.get('stage')} "
                                              f"rank={p.get('actionable_rank')} q={p.get('quality_label')}")
                                elif event_type == "halt":
                                    log_line(f"SSE halt: {payload.get('payload')}")
                        event_type, data_lines = None, []
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_line(f"SSE disconnected ({exc}); reconnecting in {backoff:.0f}s")
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 1.7)


# ---------------------------------------------------------------------------
# 2. Findings poller (comprehensive -- includes shadow/non-dispatched findings)
# ---------------------------------------------------------------------------

async def findings_poller(session: aiohttp.ClientSession, base: str, out_dir: Path, interval: float) -> None:
    seen: set[int] = set()
    # Prime `seen` from today's already-logged findings so a restart doesn't re-log history.
    for rec in read_ndjson(log_path(out_dir, "findings", today_utc())):
        fid = rec.get("id")
        if fid is not None:
            seen.add(int(fid))
    while True:
        try:
            async with session.get(f"{base}/api/findings", params={"limit": 200},
                                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                payload = await resp.json()
            rows = payload.get("items", []) if isinstance(payload, dict) else payload
            new = 0
            for row in rows:
                fid = row.get("id")
                if fid is None or int(fid) in seen:
                    continue
                seen.add(int(fid))
                new += 1
                append_ndjson(log_path(out_dir, "findings", today_utc()),
                              {"observed_at": time.time(), **row})
            if new:
                log_line(f"findings poll: +{new} new (of {len(rows)} returned)")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_line(f"findings poll failed: {exc}")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# 3. Health monitor
# ---------------------------------------------------------------------------

async def health_poller(session: aiohttp.ClientSession, base: str, out_dir: Path, interval: float) -> None:
    last_backpressure: str | None = None
    last_disconnects: dict[str, int] = {}
    while True:
        try:
            async with session.get(f"{base}/api/status", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                status = await resp.json()
            bridge = status.get("hybrid", {}).get("rust_bridge", {})
            feeds = status.get("feeds", {}).get("health", {})
            record = {
                "observed_at": time.time(),
                "queue_depth": bridge.get("queue_depth"),
                "queue_capacity": bridge.get("queue_capacity"),
                "backpressure": bridge.get("backpressure"),
                "dropped": bridge.get("dropped"),
                "submitted": bridge.get("submitted"),
                "written": bridge.get("written"),
                "candidates": bridge.get("candidates"),
                "last_submit_at": bridge.get("last_submit_at"),
                "last_candidate_at": bridge.get("last_candidate_at"),
                "restarts": bridge.get("restarts"),
                "tracked_states": status.get("tracked_states"),
                "universe": status.get("universe"),
                "active_halts": status.get("active_halts"),
                "feeds": {name: h.get("disconnects") for name, h in feeds.items()},
            }
            append_ndjson(log_path(out_dir, "health", today_utc()), record)

            backpressure = bridge.get("backpressure")
            if backpressure == "saturated" and last_backpressure != "saturated":
                log_line(f"ALERT: Rust bridge backpressure SATURATED "
                          f"(depth={bridge.get('queue_depth')}/{bridge.get('queue_capacity')}, "
                          f"dropped={bridge.get('dropped')})")
            elif backpressure != "saturated" and last_backpressure == "saturated":
                log_line("Rust bridge backpressure recovered")
            last_backpressure = backpressure

            for name, h in feeds.items():
                d = h.get("disconnects")
                if d is not None and name in last_disconnects and d > last_disconnects[name]:
                    log_line(f"ALERT: feed '{name}' disconnected (disconnect count {last_disconnects[name]} -> {d})")
                if d is not None:
                    last_disconnects[name] = d
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_line(f"health poll failed: {exc}")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# 4. Recall check (real movers Scout isn't tracking at all)
# ---------------------------------------------------------------------------

async def recall_poller(session: aiohttp.ClientSession, base: str, out_dir: Path, interval: float) -> None:
    alerted_today: set[str] = set()
    while True:
        try:
            async with session.get(f"{base}/api/market/gainers", params={"top": 30},
                                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                payload = await resp.json()
            items = payload.get("items", [])
            record = {
                "observed_at": time.time(),
                "gainers": [
                    {
                        "symbol": it.get("symbol"), "percent_change": it.get("percent_change"),
                        "price": it.get("price"), "tracked": bool(it.get("scout")),
                        "stage": (it.get("scout") or {}).get("stage"),
                    }
                    for it in items
                ],
            }
            append_ndjson(log_path(out_dir, "recall", today_utc()), record)
            misses = [it for it in items if not it.get("scout")]
            currently_missing = {it.get("symbol") for it in misses}
            for it in misses:
                sym = it.get("symbol")
                if sym not in alerted_today:
                    alerted_today.add(sym)
                    pct = it.get("percent_change")
                    pct_str = f"{pct:.1f}%" if isinstance(pct, (int, float)) else "?"
                    log_line(f"ALERT: live recall miss -- {sym} +{pct_str} "
                             f"@ ${it.get('price')} has NO Scout tracking entry")
            # Drop symbols that are no longer missing so a later re-drop alerts again.
            alerted_today &= currently_missing
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_line(f"recall poll failed: {exc}")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run(base: str, out_dir: Path) -> None:
    connector = aiohttp.TCPConnector(limit=8)
    async with aiohttp.ClientSession(connector=connector) as session:
        log_line(f"Live observer starting against {base}, logging to {out_dir.resolve()}")
        await asyncio.gather(
            sse_logger(session, base, out_dir),
            findings_poller(session, base, out_dir, interval=30.0),
            health_poller(session, base, out_dir, interval=30.0),
            recall_poller(session, base, out_dir, interval=180.0),
        )


# ---------------------------------------------------------------------------
# Summarize mode
# ---------------------------------------------------------------------------

def summarize(out_dir: Path, date: str) -> None:
    findings = read_ndjson(log_path(out_dir, "findings", date))
    sse = read_ndjson(log_path(out_dir, "sse", date))
    health = read_ndjson(log_path(out_dir, "health", date))
    recall = read_ndjson(log_path(out_dir, "recall", date))

    print(f"===== Scout live observer report -- {date} (UTC) =====\n")

    print(f"-- Findings (comprehensive poll, all quality levels): {len(findings)} logged --")
    if findings:
        by_stage = Counter(f.get("stage") for f in findings)
        by_rank = Counter(f.get("actionable_rank") for f in findings)
        by_quality = Counter(f.get("quality_label") for f in findings)
        print("  by stage:  ", dict(by_stage.most_common()))
        print("  by rank:   ", dict(by_rank.most_common()))
        print("  by quality:", dict(by_quality.most_common()))
    dispatched = [e for e in sse if e.get("sse_event") == "finding"]
    print(f"  dispatched (passed notification gate, via SSE): {len(dispatched)}")

    print(f"\n-- Health ({len(health)} samples) --")
    if health:
        saturated = [h for h in health if h.get("backpressure") == "saturated"]
        print(f"  saturated samples: {len(saturated)} / {len(health)}")
        max_dropped = max((h.get("dropped") or 0) for h in health)
        print(f"  max cumulative dropped-trade counter seen: {max_dropped}")
        restarts = max((h.get("restarts") or 0) for h in health)
        print(f"  max restarts counter seen: {restarts}")
        tracked = [h.get("tracked_states") for h in health if h.get("tracked_states") is not None]
        if tracked:
            print(f"  tracked_states range: {min(tracked)}-{max(tracked)}")
    else:
        print("  no health samples logged for this date")

    print(f"\n-- Recall (gainers cross-check, {len(recall)} polls) --")
    if recall:
        ever_missed: dict[str, float] = {}
        for rec in recall:
            for g in rec.get("gainers", []):
                if not g.get("tracked"):
                    sym = g.get("symbol")
                    ever_missed.setdefault(sym, rec["observed_at"])
        if ever_missed:
            print(f"  {len(ever_missed)} distinct symbol(s) seen as a top-30 gainer with NO Scout tracking entry at least once:")
            for sym, ts in sorted(ever_missed.items()):
                print(f"    {sym}  (first seen missing at {datetime.fromtimestamp(ts, timezone.utc).strftime('%H:%M:%S')} UTC)")
        else:
            print("  no misses observed -- every top-30 gainer polled had a Scout tracking entry")
    else:
        print("  no recall samples logged for this date")

    print(f"\nRaw logs: {out_dir.resolve()}/*-{date}.ndjson")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", default=DEFAULT_BASE)
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.add_argument("--summarize", action="store_true")
    p.add_argument("--date", default=None, help="UTC date (YYYY-MM-DD) for --summarize; default today")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    if args.summarize:
        summarize(out_dir, args.date or today_utc())
        return 0

    try:
        asyncio.run(run(args.base, out_dir))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

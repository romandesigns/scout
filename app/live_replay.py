"""On-demand replay of a real Alpaca historical window through Scout's actual
production detector (`app.replay.run_dataset`, the same engine
`scripts/historical_backtest.py` uses for offline validation), so a single
Scout Development query can answer "what would Scout have flagged here" --
not "what did Scout already have stored" the way `evaluate_ticker`'s
stored-detection lookup does.

This exists because the two are genuinely different questions: Scout Development
originally only visualized detections already sitting in the database. A ticker
Scout never actually watched at that historical moment (or a window predating
this deployment) has nothing stored, so nothing was ever marked -- not because
detection failed, but because detection never ran. This module runs it, live,
for the requested window, fully isolated (a throwaway SQLite state file, no
live notifications -- see `ReplayDispatcher` in `app/replay.py`).
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import time
import uuid
from dataclasses import fields as dataclass_fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import settings
from .models import Finding
from .replay import MarketEvent, SCHEMA_VERSION, run_dataset, write_events

MAX_LIVE_REPLAY_SECONDS = 4 * 60 * 60  # keep interactive queries responsive
_FINDING_FIELD_NAMES = {field.name for field in dataclass_fields(Finding)}


def _get_trades(ticker: str, start: datetime, end: datetime, feed: str) -> list[dict]:
    # Imported lazily: scripts/ isn't a normal runtime dependency of app/, and
    # importing it eagerly would make every app.* import pull in a CLI script.
    from scripts.replay_last10 import get_trades
    return get_trades(ticker, start, end, feed)


def _get_quotes(ticker: str, start: datetime, end: datetime, feed: str) -> list[dict]:
    rows: list[dict] = []
    token = None
    while True:
        params = {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "feed": feed, "limit": 10000, "sort": "asc",
        }
        if token:
            params["page_token"] = token
        response = requests.get(
            f"{settings.alpaca_data_base}/v2/stocks/{ticker}/quotes", params=params,
            headers={"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        rows.extend(payload.get("quotes") or [])
        token = payload.get("next_page_token")
        if not token:
            return rows


def _rust_binary() -> Path:
    configured = Path(settings.rust_perception_binary)
    candidates = [
        configured,
        Path(__file__).resolve().parents[1] / "rust/market-replay/target/release/scout-market-replay.exe",
        Path(__file__).resolve().parents[1] / "rust/market-replay/target/debug/scout-market-replay.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Rust perception binary not found; checked: {', '.join(map(str, candidates))}")


def run_rust_detector(ticker: str, start_ts: float, end_ts: float, feed: str | None = None,
                      output_root: Path | None = None) -> dict[str, Any]:
    """Replay the canonical live market-event stream through the Rust engine."""
    ticker = ticker.strip().upper()
    if end_ts <= start_ts or end_ts - start_ts > MAX_LIVE_REPLAY_SECONDS:
        raise ValueError("Rust replay requires a positive window no longer than 4 hours")
    if not settings.alpaca_key or not settings.alpaca_secret:
        raise ValueError("ALPACA_API_KEY and ALPACA_API_SECRET are required for Rust replay")
    feed = feed or settings.alpaca_feed
    start_dt = datetime.fromtimestamp(start_ts, timezone.utc)
    end_dt = datetime.fromtimestamp(end_ts, timezone.utc)
    trades = _get_trades(ticker, start_dt, end_dt, feed)
    quotes = _get_quotes(ticker, start_dt, end_dt, feed)
    raw_events: list[tuple[float, str, dict]] = []
    for row in trades:
        ts = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).timestamp()
        raw_events.append((ts, "trade", {"price": float(row["p"]), "size": float(row["s"])}))
    quote_events: dict[int, tuple[float, str, dict]] = {}
    for row in quotes:
        ts = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).timestamp()
        bid, ask = float(row.get("bp") or 0), float(row.get("ap") or 0)
        if bid <= 0 or ask <= bid:
            continue
        quote_events[int(ts)] = (ts, "quote", {"price": (bid + ask) / 2, "size": 0.0,
                                 "bid_price": bid, "ask_price": ask,
                                 "bid_size": float(row.get("bs") or 0), "ask_size": float(row.get("as") or 0)})
    # Match the live bridge's one-quote-per-second throttle while retaining the
    # last quote state observed in each second.
    raw_events.extend(quote_events.values())
    raw_events.sort(key=lambda item: (item[0], 0 if item[1] == "quote" else 1))
    if not raw_events:
        return {"status": "NO_EVENTS", "findings": [], "evaluations": [], "processed_events": 0}
    root = output_root or (settings.data_dir / "replays" / "adhoc")
    root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    dataset = root / f"rust-{ticker}-{token}.ndjson"
    report_path = root / f"rust-{ticker}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{token}.json"
    events = [MarketEvent(schema=SCHEMA_VERSION, event_type=kind, symbol=ticker, source_ts=ts,
                          received_ts=ts, sequence=index, feed=feed, payload=payload)
              for index, (ts, kind, payload) in enumerate(raw_events, 1)]
    write_events(dataset, events)
    try:
        completed = subprocess.run([str(_rust_binary()), str(dataset), "--output", str(report_path)],
                                   capture_output=True, text=True, timeout=120, check=False)
        if completed.returncode:
            raise RuntimeError(f"Rust replay failed ({completed.returncode}): {completed.stderr[-1000:]}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
    finally:
        dataset.unlink(missing_ok=True)
    return {"status": "OK", "findings": report.get("candidates") or [],
            "evaluations": report.get("evaluations") or [],
            "processed_events": report.get("processed_events", 0),
            "integrity": report.get("integrity") or {}, "engine": report.get("engine"),
            "evaluation_trace_path": str(report_path)}


def run_live_detector(ticker: str, start_ts: float, end_ts: float, feed: str | None = None,
                      output_root: Path | None = None) -> dict[str, Any]:
    """Fetch real Alpaca trades for [start_ts, end_ts) and replay them through
    Scout's actual production detector. Returns a dict with the raw
    `app.replay.run_dataset` report plus `findings` normalized to the same
    shape `Store.list_findings` rows use (an `id` key, `mode: SIMULATION`),
    so callers can feed them straight into existing detection-rendering code.
    """
    ticker = ticker.strip().upper()
    if end_ts <= start_ts:
        raise ValueError("live replay window end must be after start")
    if end_ts - start_ts > MAX_LIVE_REPLAY_SECONDS:
        raise ValueError(f"live replay window cannot exceed {MAX_LIVE_REPLAY_SECONDS // 3600} hours "
                          f"(tick-level replay is slower than the stored-detection chart lookup)")
    if not settings.alpaca_key or not settings.alpaca_secret:
        raise ValueError("ALPACA_API_KEY and ALPACA_API_SECRET are required for live replay")

    feed = feed or settings.alpaca_feed
    start_dt = datetime.fromtimestamp(start_ts, timezone.utc)
    end_dt = datetime.fromtimestamp(end_ts, timezone.utc)
    trades = _get_trades(ticker, start_dt, end_dt, feed)
    if not trades:
        return {"status": "NO_TRADES", "findings": [], "processed_events": 0}

    events = []
    for sequence, row in enumerate(trades, 1):
        ts = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00")).timestamp()
        events.append(MarketEvent(
            schema=SCHEMA_VERSION, event_type="trade", symbol=ticker,
            source_ts=ts, received_ts=ts, sequence=sequence, feed=feed,
            payload={"price": float(row["p"]), "size": float(row["s"]), "exchange": row.get("x"), "conditions": row.get("c", [])},
        ))

    tmp_root = output_root or (settings.data_dir / "replays" / "adhoc")
    tmp_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dataset_path = tmp_root / f"live-{ticker}-{stamp}-{uuid.uuid4().hex[:8]}.ndjson"
    write_events(dataset_path, events)

    try:
        report = asyncio.run(run_dataset(dataset_path, tmp_root))
    finally:
        dataset_path.unlink(missing_ok=True)

    findings = []
    for index, item in enumerate(report.get("findings") or [], 1):
        row = dict(item)
        row["id"] = row.get("finding_id") or index
        row.setdefault("candidate_profile", {})
        findings.append(row)
    return {
        "status": "OK", "findings": findings,
        "processed_events": report.get("processed_events"),
        "run_id": report.get("run_id"),
        "benchmark": report.get("benchmark"),
    }


def finding_from_row(row: dict) -> Finding:
    """Rebuild a real `Finding` from a live-replay result row (or any stored
    finding dict with the same fields) so store methods that require Finding
    attribute access -- e.g. `Store.paper_edge_validation` -- can be reused
    unmodified against a detection that never touched the live dispatcher."""
    payload = {key: value for key, value in row.items() if key in _FINDING_FIELD_NAMES}
    payload.setdefault("evidence", [])
    return Finding(**payload)

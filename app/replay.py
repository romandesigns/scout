from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import tracemalloc
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import settings
from .db import Store
from .market import MarketWatcher, trading_session_key
from .models import Finding, SymbolState

SCHEMA_VERSION = "scout.market-event.v1"
REPLAY_ENGINE_VERSION = "1.0.0"
SUPPORTED_TYPES = {"trade"}


@dataclass(frozen=True)
class MarketEvent:
    schema: str
    event_type: str
    symbol: str
    source_ts: float
    received_ts: float
    sequence: int
    feed: str
    payload: dict[str, Any]

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "MarketEvent":
        event = cls(
            schema=str(value.get("schema", "")),
            event_type=str(value.get("event_type", "")).lower(),
            symbol=str(value.get("symbol", "")).upper(),
            source_ts=float(value.get("source_ts", 0)),
            received_ts=float(value.get("received_ts", value.get("source_ts", 0))),
            sequence=int(value.get("sequence", 0)),
            feed=str(value.get("feed", "unknown")).lower(),
            payload=dict(value.get("payload") or {}),
        )
        event.validate()
        return event

    def validate(self) -> None:
        if self.schema != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema: {self.schema!r}")
        if self.event_type not in SUPPORTED_TYPES:
            raise ValueError(f"unsupported event_type: {self.event_type!r}")
        if not self.symbol or self.source_ts <= 0 or self.sequence < 0:
            raise ValueError("symbol, positive source_ts, and non-negative sequence are required")
        if self.event_type == "trade":
            if float(self.payload.get("price", 0)) <= 0 or float(self.payload.get("size", 0)) < 0:
                raise ValueError("trade requires positive price and non-negative size")


class ReplayClock:
    """Clock driven exclusively by source events; never by wall time."""

    def __init__(self) -> None:
        self._now = 0.0

    def advance(self, source_ts: float) -> float:
        if source_ts < self._now:
            raise ValueError("replay clock cannot move backward")
        self._now = source_ts
        return self._now

    def now(self) -> float:
        return self._now


class ReplayDispatcher:
    """Hard isolation boundary: capture findings without production side effects."""

    delivery_enabled = False

    def __init__(self) -> None:
        self.items: list[Finding] = []

    async def emit(self, finding: Finding, buckets=None, current=None) -> int:
        self.items.append(finding)
        finding.finding_id = len(self.items)
        return finding.finding_id


def _finding_dict(finding: Finding) -> dict[str, Any]:
    value = asdict(finding)
    value["mode"] = "SIMULATION"
    return value


def load_events(path: Path) -> tuple[list[MarketEvent], dict[str, int]]:
    events: list[MarketEvent] = []
    malformed = 0
    duplicates = 0
    out_of_order = 0
    seen: set[tuple[str, int]] = set()
    last_ts = 0.0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = MarketEvent.parse(json.loads(line))
            except Exception as exc:
                malformed += 1
                raise ValueError(f"invalid event at line {line_number}: {exc}") from exc
            key = (event.feed, event.sequence)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            if event.source_ts < last_ts:
                out_of_order += 1
            last_ts = max(last_ts, event.source_ts)
            events.append(event)
    events.sort(key=lambda event: (event.source_ts, event.sequence))
    return events, {"malformed": malformed, "duplicates": duplicates, "out_of_order": out_of_order}


async def run_dataset(dataset: Path, output_root: Path | None = None) -> dict[str, Any]:
    dataset = dataset.resolve()
    output_root = (output_root or settings.data_dir / "replays").resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = f"replay-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    events, integrity = load_events(dataset)
    if not events:
        raise ValueError("dataset contains no supported events")

    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    replay_store = Store(run_dir / "state.db")
    capture = ReplayDispatcher()
    market = MarketWatcher(replay_store, capture)
    clock = ReplayClock()
    processed = 0
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    tracemalloc.start()

    for event in events:
        ts = clock.advance(event.source_ts)
        if event.event_type != "trade":
            continue
        state = market.states.get(event.symbol)
        if state is None:
            state = market.states[event.symbol] = SymbolState(event.symbol, settings.bucket_seconds, settings.keep_buckets)
        price = float(event.payload["price"])
        size = float(event.payload["size"])
        state.update_trade(ts, price, size, trading_session_key(ts))
        market._update_outcomes(event.symbol, ts, price)
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
        processed += 1

    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    wall_seconds = max(0.000001, time.perf_counter() - started_wall)
    cpu_seconds = max(0.0, time.process_time() - started_cpu)
    findings = [_finding_dict(item) for item in capture.items]
    report = {
        "run_id": run_id,
        "mode": "SIMULATION",
        "status": "completed",
        "schema_version": SCHEMA_VERSION,
        "replay_engine_version": REPLAY_ENGINE_VERSION,
        "scout_version": settings.app_version,
        "dataset": dataset.name,
        "dataset_sha256": digest,
        "started_source_ts": events[0].source_ts,
        "ended_source_ts": events[-1].source_ts,
        "processed_events": processed,
        "findings_count": len(findings),
        "integrity": integrity,
        "benchmark": {
            "wall_seconds": wall_seconds,
            "cpu_seconds": cpu_seconds,
            "events_per_second": processed / wall_seconds,
            "peak_memory_bytes": peak_bytes,
            "current_memory_bytes": current_bytes,
        },
        "notifications": {"enabled": False, "attempted": 0},
        "findings": findings,
        "completed_at": int(time.time()),
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    latest = {key: value for key, value in report.items() if key != "findings"}
    latest["report_path"] = str(report_path)
    (output_root / "latest.json").write_text(json.dumps(latest, indent=2), encoding="utf-8")
    return report


def replay_status(output_root: Path | None = None) -> dict[str, Any]:
    root = output_root or settings.data_dir / "replays"
    latest = root / "latest.json"
    if not latest.is_file():
        return {"mode": "LIVE", "active": False, "latest_run": None}
    try:
        return {"mode": "LIVE", "active": False, "latest_run": json.loads(latest.read_text(encoding="utf-8"))}
    except Exception:
        return {"mode": "LIVE", "active": False, "latest_run": None, "error": "latest replay metadata is unreadable"}


def write_events(path: Path, events: Iterable[MarketEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(json.dumps(asdict(event), separators=(",", ":")) + "\n")

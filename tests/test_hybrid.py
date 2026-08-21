from __future__ import annotations

from pathlib import Path

from app.db import Store
from app.hybrid import HybridMemory
from app.models import Finding
from app.notifiers import notification_allowed
from app.preferences import normalize_notification_preferences


def _finding(**overrides) -> Finding:
    values = dict(
        ticker="TEST",
        stage="AWAKENING",
        detected_at=1_700_000_000.0,
        price=3.0,
        score=8,
        vol_ratio_15s=3.2,
        vol_ratio_30s=2.4,
        change_60s_pct=0.8,
        extension_pct=0.2,
        ema9=3.0,
        ema21=2.98,
        ema9_slope=0.01,
        vwap=2.97,
        above_vwap=True,
        quiet_break=True,
        evidence=["early transition"],
        quality_label="CLEAN",
        quality_score=82,
        engine_source="rust",
        hybrid_sources=["rust"],
        hybrid_score=82,
        hybrid_key="TEST:2026-08-17:0",
    )
    values.update(overrides)
    return Finding(**values)


def test_hybrid_memory_correlates_engines_without_redefining_detection():
    memory = HybridMemory(merge_window_seconds=45, dedupe_seconds=20)
    assert memory.observe("TEST", "rust", 100.0, "AWAKENING") == ["rust"]
    assert memory.observe("TEST", "python", 118.0, "FIRST_LEG") == ["python", "rust"]
    other = memory.recent_other("TEST", "python", 118.0)
    assert other is not None and other.source == "rust"


def test_recent_python_alert_suppresses_redundant_rust_notification():
    memory = HybridMemory(merge_window_seconds=45, dedupe_seconds=20)
    memory.observe("TEST", "python", 100.0, "EARLY")
    assert memory.rust_notification_is_duplicate("TEST", 115.0)
    assert not memory.rust_notification_is_duplicate("TEST", 125.0)


def test_shadow_rust_candidate_never_notifies():
    prefs = normalize_notification_preferences(None)
    # AWAKENING remains available in the dashboard but is no longer a user-facing
    # decision notification; only EARLY setup and one confirmation are pushed.
    assert not notification_allowed(_finding(shadow_mode=False), prefs, "android")
    assert not notification_allowed(_finding(shadow_mode=True), prefs, "android")


def test_hybrid_provenance_round_trips_through_store(tmp_path: Path):
    store = Store(tmp_path / "state.db")
    finding = _finding(hybrid_sources=["rust", "python"], hybrid_score=97, notification_reason="dual-engine confirmation")
    finding_id = store.save_finding(finding)
    row = store.get_finding(finding_id)
    assert row is not None
    assert row["engine_source"] == "rust"
    assert row["hybrid_sources"] == ["rust", "python"]
    assert row["hybrid_score"] == 97
    assert row["hybrid_key"] == "TEST:2026-08-17:0"
    assert row["notification_reason"] == "dual-engine confirmation"
    store.close()

def test_hybrid_episode_key_rolls_after_gap():
    memory = HybridMemory(merge_window_seconds=45, dedupe_seconds=20, episode_gap_seconds=900)
    first = memory.episode_key("TEST", "2026-08-17", 100.0)
    same = memory.episode_key("TEST", "2026-08-17", 500.0)
    later = memory.episode_key("TEST", "2026-08-17", 1501.0)
    next_session = memory.episode_key("TEST", "2026-08-18", 1600.0)
    assert first == same == "TEST:2026-08-17:0"
    assert later == "TEST:2026-08-17:1"
    assert next_session == "TEST:2026-08-18:0"

def test_hybrid_precision_uses_first_detection_in_episode(tmp_path: Path):
    store = Store(tmp_path / "state.db")
    first = _finding(detected_at=1_700_000_000.0, hybrid_key="TEST:2026-08-17:0", engine_source="rust")
    later = _finding(detected_at=1_700_000_100.0, hybrid_key="TEST:2026-08-17:0", engine_source="python")
    first_id = store.save_finding(first)
    later_id = store.save_finding(later)
    store.upsert_outcome(first_id, None, None, 2.0, 2.0, None)
    store.upsert_outcome(later_id, None, None, 12.0, 12.0, None)
    stats = store.hybrid_precision_stats(5.0)
    assert stats["completed_episodes"] == 1
    assert stats["successful_episodes"] == 0
    assert stats["source_mix"]["both"] == 1
    store.close()

def test_rust_bridge_jsonl_transport_with_fake_binary(tmp_path: Path):
    import asyncio
    import sys
    from app.hybrid import RustPerceptionBridge

    fake = tmp_path / "fake-rust.py"
    fake.write_text(
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "  event=json.loads(line)\n"
        "  out={'ticker':event['symbol'],'detected_at':event['source_ts'],'price':event['payload']['price'],'stage':'PRE_IGNITION','lifecycle_phase':'ARMED','shadow_mode':True,'recipe_score':8,'recipe_present':['test'],'recipe_missing':[],'trigger_distance_pct':0.0,'base_extension_pct':0.1}\n"
        "  print(json.dumps(out), flush=True)\n"
    )
    received = []

    async def run_test():
        async def handler(payload):
            received.append(payload)

        bridge = RustPerceptionBridge(handler)
        bridge.binary = Path(sys.executable)
        bridge.process_args = [str(fake)]
        bridge.enabled = True
        await bridge.start()
        try:
            bridge.submit_trade(symbol="TEST", ts=1000.0, price=3.0, size=100.0, feed="sip")
            for _ in range(100):
                if received:
                    break
                await asyncio.sleep(0.01)
            assert received and received[0]["ticker"] == "TEST"
            status = bridge.status()
            assert status["submitted"] == 1
            assert status["candidates"] == 1
            assert status["dropped"] == 0
        finally:
            await bridge.stop()

    asyncio.run(run_test())

def test_market_rust_candidate_becomes_actionable_awakening(tmp_path: Path):
    import asyncio
    from app.market import MarketWatcher
    from app.models import SymbolState

    class CaptureDispatcher:
        def __init__(self):
            self.items = []
        async def emit(self, finding, buckets=None, current=None):
            self.items.append(finding)
            return len(self.items)

    store = Store(tmp_path / "state.db")
    dispatcher = CaptureDispatcher()
    market = MarketWatcher(store, dispatcher)  # type: ignore[arg-type]
    state = SymbolState("TEST", 15, 160)
    market.states["TEST"] = state
    metrics = {
        "full_warmup": True, "quality_label": "CLEAN", "quality_score": 86, "price": 3.05,
        "score": 7, "vol15": 3.0, "vol30": 2.2, "change5": 0.12, "change15": 0.2,
        "change3": 0.05, "change10": 0.16, "change30": 0.31, "change60": 0.5,
        "extension": 0.25, "ema9": 3.02, "ema21": 3.0, "ema9_slope": 0.01,
        "vwap": 3.0, "above_vwap": True, "quiet_break": True, "evidence": ["quality clean"],
        "accel15_pp": 0.1, "dollar15": 12000.0, "dollar30": 18000.0, "trades15": 18,
        "trades30": 30, "breakout_level": 3.06, "breakout_window": "micro",
        "rejection_reasons": [], "directional_efficiency": 0.8, "active_bucket_ratio": 1.0,
        "direction_reversals": 0, "previous_close": 2.9, "gap_pct": 5.0, "day_volume": 500000.0,
        "projected_session_volume": 1000000.0, "volume_rate_per_minute": 20000.0,
        "candidate_profile": {"velocity": 70, "participation": 75, "structure": 80, "quality": 86},
        "base_low": 2.98, "base_high": 3.05, "micro_resistance": 3.06,
    }
    market._metrics = lambda _state, _ts: metrics  # type: ignore[method-assign]
    candidate = {
        "ticker": "TEST", "detected_at": 1_700_000_000.0, "price": 3.05,
        "recipe_score": 8, "recipe_present": ["compressed or orderly base", "relative volume is waking up"],
        "recipe_missing": [], "trigger_distance_pct": 0.1, "base_extension_pct": 0.2,
    }
    asyncio.run(market.handle_rust_candidate(candidate))
    assert len(dispatcher.items) == 1
    finding = dispatcher.items[0]
    assert finding.stage == "AWAKENING"
    assert finding.engine_source == "rust"
    assert finding.shadow_mode is False
    assert finding.lifecycle_phase == "AWAKENING"
    assert finding.hybrid_key
    store.close()

def test_rust_bridge_microbatches_preserve_burst_without_drops(tmp_path: Path):
    import asyncio
    import sys
    from app.hybrid import RustPerceptionBridge

    fake = tmp_path / "sink-rust.py"
    fake.write_text(
        "import sys\n"
        "for line in sys.stdin:\n"
        "  pass\n"
    )

    async def run_test():
        async def handler(payload):
            return None

        bridge = RustPerceptionBridge(handler)
        bridge.binary = Path(sys.executable)
        bridge.process_args = [str(fake)]
        bridge.enabled = True
        await bridge.start()
        try:
            total = 5000
            for i in range(total):
                assert bridge.submit_trade(symbol="TEST", ts=1000.0 + i / 1000, price=3.0, size=1.0, feed="sip")
            for _ in range(500):
                if bridge.written >= total:
                    break
                await asyncio.sleep(0.01)
            status = bridge.status()
            assert status["written"] == total
            assert status["dropped"] == 0
            assert status["queue_depth"] == 0
            assert status["writer_batches"] < total
            assert status["writer_avg_batch"] > 1
            assert status["backpressure"] == "healthy"
        finally:
            await bridge.stop()

    asyncio.run(run_test())


def test_rust_bridge_rate_limits_quotes_per_symbol():
    from app.hybrid import RustPerceptionBridge

    async def handler(payload):
        return None

    bridge = RustPerceptionBridge(handler)
    bridge.enabled = True
    assert bridge.submit_quote(
        symbol="wake", ts=1000.0, bid_price=1.0, ask_price=1.01,
        bid_size=800, ask_size=200, feed="sip",
    )
    assert not bridge.submit_quote(
        symbol="wake", ts=1000.2, bid_price=1.0, ask_price=1.01,
        bid_size=900, ask_size=200, feed="sip",
    )
    assert bridge.submit_quote(
        symbol="wake", ts=1001.0, bid_price=1.01, ask_price=1.02,
        bid_size=900, ask_size=200, feed="sip",
    )
    assert bridge.queue.qsize() == 2


def test_rust_bridge_rejects_one_sided_and_crossed_quotes_before_enqueue():
    from app.hybrid import RustPerceptionBridge

    async def handler(payload):
        return None

    bridge = RustPerceptionBridge(handler)
    bridge.enabled = True
    assert not bridge.submit_quote(
        symbol="wake", ts=1000.0, bid_price=0.0, ask_price=1.01,
        bid_size=0, ask_size=200, feed="sip",
    )
    assert not bridge.submit_quote(
        symbol="wake", ts=1001.0, bid_price=1.02, ask_price=1.01,
        bid_size=800, ask_size=200, feed="sip",
    )
    assert bridge.queue.qsize() == 0


def test_shaping_up_transition_becomes_evidence_rich_early_watch(tmp_path: Path):
    import asyncio
    from app.market import MarketWatcher
    from app.models import SymbolState

    class CaptureDispatcher:
        def __init__(self): self.items = []
        async def emit(self, finding, buckets=None, current=None):
            self.items.append(finding); return len(self.items)

    store = Store(tmp_path / "state.db")
    dispatcher = CaptureDispatcher()
    market = MarketWatcher(store, dispatcher)  # type: ignore[arg-type]
    market.states["WAKE"] = SymbolState("WAKE", 15, 160)
    metrics = {
        "full_warmup": True, "quality_label": "ILLIQUID", "quality_score": 58,
        "price": .48, "score": 6, "vol15": 1.1, "vol30": 1.0,
        "change5": .06, "change15": .08, "change3": .02, "change10": .07,
        "change30": .10, "change60": .12, "extension": .4,
        "ema9": .48, "ema21": .479, "ema9_slope": .001, "vwap": .475,
        "above_vwap": True, "quiet_break": False, "accel15_pp": .02,
        "dollar15": 900, "dollar30": 1400, "trades15": 4, "trades30": 7,
        "breakout_level": .50, "breakout_window": "micro", "rejection_reasons": ["LOW PARTICIPATION"],
        "directional_efficiency": .7, "active_bucket_ratio": .75, "direction_reversals": 1,
        "previous_close": .46, "gap_pct": 4.3, "day_volume": 10000,
        "projected_session_volume": 100000, "volume_rate_per_minute": 1200,
        "candidate_profile": {}, "base_low": .46, "base_high": .49, "micro_resistance": .50,
    }
    market._metrics = lambda _state, _ts: metrics  # type: ignore[method-assign]
    candidate = {
        "ticker": "WAKE", "detected_at": 1_700_000_000.0, "stage": "SHAPING_UP",
        "recipe_score": 8, "confidence": 82, "trade_acceleration": 6.0,
        "dollar_acceleration": 9.6, "bid_ask_imbalance": 4.0, "spread_pct": 2.0,
        "trigger_level": .50, "invalidation_level": .46,
        "recipe_present": ["trade frequency is accelerating", "bid pressure supports the move"],
        "recipe_missing": [], "trigger_distance_pct": 4.17, "base_extension_pct": .4,
    }
    asyncio.run(market.handle_rust_candidate(candidate))
    finding = dispatcher.items[0]
    assert finding.stage == "AWAKENING"
    assert finding.actionable_rank == "B"
    assert finding.shadow_mode is False
    assert finding.trigger_level == .50 and finding.invalidation_level == .46
    assert any("6.0x its dormant baseline" in item for item in finding.evidence)
    assert finding.candidate_profile["transition_confidence"] == 82
    store.close()

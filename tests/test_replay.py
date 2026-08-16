from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.replay import MarketEvent, ReplayClock, ReplayDispatcher, calibrate_pre_ignition, load_events, run_dataset


class ReplaySpineTests(unittest.TestCase):
    def test_clock_rejects_time_travel(self):
        clock = ReplayClock()
        clock.advance(10)
        with self.assertRaises(ValueError):
            clock.advance(9)

    def test_event_validation(self):
        with self.assertRaises(ValueError):
            MarketEvent.parse({"schema": "wrong", "event_type": "trade", "symbol": "X", "source_ts": 1, "sequence": 1, "payload": {"price": 1, "size": 1}})

    def test_fixture_is_ordered(self):
        path = Path(__file__).parent / "fixtures" / "replay-smoke.ndjson"
        events, integrity = load_events(path)
        self.assertEqual(10, len(events))
        self.assertEqual(0, integrity["out_of_order"])

    def test_replay_is_notification_isolated_and_deterministic(self):
        path = Path(__file__).parent / "fixtures" / "replay-smoke.ndjson"
        with tempfile.TemporaryDirectory() as root:
            first = asyncio.run(run_dataset(path, Path(root)))
        with tempfile.TemporaryDirectory() as root:
            second = asyncio.run(run_dataset(path, Path(root)))
        self.assertFalse(first["notifications"]["enabled"])
        self.assertEqual(0, first["notifications"]["attempted"])
        self.assertEqual(first["processed_events"], second["processed_events"])
        self.assertEqual(first["findings"], second["findings"])

    def test_shadow_calibration_measures_lead_time_without_changing_detection(self):
        events = [MarketEvent("scout.market-event.v1", "trade", "TEST", ts, ts, index, "sip", {"price": price, "size": 100}) for index, (ts, price) in enumerate([(1000, 10.0), (1030, 10.1), (1060, 10.21)], 1)]
        findings = [{"finding_id": 1, "ticker": "TEST", "stage": "PRE_IGNITION", "detected_at": 1000, "price": 10.0, "recipe_score": 8, "recipe_present": ["compressed base"], "base_extension_at_detection_pct": 0.2}]
        report = calibrate_pre_ignition(events, findings)
        self.assertEqual(1, report["successful_precursors"])
        self.assertEqual(60, report["median_lead_seconds"])
        self.assertEqual(0, report["false_arms"])


if __name__ == "__main__":
    unittest.main()

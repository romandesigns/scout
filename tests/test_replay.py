from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.replay import MarketEvent, ReplayClock, ReplayDispatcher, load_events, run_dataset


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


if __name__ == "__main__":
    unittest.main()

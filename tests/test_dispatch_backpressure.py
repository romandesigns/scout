from __future__ import annotations

import asyncio
import dataclasses
import time
import unittest
from unittest.mock import patch

from app.config import settings
from app.dispatch import Dispatcher
from app.models import Finding


def finding(ticker: str, stage: str = "BREAKOUT") -> Finding:
    return Finding(
        ticker=ticker, stage=stage, detected_at=time.time(), price=2.0, score=9,
        vol_ratio_15s=8, vol_ratio_30s=6, change_60s_pct=2, extension_pct=1,
        ema9=2.0, ema21=1.99, ema9_slope=.01, vwap=1.98, above_vwap=True,
        quiet_break=True, evidence=["burst test"], quality_label="CLEAN",
        actionable_rank="A", candidate_profile={"edge_validation": {"validated": True}},
    )


class RecordingDispatcher(Dispatcher):
    def __init__(self):
        super().__init__(store=None)
        self.completed: list[str] = []

    async def emit(self, f, buckets=None, current=None):
        await asyncio.sleep(.01)
        self.completed.append(f.ticker)
        return len(self.completed)


class DispatchBackpressureTests(unittest.IsolatedAsyncioTestCase):
    async def test_burst_submission_does_not_wait_for_persistence(self):
        dispatcher = RecordingDispatcher()
        release_persistence = asyncio.Event()

        async def blocked_emit(f, buckets=None, current=None):
            await release_persistence.wait()
            dispatcher.completed.append(f.ticker)
            return len(dispatcher.completed)

        dispatcher.emit = blocked_emit
        futures = [dispatcher.submit(finding(f"T{i:03d}")) for i in range(100)]

        self.assertTrue(all(not future.done() for future in futures))
        self.assertGreater(dispatcher._dispatch_queue.qsize(), 0)
        release_persistence.set()
        await asyncio.gather(*futures)
        self.assertEqual(len(dispatcher.completed), 100)
        self.assertEqual(dispatcher._dispatch_queue.qsize(), 0)

    async def test_higher_priority_event_moves_ahead_of_pending_normal_work(self):
        dispatcher = RecordingDispatcher()
        normal = [dispatcher.submit(finding(f"N{i:03d}", "EARLY")) for i in range(20)]
        await asyncio.sleep(0)
        halt = dispatcher.submit(finding("URGENT", "HALT"))
        await halt
        self.assertIn("URGENT", dispatcher.completed[:8])
        await asyncio.gather(*normal)

    async def test_low_priority_load_cannot_consume_reserved_capacity(self):
        tuned = dataclasses.replace(
            settings, dispatch_queue_max=10, dispatch_worker_count=1,
            dispatch_low_priority_max_utilization=.5,
        )
        with patch("app.dispatch.settings", tuned):
            dispatcher = RecordingDispatcher()
            accepted = [dispatcher.submit(finding(f"W{i}", "PRE_IGNITION")) for i in range(5)]
            shed = dispatcher.submit(finding("SHED", "PRE_IGNITION"))
            urgent = dispatcher.submit(finding("KEEP", "HALT"))
            with self.assertRaisesRegex(RuntimeError, "backpressure shed"):
                await shed
            await urgent
            self.assertEqual(dispatcher._dispatch_shed_low_priority, 1)
            await asyncio.gather(*accepted)


if __name__ == "__main__":
    unittest.main()

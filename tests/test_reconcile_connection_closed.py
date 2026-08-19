"""
Regression test for the 2026-08-19 log-review fix: MarketWatcher._reconcile() must swallow
websockets.exceptions.ConnectionClosed (the shared SIP/BOATS websocket dying mid-send, e.g.
during a keepalive ping timeout) instead of letting it propagate to universe_loop's
`except Exception: log.exception("Universe refresh failed")` -- previously this logged a
full ERROR-level stack trace on every occurrence even though the universe refresh itself had
already succeeded and the connection self-heals via _stream()'s own reconnect loop. A genuine,
unrelated exception during reconcile must still propagate (that's a real bug, not noise).
"""
import asyncio
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="scout-test-data-"))
os.environ.setdefault("CHART_DIR", tempfile.mkdtemp(prefix="scout-test-charts-"))
os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp(prefix="scout-test-mpl-"))

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.RequestException = RuntimeError
    requests_stub.Response = object
    sys.modules["requests"] = requests_stub
try:
    import orjson  # noqa: F401
except ModuleNotFoundError:
    orjson_stub = types.ModuleType("orjson")
    orjson_stub.loads = json.loads
    sys.modules["orjson"] = orjson_stub

import websockets.exceptions

from app.db import Store
from app.dispatch import Dispatcher
from app.market import MarketWatcher


class _FakeClosingWS:
    """Mimics a shared websocket that dies mid-send with the same exception class the real
    `websockets` library raises on a keepalive ping timeout."""
    async def send(self, payload):
        raise websockets.exceptions.ConnectionClosed(None, None)


class _FakeBrokenWS:
    """A websocket whose send() fails for some unrelated, genuine reason."""
    async def send(self, payload):
        raise RuntimeError("boom: unrelated bug")


class ReconcileConnectionClosedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "scout.db")
        self.market = MarketWatcher(self.store, Dispatcher(self.store))
        self.market._desired = {"AAPL", "TSLA"}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_connection_closed_during_send_is_swallowed(self):
        async def run():
            # Should not raise -- this is the actual regression being fixed.
            await self.market._reconcile(_FakeClosingWS(), set(), "SIP")
        asyncio.run(run())
        status = self.market.reconcile_status["sip"]
        self.assertIn("closed mid-reconcile", status["last_error"])
        self.assertFalse(status["in_progress"])

    def test_unrelated_exception_during_send_still_propagates(self):
        async def run():
            await self.market._reconcile(_FakeBrokenWS(), set(), "SIP")
        with self.assertRaises(RuntimeError):
            asyncio.run(run())
        status = self.market.reconcile_status["sip"]
        self.assertEqual(status["last_error"], "boom: unrelated bug")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
import os
import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

_runtime = tempfile.mkdtemp(prefix="scout-historical-test-")
os.environ.setdefault("DATA_DIR", _runtime)
os.environ.setdefault("CHART_DIR", _runtime)
os.environ.setdefault("MPLCONFIGDIR", _runtime)

try:
    import requests  # noqa: F401
except ModuleNotFoundError:
    requests_stub = types.ModuleType("requests")
    requests_stub.RequestException = RuntimeError
    requests_stub.Response = object
    requests_stub.get = lambda *args, **kwargs: None
    requests_stub.post = lambda *args, **kwargs: None
    sys.modules["requests"] = requests_stub
try:
    import orjson  # noqa: F401
except ModuleNotFoundError:
    orjson_stub = types.ModuleType("orjson")
    orjson_stub.loads = json.loads
    sys.modules["orjson"] = orjson_stub
try:
    import websockets  # noqa: F401
except ModuleNotFoundError:
    sys.modules["websockets"] = types.ModuleType("websockets")

from app.db import Store
from app.dispatch import Dispatcher
from app.market import MarketWatcher


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class HistoricalChartTests(unittest.TestCase):
    def test_historical_trades_are_aggregated_into_native_candles(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "scout.db")
            watcher = MarketWatcher(store, Dispatcher(store))
            trades = {
                "trades": [
                    {"t": "2026-08-14T14:30:01Z", "p": 2.00, "s": 100},
                    {"t": "2026-08-14T14:30:07Z", "p": 2.08, "s": 80},
                    {"t": "2026-08-14T14:30:16Z", "p": 2.05, "s": 60},
                ]
            }
            with patch("app.market.requests.get", return_value=_Response(trades)):
                payload = watcher.historical_snapshot_sync("TEST", 1786717808, 15)
        self.assertEqual("historical-trades", payload["source"])
        self.assertEqual(2, len(payload["buckets"]))
        self.assertEqual(2.08, payload["buckets"][0]["high"])
        self.assertEqual(180, payload["buckets"][0]["volume"])
        self.assertEqual(2, payload["buckets"][0]["trades"])

    def test_historical_trade_pages_are_exhausted_before_aggregation(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / "scout.db")
            watcher = MarketWatcher(store, Dispatcher(store))
            pages = [
                _Response({"trades": [{"t": "2026-08-14T14:30:01Z", "p": 2.00, "s": 100}], "next_page_token": "page-2"}),
                _Response({"trades": [{"t": "2026-08-14T14:30:16Z", "p": 2.05, "s": 60}]}),
            ]
            with patch("app.market.requests.get", side_effect=pages) as request:
                payload = watcher.historical_snapshot_sync("TEST", 1786717808, 15)
        self.assertEqual(2, request.call_count)
        self.assertEqual(2, payload["historical_pages"])
        self.assertEqual(2, payload["historical_trade_count"])
        self.assertTrue(payload["historical_complete"])


if __name__ == "__main__":
    unittest.main()

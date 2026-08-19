"""/api/notifications/ntfy-config -- lets any client discover this deployment's own ntfy
server/topic in-app, instead of the operator needing to already know their own .env value
to subscribe a new device. Real gap found investigating notification cross-platform sync
(2026-08-19 follow-up)."""
from __future__ import annotations

import asyncio
import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.api import ScoutApi
from app.config import settings
from app.db import Store
from app.dispatch import Dispatcher
from app.events import EventHub
from app.market import MarketWatcher


class NtfyConfigEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "scout.db")
        self.market = MarketWatcher(self.store, Dispatcher(self.store))
        self.api = ScoutApi(self.store, self.market, EventHub())

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_configured_exposes_server_and_topic(self):
        configured_settings = dataclasses.replace(settings, ntfy_server="https://ntfy.example.internal", ntfy_topic="scout-alerts-abc123")
        with patch("app.api.settings", configured_settings):
            response = asyncio.run(self.api.ntfy_config(None))
        body = response.body if hasattr(response, "body") else None
        import json
        payload = json.loads(response.text)
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["server"], "https://ntfy.example.internal")
        self.assertEqual(payload["topic"], "scout-alerts-abc123")
        self.assertEqual(payload["subscribe_url"], "https://ntfy.example.internal/scout-alerts-abc123")

    def test_unconfigured_does_not_leak_placeholder_topic(self):
        unconfigured_settings = dataclasses.replace(settings, ntfy_server="https://ntfy.sh", ntfy_topic="")
        with patch("app.api.settings", unconfigured_settings):
            response = asyncio.run(self.api.ntfy_config(None))
        import json
        payload = json.loads(response.text)
        self.assertFalse(payload["configured"])
        self.assertIsNone(payload["topic"])
        self.assertIsNone(payload["subscribe_url"])


if __name__ == "__main__":
    unittest.main()

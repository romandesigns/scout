import os
import json
import sys
import tempfile
import types
import unittest
import time
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="scout-test-data-"))
os.environ.setdefault("CHART_DIR", tempfile.mkdtemp(prefix="scout-test-charts-"))
os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp(prefix="scout-test-mpl-"))

# Keep this pure metric test runnable in minimal source-validation environments;
# production installs the pinned network dependencies from requirements.txt.
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
try:
    import websockets  # noqa: F401
except ModuleNotFoundError:
    sys.modules["websockets"] = types.ModuleType("websockets")

from app.db import Store
from app.dispatch import Dispatcher
from app.market import MarketWatcher
from app.models import Bucket, SymbolState
from app.models import Finding
from app.notifiers import _allowed
from app.preferences import DEFAULT_NOTIFICATION_PREFERENCES


class MarketQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "scout.db")
        self.stores = [self.store]
        self.market = MarketWatcher(self.store, Dispatcher(self.store))

    def tearDown(self):
        for store in reversed(self.stores):
            store.close()
        self.tmp.cleanup()

    def state(self, closes, *, current_volume=4000, current_trades=20):
        state = SymbolState("TEST", 15, 160)
        start = 1_800_000_000
        for index, close in enumerate(closes[:-1]):
            previous = closes[index - 1] if index else close
            state.buckets.append(Bucket(start + index * 15, previous, max(previous, close) * 1.0005, min(previous, close) * .9995, close, 250, 5))
            state.price_points.append((start + index * 15 + 14, close))
        current = closes[-1]
        state.current = Bucket(start + (len(closes) - 1) * 15, closes[-2], current * 1.0005, min(closes[-2], current) * .9995, current, current_volume, current_trades)
        now = state.current.start_ts + 14
        state.price_points.append((now, current))
        state.session_pv = sum(b.close * b.volume for b in state.buckets) + current * current_volume
        state.session_volume = sum(b.volume for b in state.buckets) + current_volume
        return state, now

    def test_orderly_bullish_participation_is_clean(self):
        state, now = self.state([2.00, 2.01, 2.02, 2.03, 2.04, 2.05, 2.07, 2.10, 2.14])
        metrics = self.market._metrics(state, now)
        self.assertEqual(metrics["quality_label"], "CLEAN")
        self.assertTrue(metrics["bullish_confirmed"])
        self.assertGreaterEqual(metrics["directional_efficiency"], .8)

    def test_alternating_path_is_choppy(self):
        state, now = self.state([2.00, 2.08, 1.98, 2.09, 1.97, 2.10, 1.96, 2.11, 2.01])
        metrics = self.market._metrics(state, now)
        self.assertEqual(metrics["quality_label"], "CHOPPY")
        self.assertIn("CHOPPY PATH", metrics["rejection_reasons"])

    def test_non_clean_price_signal_cannot_notify(self):
        finding = Finding(
            ticker="TEST", stage="EARLY", detected_at=1_800_000_000, price=2, score=10,
            vol_ratio_15s=20, vol_ratio_30s=12, change_60s_pct=3, extension_pct=2,
            ema9=2, ema21=1.99, ema9_slope=.01, vwap=1.98, above_vwap=True,
            quiet_break=True, evidence=["raw anomaly"], quality_label="CHOPPY", quality_score=45,
        )
        self.assertFalse(_allowed(finding, DEFAULT_NOTIFICATION_PREFERENCES, "android"))

    def test_clean_price_signal_can_notify(self):
        finding = Finding(
            ticker="TEST", stage="EARLY", detected_at=1_800_000_000, price=2, score=8,
            vol_ratio_15s=8, vol_ratio_30s=6, change_60s_pct=2, extension_pct=1,
            ema9=2, ema21=1.99, ema9_slope=.01, vwap=1.98, above_vwap=True,
            quiet_break=True, evidence=["orderly participation"], quality_label="CLEAN", quality_score=82,
            actionable_rank="A",
        )
        self.assertTrue(_allowed(finding, DEFAULT_NOTIFICATION_PREFERENCES, "android"))

    def test_reclaim_and_reversal_watch_remain_dashboard_only(self):
        base = dict(
            ticker="TEST", detected_at=1_800_000_000, price=2.62, score=9,
            vol_ratio_15s=6, vol_ratio_30s=5, change_60s_pct=3, extension_pct=2,
            ema9=2.60, ema21=2.58, ema9_slope=.01, vwap=2.59,
            above_vwap=True, quiet_break=False, evidence=["structural reclaim"],
            quality_label="CLEAN", quality_score=84,
        )
        self.assertFalse(_allowed(Finding(stage="RECLAIM", **base), DEFAULT_NOTIFICATION_PREFERENCES, "android"))
        self.assertFalse(_allowed(Finding(stage="REVERSAL_WATCH", **base), DEFAULT_NOTIFICATION_PREFERENCES, "android"))

    def test_validation_keeps_immature_horizons_pending_and_floors_max(self):
        store = Store(Path(self.tmp.name) / "validation.db")
        self.stores.append(store)
        finding = Finding(
            ticker="TEST", stage="BREAKOUT", detected_at=time.time(), price=2, score=8,
            vol_ratio_15s=8, vol_ratio_30s=6, change_60s_pct=2, extension_pct=1,
            ema9=2, ema21=1.99, ema9_slope=.01, vwap=1.98, above_vwap=True,
            quiet_break=True, evidence=["test"], quality_label="CLEAN", quality_score=82,
        )
        finding_id = store.save_finding(finding)
        store.upsert_outcome(finding_id, -.2, -.3, -.4, -.5, 1)
        row = store.list_validation(1)[0]
        self.assertIsNone(row["max_1m_pct"])
        self.assertIsNone(row["max_5m_pct"])
        self.assertIsNone(row["max_15m_pct"])
        self.assertEqual(row["max_session_pct"], 0.0)

    def test_scanner_range_is_persisted(self):
        store = Store(Path(self.tmp.name) / "range.db")
        self.stores.append(store)
        self.assertEqual(store.get_scanner_settings(), {"min_price": 0.15, "max_price": 10.0})
        store.set_scanner_settings(2.0, 8.0)
        self.assertEqual(store.get_scanner_settings(), {"min_price": 2.0, "max_price": 8.0})

    def test_midday_selloff_then_reclaim_has_fresh_reversal_context(self):
        decline = [3.00 - (0.50 * index / 79) for index in range(80)]
        base = [2.50, 2.50, 2.51, 2.50, 2.51, 2.52]
        reclaim = [2.53, 2.55, 2.57, 2.59]
        state, now = self.state(decline + base + reclaim, current_volume=12000, current_trades=40)
        metrics = self.market._metrics(state, now)
        self.assertGreaterEqual(metrics["reversal_drawdown_pct"], 15.0)
        self.assertGreaterEqual(metrics["reversal_bounce_pct"], 3.0)
        self.assertLessEqual(metrics["reversal_low_age_seconds"], 180)
        self.assertTrue(metrics["ema9_reclaimed"] or metrics["ema21_reclaimed"] or metrics["reclaim_structure"])

    def test_first_leg_detects_confirmed_release_near_compressed_base(self):
        closes = [2.00, 2.001, 2.002, 2.001, 2.003, 2.004, 2.005, 2.006, 2.008, 2.010, 2.012, 2.014, 2.022]
        state, now = self.state(closes, current_volume=9000, current_trades=30)
        state.price_points.extend([(now - 5, 2.015), (now - 3, 2.017), (now, 2.022)])
        metrics = self.market._metrics(state, now)
        self.assertTrue(metrics["first_leg_watch"])
        self.assertTrue(metrics["first_leg_release"])
        self.assertLessEqual(abs(metrics["base_extension_pct"]), 2.0)
        self.assertIn(metrics["leg_context"], {"BASE_RELEASE", "CONSOLIDATION_RELEASE"})

    def test_first_leg_states_remain_dashboard_only(self):
        base = dict(
            ticker="TEST", detected_at=1_800_000_000, price=2.02, score=8,
            vol_ratio_15s=6, vol_ratio_30s=4, change_60s_pct=.8, extension_pct=.9,
            ema9=2.01, ema21=2.00, ema9_slope=.01, vwap=2.00,
            above_vwap=True, quiet_break=False, evidence=["compression releasing"],
            quality_label="CLEAN", quality_score=90,
        )
        self.assertFalse(_allowed(Finding(stage="FIRST_LEG_WATCH", **base), DEFAULT_NOTIFICATION_PREFERENCES, "android"))
        self.assertFalse(_allowed(Finding(stage="PRE_IGNITION", shadow_mode=True, **base), DEFAULT_NOTIFICATION_PREFERENCES, "android"))
        self.assertFalse(_allowed(Finding(stage="FIRST_LEG", **base), DEFAULT_NOTIFICATION_PREFERENCES, "android"))

    def test_pre_ignition_recipe_round_trips_as_shadow_evidence(self):
        store = Store(Path(self.tmp.name) / "pre-ignition.db")
        self.stores.append(store)
        finding = Finding(
            ticker="TEST", stage="PRE_IGNITION", detected_at=1_800_000_000, price=2.01, score=7,
            vol_ratio_15s=2.2, vol_ratio_30s=1.8, change_60s_pct=.2, extension_pct=.35,
            ema9=2.00, ema21=1.99, ema9_slope=.01, vwap=1.99, above_vwap=True,
            quiet_break=False, evidence=["base pressure building"], quality_label="DEVELOPING",
            lifecycle_phase="ARMED", shadow_mode=True, recipe_score=8,
            recipe_present=["compressed or orderly base", "pressing a nearby trigger"],
            recipe_missing=["participation is broadening"], trigger_distance_pct=.18,
            base_extension_at_detection_pct=.35, timeliness_label="PRE_IGNITION",
        )
        finding_id = store.save_finding(finding)
        row = store.get_finding(finding_id)
        self.assertTrue(row["shadow_mode"])
        self.assertEqual("ARMED", row["lifecycle_phase"])
        self.assertEqual(8, row["recipe_score"])
        self.assertIn("pressing a nearby trigger", row["recipe_present"])
        self.assertEqual("PRE_IGNITION", row["timeliness_label"])

    def test_attention_inbox_groups_episode_and_preserves_user_status(self):
        store = Store(Path(self.tmp.name) / "attention.db")
        self.stores.append(store)
        base = dict(
            ticker="TEST", detected_at=1_800_000_000, price=2.02, score=9,
            vol_ratio_15s=7, vol_ratio_30s=5, change_60s_pct=1.2, extension_pct=1,
            ema9=2.01, ema21=2, ema9_slope=.01, vwap=2, above_vwap=True,
            quiet_break=True, evidence=["compression release"], quality_label="CLEAN",
            quality_score=90, actionable_rank="A", episode_id=7,
        )
        first_id = store.save_finding(Finding(stage="FIRST_LEG", **base))
        items = store.list_attention()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["first_finding_id"], first_id)
        store.update_attention(items[0]["id"], "watching")
        later = dict(base, detected_at=1_800_000_010, price=2.08)
        latest_id = store.save_finding(Finding(stage="BREAKOUT", **later))
        updated = store.list_attention()[0]
        self.assertEqual(updated["latest_finding_id"], latest_id)
        self.assertEqual(updated["status"], "watching")

    def test_verification_combines_detection_delivery_and_user_grade(self):
        store = Store(Path(self.tmp.name) / "verification.db")
        self.stores.append(store)
        finding = Finding(
            ticker="TEST", stage="EARLY", detected_at=1_800_000_000, price=2.00, score=9,
            vol_ratio_15s=8, vol_ratio_30s=6, change_60s_pct=2, extension_pct=1,
            ema9=2, ema21=1.99, ema9_slope=.01, vwap=1.98, above_vwap=True,
            quiet_break=True, evidence=["orderly release"], quality_label="CLEAN", quality_score=88,
            detection_timeframe_seconds=15, formation_start_at=1_799_999_940,
            formation_end_at=1_799_999_995, formation_low=1.94, formation_high=1.99,
            trigger_level=2.00, invalidation_level=1.94, engine_version="5.8.0",
        )
        finding_id = store.save_finding(finding)
        store.record_delivery(finding_id, "android", "provider_accepted", "ntfy")
        first = store.finding_verification(finding_id)
        self.assertEqual(first["delivery"][0]["status"], "provider_accepted")
        self.assertEqual(first["automatic_label"], "PROVISIONAL")
        self.assertIsNone(first["review"])
        store.save_finding_review(finding_id, 5, None, ["early"], "excellent early detection")
        reviewed = store.finding_verification(finding_id)
        self.assertEqual(reviewed["review"]["user_grade"], 5)
        self.assertEqual(reviewed["review"]["reason_tags"], ["early"])

    def test_clean_halt_pressure_alert_is_notification_eligible(self):
        finding = Finding(
            ticker="TEST", stage="HALT_PRESSURE", detected_at=1_800_000_000, price=2.00, score=10,
            vol_ratio_15s=12, vol_ratio_30s=9, change_60s_pct=4.5, extension_pct=1.2,
            ema9=2, ema21=1.96, ema9_slope=.02, vwap=1.94, above_vwap=True,
            quiet_break=True, evidence=["accelerating regular-session participation"],
            quality_label="CLEAN", quality_score=92, halt_pressure_score=87, urgency="NOW",
        )
        self.assertTrue(_allowed(finding, DEFAULT_NOTIFICATION_PREFERENCES, "android"))

    def test_web_push_subscription_lifecycle(self):
        store = Store(Path(self.tmp.name) / "push.db")
        self.stores.append(store)
        store.upsert_web_push_subscription("https://push.example/subscription", "public-key", "auth-secret", "Scout test")
        self.assertEqual(store.web_push_subscription_count(), 1)
        self.assertEqual(store.list_web_push_subscriptions()[0]["p256dh"], "public-key")
        self.assertTrue(store.delete_web_push_subscription("https://push.example/subscription"))
        self.assertEqual(store.web_push_subscription_count(), 0)

    def test_catalyst_without_market_reaction_is_watch_not_active(self):
        finding, _, _ = self.market.make_catalyst_finding("TEST", "Company wins material contract", "Contract", 5, "https://example.test/news", time.time())
        self.assertEqual(finding.stage, "CATALYST_WATCH")
        self.assertEqual(finding.urgency, "WATCH")
        self.assertTrue(_allowed(finding, DEFAULT_NOTIFICATION_PREFERENCES, "android"))

    def test_catalyst_with_clean_bullish_reaction_is_active(self):
        state, now = self.state([2.00, 2.01, 2.02, 2.03, 2.04, 2.06, 2.09, 2.14, 2.20], current_volume=18000, current_trades=55)
        self.market.states["TEST"] = state
        finding, _, _ = self.market.make_catalyst_finding("TEST", "Company wins material contract", "Contract", 5, "https://example.test/news", now)
        self.assertEqual(finding.stage, "CATALYST_ACTIVE")
        self.assertEqual(finding.urgency, "NOW")


if __name__ == "__main__":
    unittest.main()

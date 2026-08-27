"""Per-platform Web Push eligibility.

Regression coverage for a real bug found during the 2026-08-18 optimization session: every
notification call site hardcoded the "android" platform bucket, including Web Push -- which
can be subscribed to from ANY platform's browser (a desktop Chrome/Edge session is not
"android" just because that used to be the only platform this code checked). A Windows/desktop
subscriber's own platform preference was silently never consulted; only the shared "android"
toggle governed whether they received anything at all.
"""
from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
import unittest
from unittest.mock import patch
from app.config import settings


def with_vapid(**overrides):
    merged = {"vapid_public_key": "pub", "vapid_private_key": "priv", "vapid_subject": "mailto:test@example.com"}
    merged.update(overrides)
    return patch("app.notifiers.settings", dataclasses.replace(settings, **merged))

from app.models import Finding
from app.notifiers import (
    _message, _user_title, infer_platform, notification_allowed_any_platform,
    notification_ineligibility_reason, notification_phase, send_web_push_all,
)
from app.dispatch import Dispatcher
from app.preferences import DEFAULT_NOTIFICATION_PREFERENCES


def make_finding(**overrides) -> Finding:
    base = dict(
        ticker="TEST", stage="EARLY", detected_at=1_800_000_000, price=2.0, score=9,
        vol_ratio_15s=8, vol_ratio_30s=6, change_60s_pct=2, extension_pct=1,
        ema9=2.0, ema21=1.99, ema9_slope=.01, vwap=1.98, above_vwap=True,
        quiet_break=True, evidence=["orderly participation"], quality_label="CLEAN", quality_score=82,
        actionable_rank="A", candidate_profile={"edge_validation": {"validated": True}},
    )
    base.update(overrides)
    return Finding(**base)


class FakeStore:
    def __init__(self, subscriptions):
        self._subscriptions = subscriptions
        self.deleted: list[str] = []

    def list_web_push_subscriptions(self):
        return self._subscriptions

    def delete_web_push_subscription(self, endpoint):
        self.deleted.append(endpoint)
        return True


class InferPlatformTests(unittest.TestCase):
    def test_android_user_agent(self):
        self.assertEqual(infer_platform("Mozilla/5.0 (Linux; Android 14; Pixel 8)"), "android")

    def test_desktop_windows_user_agent(self):
        self.assertEqual(infer_platform("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/128"), "windows")

    def test_desktop_mac_user_agent_falls_back_to_windows_bucket(self):
        # Scout's preference schema only has two push-platform buckets (android, windows);
        # "windows" is really "not android" for this purpose, matching web/lib/native.ts's
        # own targetPlatform() convention so client and server never disagree.
        self.assertEqual(infer_platform("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)"), "windows")

    def test_empty_or_missing_user_agent(self):
        self.assertEqual(infer_platform(""), "windows")
        self.assertEqual(infer_platform(None), "windows")


class WebPushPerSubscriberPlatformTests(unittest.TestCase):
    def setUp(self):
        self.android_sub = {"endpoint": "https://push/android-1", "p256dh": "k", "auth": "a",
                             "user_agent": "Mozilla/5.0 (Linux; Android 14)"}
        self.windows_sub = {"endpoint": "https://push/windows-1", "p256dh": "k", "auth": "a",
                             "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def test_both_platforms_enabled_delivers_to_both(self):
        store = FakeStore([self.android_sub, self.windows_sub])
        finding = make_finding()
        prefs = copy.deepcopy(DEFAULT_NOTIFICATION_PREFERENCES)
        prefs["platforms"]["android"]["enabled"] = True
        prefs["platforms"]["windows"]["enabled"] = True
        with with_vapid(), patch("pywebpush.webpush") as mock_webpush:
            delivered = send_web_push_all(store, finding, prefs)
        self.assertEqual(delivered, 2)
        self.assertEqual(mock_webpush.call_count, 2)

    def test_windows_disabled_still_delivers_to_android_only(self):
        """The actual bug: before the fix, disabling ONLY the android toggle (or it being
        the sole thing ever checked) meant a desktop/windows subscriber's own preference
        was never consulted at all. This confirms per-subscriber filtering now works."""
        store = FakeStore([self.android_sub, self.windows_sub])
        finding = make_finding()
        prefs = copy.deepcopy(DEFAULT_NOTIFICATION_PREFERENCES)
        prefs["platforms"]["android"]["enabled"] = True
        prefs["platforms"]["windows"]["enabled"] = False
        with with_vapid(), patch("pywebpush.webpush") as mock_webpush:
            delivered = send_web_push_all(store, finding, prefs)
        self.assertEqual(delivered, 1)
        self.assertEqual(mock_webpush.call_count, 1)
        sent_to = mock_webpush.call_args.kwargs["subscription_info"]["endpoint"]
        self.assertEqual(sent_to, self.android_sub["endpoint"])

    def test_android_disabled_still_delivers_to_windows_only(self):
        """The symmetric case: a user who disables Android specifically must not also
        silently lose their desktop browser's Web Push -- these are independent toggles."""
        store = FakeStore([self.android_sub, self.windows_sub])
        finding = make_finding()
        prefs = copy.deepcopy(DEFAULT_NOTIFICATION_PREFERENCES)
        prefs["platforms"]["android"]["enabled"] = False
        prefs["platforms"]["windows"]["enabled"] = True
        with with_vapid(), patch("pywebpush.webpush") as mock_webpush:
            delivered = send_web_push_all(store, finding, prefs)
        self.assertEqual(delivered, 1)
        sent_to = mock_webpush.call_args.kwargs["subscription_info"]["endpoint"]
        self.assertEqual(sent_to, self.windows_sub["endpoint"])

    def test_both_disabled_delivers_to_neither_without_erroring(self):
        store = FakeStore([self.android_sub, self.windows_sub])
        finding = make_finding()
        prefs = copy.deepcopy(DEFAULT_NOTIFICATION_PREFERENCES)
        prefs["platforms"]["android"]["enabled"] = False
        prefs["platforms"]["windows"]["enabled"] = False
        with with_vapid(), patch("pywebpush.webpush") as mock_webpush:
            delivered = send_web_push_all(store, finding, prefs)
        self.assertEqual(delivered, 0)
        mock_webpush.assert_not_called()


class DecisionNotificationTests(unittest.TestCase):
    def setUp(self):
        self.android_sub = {"endpoint": "https://push/android-1", "p256dh": "k", "auth": "a",
                            "user_agent": "Mozilla/5.0 (Linux; Android 14)"}
        self.windows_sub = {"endpoint": "https://push/windows-1", "p256dh": "k", "auth": "a",
                            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def test_internal_lifecycle_stages_never_push(self):
        for stage in ("ACTIVITY_WATCH", "PRE_IGNITION", "AWAKENING", "STAIRCASE", "REARM"):
            finding = make_finding(stage=stage)
            self.assertIsNone(notification_phase(finding))
            self.assertFalse(notification_allowed_any_platform(finding, DEFAULT_NOTIFICATION_PREFERENCES))

    def test_setup_is_self_contained(self):
        finding = make_finding(
            trigger_level=2.04, invalidation_level=1.94,
            notification_reason="volume and price structure aligned",
        )
        message = _message(finding)
        self.assertEqual(notification_phase(finding), "setup")
        self.assertEqual(_user_title(finding), "⚡ TEST · FIRST MOVE SETUP")
        self.assertIn("Trigger $2.0400 (+2.00% away)", message)
        self.assertIn("Invalid below $1.9400", message)
        self.assertIn("Scout is monitoring this episode", message)

    def test_confirmation_has_one_user_facing_label(self):
        for stage in ("IGNITION", "BREAKOUT", "SURGE"):
            finding = make_finding(stage=stage, trigger_level=1.98, invalidation_level=1.92)
            self.assertEqual(notification_phase(finding), "confirmed")
            self.assertEqual(_user_title(finding), "✅ TEST · FIRST MOVE CONFIRMED")
            self.assertIn("Confirmed at $2.0000", _message(finding))

    def test_secondary_entry_is_labeled_and_late_move_is_suppressed(self):
        secondary = make_finding(stage="IGNITION", leg_context="CONSOLIDATION")
        self.assertEqual(_user_title(secondary), "✅ TEST · SECONDARY ENTRY CONFIRMED")
        self.assertTrue(notification_allowed_any_platform(secondary, DEFAULT_NOTIFICATION_PREFERENCES))

        late = make_finding(stage="IGNITION", timeliness_label="LATE")
        self.assertFalse(notification_allowed_any_platform(late, DEFAULT_NOTIFICATION_PREFERENCES))

    def test_shared_gates_still_block_regardless_of_platform(self):
        """A CHOPPY (non-CLEAN) finding must still be blocked for everyone -- the platform
        split must not weaken the pre-existing quality/master/session/quiet-hours gates."""
        store = FakeStore([self.android_sub, self.windows_sub])
        finding = make_finding(quality_label="CHOPPY")
        self.assertFalse(notification_allowed_any_platform(finding, DEFAULT_NOTIFICATION_PREFERENCES))
        with with_vapid(), patch("pywebpush.webpush") as mock_webpush:
            delivered = send_web_push_all(store, finding, DEFAULT_NOTIFICATION_PREFERENCES)
        self.assertEqual(delivered, 0)
        mock_webpush.assert_not_called()

    def test_group_b_never_generates_an_opportunity_notification(self):
        finding = make_finding(actionable_rank="B")
        self.assertFalse(notification_allowed_any_platform(finding, DEFAULT_NOTIFICATION_PREFERENCES))

    def test_clean_a_rank_momentum_surfaces_while_cohort_is_accumulating(self):
        finding = make_finding(stage="BREAKOUT", candidate_profile={"edge_validation": {
            "validated": False, "status": "EVALUATING", "samples": 13, "minimum_samples": 30,
        }})
        self.assertTrue(notification_allowed_any_platform(finding, DEFAULT_NOTIFICATION_PREFERENCES))

    def test_clean_a_rank_momentum_surfaces_when_trade_cohort_is_unprofitable(self):
        finding = make_finding(stage="BREAKOUT", candidate_profile={"edge_validation": {
            "validated": False, "status": "EVALUATING", "samples": 30, "minimum_samples": 30,
        }})
        self.assertTrue(notification_allowed_any_platform(finding, DEFAULT_NOTIFICATION_PREFERENCES))

    def test_special_event_notification_does_not_claim_trade_edge(self):
        finding = make_finding(stage="HALT", candidate_profile={})
        self.assertTrue(notification_allowed_any_platform(finding, DEFAULT_NOTIFICATION_PREFERENCES))

    def test_unvalidated_clean_a_momentum_has_no_ineligibility_reason(self):
        finding = make_finding(candidate_profile={"edge_validation": {"validated": False}})
        self.assertIsNone(
            notification_ineligibility_reason(finding, DEFAULT_NOTIFICATION_PREFERENCES, "windows"),
        )

    def test_unvalidated_non_clean_momentum_remains_suppressed(self):
        finding = make_finding(
            quality_label="DEVELOPING",
            candidate_profile={"edge_validation": {"validated": False}},
        )
        self.assertFalse(notification_allowed_any_platform(finding, DEFAULT_NOTIFICATION_PREFERENCES))
        self.assertEqual(
            notification_ineligibility_reason(finding, DEFAULT_NOTIFICATION_PREFERENCES, "windows"),
            "opportunity_gate",
        )


class DispatchLatencyGuardTests(unittest.IsolatedAsyncioTestCase):
    def test_stale_momentum_is_suppressed_but_recent_momentum_is_not(self):
        finding = make_finding(detected_at=1_000.0)
        self.assertIsNone(Dispatcher._stale_reason(finding, now=1_010.0))
        self.assertIn("stale_candidate", Dispatcher._stale_reason(finding, now=1_020.0))

    def test_special_events_have_a_longer_stale_window(self):
        finding = make_finding(stage="HALT", detected_at=1_000.0, candidate_profile={})
        self.assertIsNone(Dispatcher._stale_reason(finding, now=1_040.0))
        self.assertIn("stale_candidate", Dispatcher._stale_reason(finding, now=1_050.0))

    async def test_preferences_are_cached_during_a_burst(self):
        class PreferenceStore:
            calls = 0

            def get_notification_preferences(self):
                self.calls += 1
                return copy.deepcopy(DEFAULT_NOTIFICATION_PREFERENCES)

        store = PreferenceStore()
        dispatcher = Dispatcher(store)
        first = await dispatcher._notification_preferences()
        second = await dispatcher._notification_preferences()
        self.assertIs(first, second)
        self.assertEqual(store.calls, 1)


class ClientNotificationParityTests(unittest.TestCase):
    def test_client_continuation_gate_tracks_server_contract(self):
        source = (Path(__file__).resolve().parents[1] / "web/lib/native.ts").read_text(encoding="utf-8")
        self.assertIn('...SPECIAL, "REVERSAL_WATCH"', source)
        for gate in (
            "multi_timeframe?.qualified === true", "Number(profile.velocity || 0) >= 80",
            "Number(profile.participation || 0) >= 80", "Number(profile.structure || 0) >= 80",
            "Boolean(profile.box?.breakout)", "gates.fresh_impulse === true",
            "gates.bullish_confirmed === true", "gates.not_bearish_short === true",
            '"LOW PARTICIPATION", "SPARSE PRINTS", "STALE TRADES"',
        ):
            self.assertIn(gate, source)

    def test_service_worker_suppresses_push_banner_while_client_is_visible(self):
        source = (Path(__file__).resolve().parents[1] / "web/public/sw.js").read_text(encoding="utf-8")
        visibility_gate = 'windows.some(client=>client.visibilityState==="visible")'
        self.assertIn(visibility_gate, source)
        self.assertLess(source.index(visibility_gate), source.index("self.registration.showNotification"))


if __name__ == "__main__":
    unittest.main()

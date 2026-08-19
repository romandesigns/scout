# Milestone 003 — Web Push per-platform eligibility fix (real bug, found, fixed, tested)

Date: 2026-08-18

## Achieved
- Found a real, verified bug while investigating the user's cross-platform notification-sync
  request: every notification eligibility check in `app/notifiers.py`/`app/dispatch.py`
  hardcoded the platform string `"android"`, including for Web Push -- which is subscribed to
  identically from any platform's browser, not just Android. All webpush subscribers were
  gated by one shared toggle regardless of their actual device.
- Verified this precisely before fixing anything: confirmed the preferences schema really
  does define separate `android`/`windows`/`email` buckets (`app/preferences.py`), confirmed
  the frontend's client-side native-toast logic (`web/lib/native.ts`) already does this
  correctly per-platform (so the bug was specifically server-side, in the background-push
  path), and confirmed the `web_push_subscriptions` table already stores `user_agent` per
  subscriber -- the data needed for a fix already existed, unused.
- Fixed with a minimal, surgical change: split shared eligibility gates from the
  platform-specific toggle, added `infer_platform(user_agent)`, applied per-subscription
  inside `send_web_push_all`. ntfy behavior intentionally unchanged.
- 9 new regression tests (`tests/test_notification_platform.py`), all passing. Full suite
  132/132 green after the change.

## Correctly avoided overclaiming
- Nearly framed this as "the" cross-platform sync fix, then checked the actual Settings UI
  before writing that down. Found the "Windows native toast" feature is a *different*,
  deliberately-frozen-off mechanism (v6.5.3 product decision, stated in-UI: "Primary alert
  channel: Scout → ntfy. Desktop OS toasts are suppressed by default to avoid duplicate
  alerts."). So the real primary shared channel today is ntfy, not Web Push -- this fix is a
  genuine correctness improvement, not the dominant lever for the user's actual concern.

## Queued next (higher-value for the actual mandate)
- Investigate whether ntfy itself reliably reaches every platform consistently (topic
  sharing, desktop Tauri subscription, silent per-platform gaps). `E2E-VALIDATION.md`
  already notes Windows toast/sound delivery has only ever been verified manually, never by
  automation -- that's the real open question for "accurately synced... mobile, web, desktop."

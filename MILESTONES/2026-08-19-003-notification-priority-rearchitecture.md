# Milestone 2026-08-19-003 — Per-client notification priority: native primary, ntfy backup everywhere

Date: 2026-08-19

## What was built
Implements the user's explicit spec: **Tauri desktop → native toast primary, ntfy backup.
Installed mobile PWA (Android + iPhone) → native device push primary, ntfy backup. Plain
web browser tab → shadcn Base UI toast** (reverses the v6.5.3 decision that paused desktop
native toast in favor of ntfy as the sole primary channel).

### Real bug found and fixed along the way
`queueNativeScoutNotification` (the Tauri desktop toast trigger) was fully implemented but
**never called from anywhere in the app** -- the live SSE finding stream had no code path
that invoked it at all. Flipping the `windows.enabled` preference default alone (which was
also done) would not have produced any toasts without this fix. Found by grepping for call
sites before assuming the preference flag was the only problem.

### Changes
- `app/preferences.py`: `windows.enabled` now defaults `True` (was `False` since v6.5.3).
- `web/lib/native.ts`: refactored `nativeAllowed` into a shared `coreAllowed` (quality/
  master/signal-mode/score/session/quiet-hours) plus three platform-specific checks
  (`nativeAllowed` for Tauri, new `webPushForegroundAllowed` for installed PWA, new
  `webToastAllowed` for plain browser) -- mirrors the same split made server-side yesterday
  for Web Push eligibility (`app/notifiers.py`). Added `isInstalledPwa()` (standalone-display
  detection) and `showPwaForegroundNotification()` (calls `registration.showNotification()`
  directly from the page, not just from a push event, so an installed PWA gets a real native
  OS notification even while it's open and focused -- not just while backgrounded).
- `web/app/page.tsx`: SSE `finding` handler now dispatches to exactly one of the three paths
  based on runtime context (`isTauriRuntime.current` / `isInstalledPwa()` / else), using refs
  to avoid stale-closure bugs in the long-lived EventSource effect.
- `web/components/ui/toast.tsx`: new shadcn Base UI toast (`@base-ui/react/toast`, already a
  project dependency -- no new package added), matching the existing hand-rolled primitive
  style (`components/ui/tooltip.tsx`).
- `app/api.py`: new `GET /api/notifications/ntfy-config` exposes this deployment's own
  server/topic/subscribe URL so any device can be pointed at the backup channel from inside
  the app, instead of the operator needing to already know their own `.env` value. 2 new
  tests (`tests/test_ntfy_config_endpoint.py`).
- `web/app/page.tsx` Settings UI: rewrote the PLATFORMS tab copy to describe the new
  priority explicitly, un-froze the Windows toggle, added the ntfy backup-channel panel with
  a copy-to-clipboard subscribe URL.
- `README.md`: fixed a stale line describing ntfy as the sole primary channel (pre-v6.5.2
  wording that never got updated).

## Verified
- Full Python suite: 134/134 passing (132 existing + 2 new).
- `bun run build`: compiles clean, including a real TS error caught and fixed (`renotify`/
  `vibrate` are valid, already-used Notification API fields not in TS's default DOM lib type).

## NOT verified -- cannot be, from this environment
No real Windows Tauri build, no real Android/iPhone device, no real browser push permission
flow was exercised. Everything above is verified for **correctness of the code and successful
build**, not for **actual on-device behavior**. Before trusting this in practice: build and
install the Tauri desktop app and confirm a real toast appears on a live finding; install the
PWA on an actual Android phone and iPhone (iOS 16.4+, must be added to home screen first) and
confirm both background push and foreground native notification fire; open Scout in a plain
un-installed browser tab and confirm the shadcn toast appears without needing any permission
prompt. None of this was skipped out of laziness -- it genuinely requires physical devices
this environment doesn't have.

## Not committed, not deployed
Same standing rule as all week.

# Changelog

## 6.4.0 — Historical Replay Calibration

- Builds bounded multi-session datasets from Alpaca historical bars and trades.
- Replays exact trades through both the live Python oracle and the new Rust rolling-market candidate core with notifications hard-disabled.
- Adds explicit Python/Rust precursor parity reporting and prevents production cutover unless ticker, timestamp, and recipe evidence agree.
- Measures precursor lead time, false-arm rate, missed objective expansions, 15-minute favorable excursion, and per-ingredient success.
- Adds a calibration-ready application badge and compact summary to replay status.
- Preserves the existing environment contract and does not enable pre-ignition notifications.

## 6.3.0 — Pre-Ignition Intelligence shadow

- Adds a silent `PRE_IGNITION`/`ARMED` lifecycle event before confirmed release when the timestamp-safe recipe converges near a trigger.
- Persists the exact present and missing recipe ingredients, recipe evidence score, trigger distance, base extension, timeliness label, and precursor relationship.
- Keeps pre-ignition candidates in shadow mode: they appear in developing views and charts but cannot enter notification delivery queues.
- Links later ignition/first-leg findings to the precursor observed before them without moving or inventing chart markers.
- Adds orange shadow markers, marker tooltips, and an Inspector pre-ignition audit section.
- Adds a repeatable PowerShell 7 release-preparation command and introduces no new environment variables.

## 5.4.1 — Universe snapshot hotfix

- Fixed Alpaca multi-symbol snapshot parsing when the response is keyed directly by ticker.
- Restored price-filtered SIP/BOATS universe initialization after the V5.4 metadata upgrade.
- Startup logging now reports the persisted runtime scanner range rather than stale `.env` defaults.

## 5.4.0 — Visual intelligence and live scanner range

- Added a persisted live scanner range with a `$0.15-$10.00` default and desktop presets.
- Range changes now reconcile SIP/BOATS subscriptions and filter detections, gainers, alerts, and the dashboard.
- Added shadcn-style Recharts radar, radial, bar, area, and composed charts for setup quality, velocity, participation, validation, and time-of-day follow-through.
- Added prior close, gap percentage, session volume, projected session volume, and current shares-per-minute metrics from live snapshot/tape data.
- Added a five-factor candidate profile derived from velocity, participation, structure, catalyst, and market-quality evidence.
- Float remains explicitly unavailable unless a trusted float source is configured; Scout does not invent supply data.

## 5.3.0 — Practical momentum workstation

- Adds CLEAN, DEVELOPING, CHOPPY, and ILLIQUID market-quality classification.
- Requires bullish structural confirmation before price activity becomes actionable.
- Keeps rejected activity as silent ACTIVITY WATCH episodes instead of discarding it.
- Suppresses Windows, Android, and email delivery until price signals become CLEAN.
- Returns one evolving lifecycle row per ticker in Radar while preserving full event history.
- Adds A/B/C operational rank, quality score, and explicit rejection reasons.
- Stabilizes Radar and separates actionable, developing, and all candidate views.
- Adds active-pane targeting, chart pinning, and independent 15s/30s/1m/5m views.
- Adds directional efficiency, active-interval ratio, and reversal metrics to Inspector.

## 5.2.0 — Optimized coordinated release

- Aligns candles and event markers on one timestamp scale and adds Eastern Time chart labels.
- Staggers chart requests, slows inactive panes, pauses hidden-window polling, and separates core from heavy refreshes.
- Adds active-pane emphasis, reduces repeated chart tabs, and improves 4K readability.
- Groups duplicate catalyst stories across tickers.
- Reports offline feeds and unavailable notification delivery channels truthfully.
- Replaces subjective validation grades with timing-oriented labels.

## 5.1.0 — Production live dashboard

- Bundles the same-origin Scout dashboard, REST API, SSE stream, and charts.
- Uses the production `$0.15-$20.00` tracking and alert universe.
- Preserves the prioritized notifier queue, retry/backoff, and delivery health.
- Supports the self-hosted ntfy server already running beside Scout.
- Adds a private Tailscale deployment and verification runbook.

## 5.0.1 — Notification delivery hotfix

- Added bounded, priority-sorted ntfy and Resend delivery queues so provider waits never block market ingestion.
- Serialized each provider channel and added configurable minimum send intervals.
- Added exponential retry with jitter and `Retry-After` support for HTTP 429 and transient 5xx responses.
- Added notification queue depth, last attempt, last success, last error, and rate-limit state to `/api/status`.
- Kept findings persisted even if a provider queue is full or delivery is temporarily unavailable.

## 5.0.0 — Full Scout platform

- Preserved the proven V4 wake-up quality gates while extending Scout into a fused multi-engine detector.
- Added event-driven `SURGE` detection using rolling 3s / 5s / 10s / 15s price velocity plus participation gates.
- Added structural `BREAKOUT` detection against completed 1m / 3m / 5m resistance levels with penetration, volume, dollar-volume, trade-count and bullish-structure requirements.
- Retained `STAIRCASE` and stricter `IGNITION`, and added `REARM` continuation/re-breakout handling.
- Findings can carry multiple fused signal tags instead of forcing one market behavior into one category.
- Added post-detection outcome tracking for +1m / +5m / +15m / session maximum.
- Added full dashboard API, live SSE events, chart snapshots, live diagnostics, validation, timeline, Top Gainers, halt/resume and notification-preference endpoints.
- Added Alpaca trading-status halt/resume persistence and Scout timeline integration.
- Added responsive live candlestick charts with volume, EMA9, EMA21, VWAP, fused Scout markers, catalyst markers and halt/resume markers.
- Added frozen detection chart support without future candles.
- Completed the VS Code-style desktop workbench: Activity Rail, activity-driven Primary Sidebar, editor-group charts, Secondary Inspector and resizable/collapsible/maximizable bottom dock.
- Retained the deliberate 6px major-panel gutter / 4px chart-group gutter while **reducing border noise**. Major surfaces now rely on tonal separation, exposed canvas and hover/active states instead of outlines around every region.
- Added functional device-local Compact Density, Auto-focus Newest Signal and Show Chart Markers preferences.
- Added first-class Catalysts, Radar, Top Gainers, Halted, Validation, Alerts and System views.
- Added icon-only mobile market selector and bottom navigation with accessible labels.
- Added persisted notification controls for Android, Windows and email, including per-signal modes, sessions, quiet hours, critical bypass, minimum score, grouping and escalation behavior.
- Added Tauri 2 native shell foundations for Windows and Android.
- Windows: native notifications, deep links, single instance, tray, close-to-tray, window-state persistence and launch-at-sign-in control.
- Android: native channels, native finding actions/deep links and preference synchronization while retaining ntfy as the background delivery transport.
- Preserved the monitoring/decision-support-only safety boundary; no trading controls or order placement were added.

## 4.2.0 — VS Code-style Scout Workbench shell

- Rebuilt the desktop shell around VS Code workbench behavior.
- Added icon-only Activity Rail, resizable rounded panels, editor groups, Secondary Inspector and bottom intelligence panel.
- Added Tabler icon language and icon-only mobile navigation.
## 5.5.0 — Reversal-Reclaim episodes

- Added a separate midday reversal state machine without relaxing the global anti-chop gate.
- Added silent `REVERSAL_WATCH`, actionable `RECLAIM`, and first-pullback `REARM` lifecycle stages.
- Requires a material prior selloff, a recent local low, expanding participation, and EMA/VWAP recovery.
- A confirmed reclaim starts a fresh ticker episode so an earlier morning alert cannot suppress it.
- Persisted reversal context and exposed it in the desktop Inspector and notification preferences.
- Added a regression test modeled on the missed LFS-style selloff-and-reclaim pattern.
## 5.5.1 — Live tape calibration

- Required fresh 15-second participation for reversal promotion and separated EMA from VWAP reclaim labels.
- Added fresh velocity confirmation to breakout and ignition; strengthened weak-structure surge requirements.
- Required a documented pullback and elapsed continuation cycle before ordinary `REARM`.
- Consolidated rapid ticker-stage notifications while retaining every finding in the platform timeline.
- Made validation horizons remain pending until mature and floored favorable excursion at zero.
# 5.6.0 — First-leg attention intelligence

- Detects confirmed first expansion from bases, pullbacks, reclaims, consolidations, HOD rebreaks, and catalyst releases.
- Adds silent `FIRST_LEG_WATCH` and immediate `FIRST_LEG` with three-second confirmation/consolidation.
- Prevents silent findings from entering Android/email queues and prioritizes delivery by stage before score.
- Adds a priority inbox, Ross Criteria tab, release-context badges, and first-leg visual emphasis.
- Keeps Inspector pinned to explicit user selection instead of following new radar arrivals.
# 5.7.0 — Durable opportunity attention + PWA

- Added a server-persisted opportunity inbox grouped by ticker episode.
- Added a persistent spotlight with explicit Open, Watch, and Dismiss actions.
- Prevented live events from taking over charts or Inspector context.
- Added PWA manifest, service worker, install/update/offline UX, app badge support, mobile safe-area handling, push event routing, shortcuts, and share target metadata.
- Made `VERSION` visible in desktop/mobile/settings and added backend mismatch warnings.
- Hardened the one-command release with SSH/SCP retries and independent stage results.
# 5.7.1 — Container version-path hotfix

- Copied the root `VERSION` file into the Docker web-build stage so Next.js can embed the coordinated version during VPS builds.
- Added an explicit multi-path version lookup with a clear build error when the version source is missing.
# 5.8.0 — Verification workstation + early halt pressure

- Added Inspector Verification, Pattern, and History views with exact event timestamps, detection timeframe, frozen formation context, outcome windows, notification delivery evidence, and user star grading.
- Added event annotations to the selected chart: formation region, detection boundary, trigger, and invalidation levels, with global Chart settings.
- Added a regular-session HALT_PRESSURE evidence score and urgent early-alert route for accelerating, liquid bullish moves. This is a setup classifier, not a halt prediction or guarantee.
- Added dedicated Ross-style screener access, improved stock-row spacing, urgency labels, and a contextual stock actions menu.
- Centered and reorganized notification settings by platform, signal, session, and behavior.
- Persisted notification lifecycle records so delivery can be distinguished from detection.
# 6.0.0 — Contract-first Replay Spine

- Added canonical `scout.market-event.v1` NDJSON envelopes.
- Added a source-event-driven replay clock.
- Added hard-isolated replay dispatch with no push, ntfy, email, native or production-finding side effects.
- Added per-run state/report directories, dataset hashing, integrity counters and performance benchmarks.
- Added an Alpaca single-symbol session dataset builder and offline replay runner.
- Added deterministic replay fixtures and tests.
- Added replay status to the backend and `REPLAY READY` / `SIMULATION` UI states.

# 5.9.0 — Always-on catalyst monitoring + installed-PWA push

- Added server-originated Web Push with per-device subscription management, VAPID key provisioning, expired-device cleanup, deep links, delivery auditing, and ntfy fallback.
- Added one-tap background-notification enrollment for the installed Android PWA.
- Split news into `CATALYST_WATCH` before market confirmation and `CATALYST_ACTIVE` when clean price, volume, trade, and VWAP evidence confirms participation.
- Added optional catalyst watchlists and per-source health/error timestamps for Alpaca News, SEC, and configured RSS/Atom feeds.
- Added push and catalyst lifecycle tests and automatic production VAPID setup in the coordinated VPS deployment.
# 6.1.0 — Durable interactive charts + icon language

- Added detection-centered Alpaca historical trade fallback when the live state cache is cold after restart or outside market hours.
- Added minute-bar fallback when a historical trade window is unavailable.
- Added crosshair OHLCV inspection, wheel zoom, drag panning, timeframe aggregation, event markers, and reset-on-double-click behavior.
- Added `react-icons` and converted Radar urgency/signal labels into compact, color-coded, keyboard-accessible icons with explanatory tooltips.
- Disabled and removes legacy service workers/caches inside the Tauri shell while preserving PWA registration in browsers.
# 6.2.0 — Timeline truth + universal selection

- Kept detection lines at the engine's exact sub-second timestamp while highlighting the candle that contained the detection.
- Added an audit overlay showing detection time, seconds into the candle, detection price, and containing-candle range.
- Added separate notification queue/sent markers so detection and delivery timing cannot be confused.
- Exhausts Alpaca historical-trade pagination and reports trade/page provenance before rendering a completed formation.
- Removed edge-clamping for event markers and expanded the chart time domain through the full final candle.
- Added live numeric badges to every activity-rail panel icon with semantic urgency colors and accessible count labels.
- Made Radar, Ross, catalysts, gainers, halts, validation, and opportunity-inbox items explicitly update Inspector selection.
- Added contextual Inspector views for market entities that do not have a Scout detection, without implying that a detection occurred.
- Extended the icon-and-tooltip event language across chart headers, Inspector, halt, validation, and attention surfaces.
- Moved Tauri to a service-worker-free `native-v2` WebView data profile and added automatic one-time cache migration/reload.

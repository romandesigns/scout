# Scout unreleased implementation record

Status: implemented in the current working tree, not yet represented by a new
remote push. This record is derived from code as of 2026-08-23; it does not
present research ideas or untrained models as completed capabilities.

## Developer Mode and historical evaluation

- Added `/development` and its API workflow for querying a ticker, historical
  inspection window, and chart timeframe.
- Users can inspect stored detections or replay real Alpaca trades through the
  current detector using `rust`, `python`, or `both`. Rust is the recommended
  deterministic recipe engine; comparison mode preserves both result sets.
- Interactive tick replay is capped at four hours and is isolated from live
  notifications and trading.
- Evaluation output includes formation, trigger, invalidation, the 15-30 second
  target area, 30-second through 15-minute excursions, favorable/adverse R,
  detection timing, and notification-preview status.

Code: `app/development.py`, `app/live_replay.py`, `app/api.py`,
`web/app/development/page.tsx`.

## Objective momentum-zone measurement

- Added price/volume-derived bullish momentum zones independent of Scout's
  detections.
- Charts shade zones green when a qualifying detection caught the move inside
  its lead window and orange when Scout missed it.
- Reports include zone recall, precision, catch count, and median lead time.

Code: `app/momentum_zones.py`, `app/development.py`.

## Detection annotations and significance

- Added advisory Tier 1 structural-breakout, Tier 2 continuation-pulse, and
  Tier 3 reaction-bounce classifications.
- Charts use red, blue, and gray markers for these tiers; a cyan star marks
  where the real notification gate would have fired, excluding user channel
  preferences.
- Significance remains advisory and does not alter delivery.

Code: `app/significance_tier.py`, `app/dispatch.py`, `app/development.py`.

## Chart annotation and sharing

- Development charts can be enlarged and annotated with freehand marks,
  rectangles, circles, notes, undo, clear, download, and platform sharing.
- `Share for analysis` stores a PNG, notes, and review metadata. The API checks
  PNG format, safe filenames, and a 12 MB limit.
- The UI produces a copyable request with exact workspace paths so Codex can
  reopen the same chart and context.

Code: `web/components/chart-annotation-editor.tsx`, `app/development.py`,
`app/api.py`.

## Rust deterministic perception and replay

- Extended the Rust engine with stateful transitions shared by batch and live
  replay, plus an evaluation trace.
- Recipe evidence covers short-window velocity, participation, dollar-volume
  acceleration, liquidity, structure, and continuation/re-arm state.
- Cooldown and structure retention prevent recipe flicker from repeatedly
  re-arming an episode.
- Developer Mode can consume the local or containerized Rust executable. The
  Docker image packages it, avoiding Windows Application Control restrictions
  on a locally built executable.

Code: `rust/market-replay/src/lib.rs`, `app/live_replay.py`, `Dockerfile`.

## Historical backtesting and evaluation

- Alpaca trades replay through Scout's actual detector path instead of a
  simplified approximation.
- Results preserve candidate profiles, promotion traces, engine source, and
  diagnostics.
- Added utilities for training-data construction, objective-move scoring,
  artifact evaluation, visualization, and release-integrity validation.
- Chronological boundaries are retained to reduce future-data leakage.

Code: `scripts/historical_backtest.py`,
`scripts/build_imminent_training_data.py`, `scripts/imminent_move_scorer.py`,
`scripts/evaluate_imminent_alert_gate.py`, `scripts/check_release_integrity.py`.

## Learning layer

- Added one point-in-time feature contract shared by training and live scoring,
  preventing training/serving drift.
- The contract adds velocity persistence across 5/15/30 seconds, bounded
  price-participation coupling, VWAP distance, logarithmic float turnover,
  normalized compression quality, and deterministic stage/source encoding.
- Rust- and Python-originated findings use the same transformations.
- Training uses chronological train, validation, and held-out test dates and
  requires at least three dates, a configurable number of matured alerts, and
  both positive and negative examples.
- Research labels now retain target-before-invalidation, five-minute maximum
  favorable excursion, and five-minute maximum adverse excursion alongside the
  strict imminent-move label.
- Threshold selection uses validation data; test metrics remain held out.
- Artifacts are atomically replaced. Runtime caching detects modification-time
  changes and loads the newer complete artifact.
- Runtime inference is fail-open and shadow-only. It records probability,
  threshold, would-pass, and model test date, but cannot suppress alerts.

Code: `app/learning_features.py`, `app/imminent_gate.py`,
`scripts/train_imminent_alert_gate.py`, `app/dispatch.py`.

## Unified momentum-structure evidence

- Translated the applicable shared observations from O'Neil-, Minervini-,
  Darvas-, and Cameron-style momentum analysis into one advisory structure
  profile rather than separate competing detectors.
- Progressive contraction measures range tightening, volume contraction,
  higher-low retention, and location within the recent base.
- An adaptive box records point-in-time support, resistance, width, breakout,
  retention, and a bounded quality value.
- Lifecycle context classifies `FRONT_SIDE`, `TRANSITION`, or `BACKSIDE` from
  drawdown, repeated high tests, lower highs, and duration below VWAP.
- Pullback context measures retracement depth, participation contraction, box
  retention, and higher-low behavior when a continuation peak exists.
- Correlated observations are capped into `supply` and `lifecycle` families;
  they do not add multiple bonuses to the legacy detector score.
- These fields are persisted in `candidate_profile`, exposed in Developer Mode,
  and included in the shared shadow-learning contract. They do not yet gate
  notifications.

Code: `app/unified_structure.py`, `app/market.py`, `app/learning_features.py`,
`app/development.py`, `web/app/development/page.tsx`.

## Persistence, API, and frontend contracts

- Added storage/round-trip support for richer candidate profiles and evaluation
  metadata.
- Added Development evaluation, annotation, chart-serving, and replay-status
  endpoints.
- Extended frontend API/types for live replay, significance, objective zones,
  annotation artifacts, and learning-gate results.

Code: `app/db.py`, `app/api.py`, `web/lib/api.ts`, `web/lib/types.ts`.

## Build, release, configuration, and documentation

- Synchronized application, web, Tauri, and service-worker versions/caches.
- Added scientific-learning dependencies and Rust build/package steps to
  Windows, Docker, and release scripts.
- Updated Next.js packaging for the static Development route.
- Added `IMMINENT_GATE_MODEL_PATH` for an optional shadow artifact and hybrid
  Rust controls for batching, merging, deduplication, and confidence behavior.
- Reorganized operational documents into `docs/guides` and version history into
  `docs/implementation-history`; removed backup/accidental web files.

Code: `.env.example`, `app/config.py`, `requirements.txt`, `Dockerfile`,
`build-windows.ps1`, `prepare-release.ps1`, `release-all.ps1`, `VERSION`,
`web/next.config.ts`, `web/src-tauri/tauri.conf.json`.

## Verification

- Focused learning/replay tests: 8 passed.
- Full Python suite: 229 passed.
- `git diff --check` reported no patch-format errors. Windows line-ending notices
  were informational.
- Existing dependency-deprecation and Windows asyncio cleanup warnings did not
  cause failures.

## Intentional safety boundary and remaining evidence

- No model is production-valid merely because the pipeline exists. It still
  needs enough matured positive/negative alerts and acceptable held-out results.
- Neither Rust nor Python mutates model weights inside the live event loop.
- Shadow predictions do not change notification eligibility.
- Order-flow imbalance and ticker/time-of-day historical baselines were not
  promoted as production signals because the current trade-only replay contract
  lacks sufficiently reliable point-in-time quote/baseline inputs.

Deterministic detection therefore remains the production authority while the
learning layer accumulates evidence safely.

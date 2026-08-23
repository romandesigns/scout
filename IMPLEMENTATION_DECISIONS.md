# Implementation Decisions

This file preserves implementation agreements across interrupted or separate chat
sessions. Update it whenever the user and Codex agree on an implementation idea.

## Logging convention

For each agreement, record:

- the date;
- the status (`Agreed`, `Revised`, `Implemented`, or `Superseded`);
- the decision and its intended outcome;
- important constraints or tradeoffs;
- affected files or components, when known;
- any unresolved follow-up work.

When an agreement changes, retain the original entry and mark it `Superseded`,
then link or refer to the replacement entry. Do not record tentative suggestions
as agreed decisions.

## Decisions

### 2026-08-22 — Maintain a durable implementation decision log

- **Status:** Agreed
- **Decision:** Update this document whenever the user and Codex agree on an
  implementation idea.
- **Outcome:** Future sessions can recover agreed direction without requiring the
  user to repeat prior discussions.
- **Constraints:** Record only actual agreements, keep entries concise, and retain
  historical decisions when they are revised or superseded.
- **Affected area:** Project documentation and future implementation work.
- **Follow-up:** At the start of related work, consult this log for applicable
  prior decisions.

### 2026-08-22 — Target pre-momentum bullish notifications

- **Status:** Agreed
- **Decision:** Scout should aim to notify the user 5–10 seconds before a
  significant bullish momentum expansion, based on bullish setup ingredients
  detected during the preceding compression or transition into expansion.
- **Outcome:** Alerts should arrive before the displacement move rather than
  during the move or after the price has already expanded.
- **Constraints:** A 60-second candlestick chart cannot establish 5–10-second
  timing. Trigger development and validation require tick or 1-second data and
  must avoid labeling sideways liquidity accumulation as momentum.
- **Affected area:** Bullish-event detection, notification timing, historical
  audit charts, and alert-quality evaluation.
- **Follow-up:** Define the exact causal, real-time bullish ingredients and test
  notification latency against sub-minute market data without look-ahead bias.

### 2026-08-22 — JUNS audit notification interpretation

- **Status:** Agreed
- **Decision:** Use the annotated JUNS chart as a near-term implementation
  reference for separating bullish impulse segments from notification-worthy
  events. Do not alert on every upward impulse or reaction bounce.
- **Candidate notification windows:** Approximately 07:08 (opening ignition),
  07:17–07:18 (high-confidence opening breakout), 07:41–07:42
  (medium/high-confidence continuation), 08:32–08:34 (lower-confidence
  recovery), 08:52–08:54 (medium-confidence local-range breakout), and
  09:56–09:58 (highest-confidence major breakout). These are minute-level
  estimates, not validated second-level trigger timestamps.
- **Suppression examples:** The moves near 07:29 and 08:25 should generally be
  suppressed because they resemble reaction bounces inside volatility rather
  than independent, significant momentum setups.
- **Alert semantics:** A formation detected substantially before a move should be
  treated as a non-actionable watch state. The actionable notification should
  occur only when the causal bullish ingredient threshold is satisfied, with a
  target lead of 5–10 seconds. For the final JUNS breakout, the 09:15 Scout
  detection was too early to be the actionable alert; the relevant alert window
  was approximately 09:56–09:58.
- **Candidate ingredients:** Compression or absorption, rising trade rate,
  aggressive buying, ask-side liquidity consumption, higher lows or shallower
  pullbacks, relative-volume acceleration, pressure against a meaningful local
  boundary, and evidence that the move is not merely a bounce inside congestion.
- **Constraints:** Develop and validate exact trigger timing with tick or
  1-second data, causal feature timestamps, notification delivery timestamps,
  cooldown/deduplication rules, and no look-ahead bias.
- **Affected area:** Bullish-event labels, confidence tiers, watch-state logic,
  alert gating, notification timing, and historical evaluation.

### 2026-08-22 — WEN audit and sub-minute ignition-phase notification timing

- **Status:** Agreed
- **Decision:** Use the annotated WEN chart (the ~10:50 explosive expansion
  candle) as a second near-term reference, specifically for pinning down when
  during a fast, near-vertical candle's formation Scout should notify, and for
  designing the concrete 5–10-second-window detection mechanics (not just the
  target timing).
- **Candidate notification windows (minute-level, unvalidated):** Watch state
  ~10:47–10:49 as the decline flattens and price lifts from the base; primary
  actionable alert ~10:49:50 (before/at the base break, medium confidence at
  minute resolution); reclaim/continuation alert ~10:58:50–10:59:00 after the
  pullback holds and a higher low forms; secondary expansion alert
  ~11:07:50–11:08:00; trend-continuation breakout alert ~11:16:50–11:17:00.
  The existing blue 11:00 Scout reference was judged roughly ten minutes late,
  positioned after the explosive candle and most of its pullback, and
  consistent with the poor audit outcome (STOP_FIRST, invalidation breached,
  16.9% capture efficiency) — useful at best as a continuation signal, not the
  primary momentum alert.
- **Ignition-phase refinement:** For an abrupt, near-vertical candle, do not
  wait for a fixed lead time before the candle exists, and do not notify once
  the body is already visibly extended. Target the first ~10–20% of the
  candle's eventual expansion (for WEN, roughly the break of the
  ~$7.67–$7.70 base area, before price reached $8.00+), not the midpoint
  (chasing acceleration) or the top (chasing exhaustion). Conceptual sequence:
  `base/compression → ignition → acceleration → vertical extension →
  exhaustion`, with the notify point at ignition, immediately after the base
  break confirms.
- **5–10-second detection mechanics:** Within a strict 5–10-second window,
  Scout should not attempt to "confirm" that a large move will happen — that
  requires hindsight. Instead detect a rapidly rising probability of ignition
  while price is still near the base, via a staged sequence:
  - **T−10s to T−6s (setup/pressure shift):** price holds near the compression
    ceiling; pullbacks shrink and recover faster; the bid steps up or
    repeatedly refreshes; spread stays controlled; trade activity rises versus
    the preceding 30–60s.
  - **T−6s to T−3s (pre-ignition evidence):** more trades print at/near the
    ask; buy-initiated volume materially exceeds sell-initiated volume; quotes
    update faster; best ask repeatedly changes or its displayed size is
    consumed; price spends less time reverting to the range middle; the
    developing candle challenges the local high without yet extending.
  - **T−3s to T0 (ignition/actionable):** price breaks the local high by a
    small threshold; several consecutive trades print at progressively higher
    prices; trade-rate acceleration persists for multiple seconds; the bid
    follows price up rather than lagging; no immediate retracement into the
    base. This is where the actionable alert fires.
- **Scoring model and state machine:** Evaluate a rolling score every
  100–250ms combining trade-rate acceleration, aggressive-buy ratio, bid
  step-up, ask consumption, micro-range pressure, pullback resilience, and
  relative-volume acceleration, penalized by spread instability, immediate
  rejection, and stale/isolated prints. Thresholds must be calibrated per
  stock from its own recent percentile distributions, not fixed raw values.
  States: `WATCH` (structural setup present) → `ARMED` (multiple pre-ignition
  ingredients persist 2–3s) → `NOTIFY` (score crosses calibrated threshold
  with both trade and quote confirmation) → `CANCEL` (price falls back into
  range, spread destabilizes, or buying pressure disappears). Require
  persistence across a short sequence of mutually supporting evidence rather
  than acting on one large print, which could be delayed, off-exchange, or
  unrepresentative.
- **Alert semantics:** The 5–10-second alert must be phrased as rising
  probability, not certainty (e.g. "pre-momentum pressure detected — bullish
  ignition likely; monitoring break above $X"). Reaching this early necessarily
  means accepting more false positives; this early/probabilistic alert should
  be distinguished in messaging and/or tiering from a stronger,
  breakout-confirmed alert.
- **Data source notes:** Alpaca's real-time stock WebSocket provides
  nanosecond-timestamped trades and top-of-book quotes (trade price/size,
  best bid/ask price/size) — sufficient for these rolling features and
  preferable to polling. It is top-of-book only, not full depth-of-book, so
  "ask consumption" must be inferred from quote changes plus trade prints,
  not from true book depletion. Alpaca also exposes timestamped historical
  trades/quotes suitable for event-by-event replay validation.
- **Validation requirement:** Historical validation must replay trades/quotes
  event-by-event and check, using only information available at each instant,
  whether Scout's score would have crossed threshold 5–10 seconds before the
  labeled impulse — no look-ahead bias, and no backward-selected timestamps.
  For WEN specifically, the true target is not a fixed clock time (not
  "10:49:50") but the first second at which compression, trade-rate
  acceleration, ask-side activity, rising bid, and lack of rejection jointly
  crossed the calibrated threshold while price was still at the breakout base;
  recovering that exact second requires tick/1-second replay.
- **Affected area:** Same as the JUNS entry above (bullish-event labels,
  confidence tiers, watch-state logic, alert gating, notification timing,
  historical evaluation) plus: real-time feature computation off Alpaca
  trade/quote streams, per-symbol threshold calibration, and the WATCH →
  ARMED → NOTIFY → CANCEL state machine design.
- **Follow-up:** Implement the rolling score and state machine against tick/
  1-second data, calibrate thresholds per symbol from historical percentile
  distributions, and validate against both the JUNS and WEN labeled events
  without look-ahead bias before any live use (see the standing never-ship-
  without-review constraint).

### 2026-08-22 — Deterministic significance tiering and would-notify chart preview (Claude Code)

- **Status:** Implemented (advisory only; not wired into live gating; pending
  the user's local review before anything ships)
- **Decision:** Add a deterministic (non-ML) significance tier and a
  notify-gate preview to every Scout detection, and render both directly on
  the Scout Development chart-review tool, operationalizing the JUNS/WEN
  tiering scheme above without waiting on the ML imminent-move gate (which
  its own backtest reports show is not deployment-ready -- see
  `data/optimization/backtest/imminent-model-v3-gated-scout-2026-08-21-report.json`,
  0/30 actionable alerts passed, 0 recall on 1048 objective moves).
  - `app/significance_tier.py`: `classify_tier()` labels a detection Tier 1
    (structural breakout: rank A, quality CLEAN, a confirmed impulse stage
    -- BREAKOUT/IGNITION/SURGE -- opportunity_class FIRST_MOVE, and at least
    2 of 4 magnitude confirmations at 1.5x the normal trigger bar), Tier 2
    (continuation pulse/reclaim that is clean and confirmed but doesn't clear
    the Tier 1 magnitude bar), or Tier 3 (reaction bounce: rank/quality below
    the clean bar, already extended/late, or a "reaction-bounce signature" --
    2+ of {directional_efficiency, direction_reversals, active_bucket_ratio}
    sitting in the marginal zone past Scout's hard quality-reject threshold
    but short of clean/confirmed). `would_notify()` mirrors
    `notifiers._allowed_platform_agnostic` (shadow_mode, `can_notify_opportunity`,
    `USER_NOTIFY_STAGES`, edge_validation, quality==CLEAN) minus the human's
    own preference toggles, so it answers "was this moment notify-worthy",
    not "did the user's settings happen to allow it".
  - Both functions accept either a live `Finding` or a stored finding dict
    (duck-typed), and reuse the exact `opportunity.py`/`notifiers.py`
    production contracts rather than re-deriving the logic, so there is no
    drift between the preview and the real gate.
  - `app/dispatch.py` tags every new finding's `candidate_profile` with
    `significance_tier` and `would_notify_preview` at detection time
    (advisory metadata only -- the actual delivery gates,
    `notification_allowed`/`notification_allowed_any_platform`, are
    unchanged).
  - `app/development.py`'s chart renderer now recomputes tier and
    would-notify fresh from each stored detection's own fields (not from
    cached `candidate_profile`), so it works retroactively on every
    historical detection already in the database, not only ones saved after
    this change. Detections are now color-coded by tier (red/blue/gray for
    Tier 1/2/3) with a tier legend, and a detection where the notify preview
    fires gets an additional cyan star marker distinct from the existing gold
    ring used for an actually-delivered notification. The shadow ML gate's
    PASS/REJECT/UNSCORED status is kept as secondary annotation text.
  - `web/app/development/page.tsx` and `web/lib/types.ts` updated to show the
    new tier counts, would-notify-preview count, and the new legend.
- **Verified locally:** full existing pytest suite (214 tests) still passes;
  16 new tests added in `tests/test_significance_tier.py` covering Tier
  1/2/3 classification (including a rank-A/quality-CLEAN finding still
  correctly downgraded to Tier 3 by the reaction-bounce signature), the
  would-notify preview's pass/fail reasons, and dict-row vs. `Finding`-object
  equivalence; `tsc --noEmit` on `web/` is clean. Also smoke-tested end to
  end against real Alpaca historical data through the actual `evaluate_ticker`
  path for several tickers with real stored findings (CONL, DXST, KNRX),
  confirming correct tier labels/reasons and a correctly rendered chart with
  the new legend and markers.
- **Known caveat found during verification:** the current `.local-dev/state.db`
  is almost entirely `shadow_mode=1` backtest/replay data with zero delivered
  notifications ever recorded, and even its 34 non-shadow rank-A/CLEAN
  findings show `would_notify: false` for all of them -- mostly
  `edge_not_validated` (24/34) and a handful of `opportunity_gate`/
  `stage_not_user_facing`. This matches the theme found elsewhere in this
  session (the ML gate's own gated-backtest report also shows near-zero
  recall): detections are passing quality/rank checks but rarely clearing
  the harder deployment-readiness gates (edge validation, multi-timeframe
  qualification) in this dataset. Worth checking against a live/production
  database, or a dataset with more paper-trading history, to see whether
  `would_notify: true` fires as expected on a genuinely strong setup.
- **Constraints:** Entirely advisory -- no change to what Scout actually
  detects or sends. The tier thresholds are a first deterministic pass
  reusing existing config knobs (`vol_ratio_trigger`, `ignition_score`,
  `price_60s_trigger_pct`, `quality_min_directional_efficiency`,
  `quality_max_direction_reversals`, `quality_min_active_ratio`) at
  hand-picked multipliers, not yet calibrated against a labeled outcome set
  the way the WEN/JUNS follow-up work calls for.
- **Affected area:** `app/significance_tier.py` (new), `app/dispatch.py`,
  `app/development.py`, `web/app/development/page.tsx`, `web/lib/types.ts`,
  `tests/test_significance_tier.py` (new).
- **Follow-up:** User to visually re-verify tiering/would-notify markers on
  their own tickers of interest in Scout Development locally before any of
  this is considered for wiring into live gating or delivery. If the tier
  thresholds look wrong on real examples, recalibrate rather than re-derive
  from scratch. Once validated, this tiering is the natural place to attach
  the WATCH/ARMED/NOTIFY state machine and sub-minute ignition timing from
  the entry above.

### 2026-08-22 — Tier 1 magnitude bar recalibrated against real detections (Claude Code)

- **Status:** Implemented, supersedes the Tier 1 magnitude check in the entry
  above (tiering concept and would-notify preview are unchanged).
- **Decision:** Before the user started testing, ran `classify_tier` against
  every real stored rank-A/quality-CLEAN/confirmed-impulse-stage finding in
  `.local-dev/state.db` and found the original "2 of 4 signals each at 1.5x
  their normal trigger" magnitude bar **never fired on real data** -- zero
  Tier 1 results across 11 genuine candidates. Root cause: score and 30-60s
  price-change are already saturated near the rank-A floor for real confirmed
  findings (observed scores were 9-10 against a required >=11; observed 60s
  price changes were mostly under 1%, occasionally ~2%, against a required
  >=3%), so those two signals essentially never contribute a hit; only
  relative volume showed real dynamic range (6x-300x baseline).
  Replaced with three OR'd paths, any one of which plus a confirmed impulse
  stage (BREAKOUT/IGNITION/SURGE) and opportunity_class FIRST_MOVE now
  qualifies Tier 1: (a) extreme relative volume, reusing the exact
  `vol_ratio_trigger * 2` bar `market.py` already scores as "extreme volume
  anomaly" evidence -- the primary, real-data-validated path; (b) exceptional
  composite score (`ignition_score + 3`) and (c) sustained 30s+60s price
  expansion (`price_60s_trigger_pct` on the 60s change, half that on 30s) --
  both kept for other tickers/datasets where volume isn't the standout
  signal, though neither fired in this sample.
- **Verified:** re-ran against the same 11 real candidates: 4 now correctly
  classify Tier 1 (CONL, OPEN, CRML, KNRX -- all genuine high-relative-volume
  breakouts), 7 stay Tier 2, including one (DXST, finding 1289) whose
  `leg_context` was `CONSOLIDATION_RELEASE` -- correctly kept out of Tier 1
  by the existing `opportunity_class` continuation-keyword check even though
  its volume was extreme, since a consolidation release is a continuation
  event, not a first move. Confirmed visually through the live, restarted
  backend and rebuilt frontend: `CONL` finding 260 (a real 2026-08-21
  BREAKOUT, 184.9x relative volume) now renders as a red T1 marker on its
  Scout Development chart, distinct from the surrounding gray T3 markers.
  Full pytest suite still 214/214 passing after the change.
- **Constraints:** Same as the entry above -- still a first calibration pass
  reusing existing config knobs, still advisory only, still not validated
  against a labeled outcome set.
- **Affected area:** `app/significance_tier.py` only.
- **Follow-up:** Same as the entry above. If Tier 1 still looks too rare or
  too common once the user reviews more tickers, adjust the OR'd path
  thresholds rather than reintroducing the "N of 4" scheme, which the real
  data showed doesn't fit how these fields actually distribute.

### 2026-08-22 — On-demand live detector replay in Scout Development (Claude Code)

- **Status:** Implemented, verified against real Alpaca data.
- **Decision:** The stored-detection chart lookup (`evaluate_ticker`'s default
  path) only visualizes detections already sitting in the database -- for a
  ticker/date Scout never actually watched live (including every "raw
  historical chart" example manually reviewed with Codex, e.g. WEN
  2026-08-12 and JUNS), nothing was ever marked, which the user correctly
  flagged as not answering "what would trigger events during a live
  session". Added a second mode that answers that question directly: a new
  "Run Scout's live detector over this window" checkbox (only available
  alongside the existing inspection-range picker, capped at 4 hours) fetches
  real Alpaca trades for exactly that ticker/window and replays them through
  Scout's actual production `MarketWatcher` detector via the existing,
  already-used-for-offline-validation `app.replay.run_dataset` engine (the
  same one `scripts/historical_backtest.py` uses) -- fully isolated, a
  throwaway SQLite state file, no live notifications. The resulting real
  findings are then tiered and marked through the exact same
  `classify_tier`/`would_notify` code path as stored detections (new module
  `app/live_replay.py`: `run_live_detector()` builds the trade dataset and
  runs the replay; `finding_from_row()` rebuilds a real `Finding` from a
  replay result row so `Store.paper_edge_validation` -- normally only called
  by the live dispatcher -- can be reused unmodified to fill in
  `would_notify`'s edge-validation check for these detections too).
- **Verified:** `tests/test_live_replay.py` (5 tests: overlong-window
  rejection, missing-credentials rejection, no-trades handling, and a real
  end-to-end run of synthetic trades through the actual detector, confirming
  the temp NDJSON dataset is cleaned up afterward) and two new
  `tests/test_development.py` cases (live-detector wiring, and requiring an
  inspection range). Full suite 221/221 passing. `tsc --noEmit` clean.
  End-to-end smoke test through the live, restarted backend: replayed WEN's
  real 2026-08-12 10:30-13:30 ET window (the exact window from the original
  Codex chart-review conversation) -- 120,372 real trades processed in ~40s,
  producing 73 real detections none of Scout's live pipeline had ever
  stored, correctly tiered (mostly Tier 3 watch-stage, 2 Tier 2
  continuations).
- **Finding surfaced by this test, relevant to [[scout-detection-optimization]]
  (the ongoing early-detection-gate work)**: in that WEN replay, Scout's
  real detector fired a dense cluster of `PRE_IGNITION`/`ACTIVITY_WATCH`
  Tier 3 detections through the base/compression leading into the move, but
  produced **no `BREAKOUT`/`IGNITION`/`SURGE` finding at all during the
  explosive vertical candle itself** (~10:52-10:56 ET) -- the next detection
  after the spike is a `REVERSAL_WATCH` well after the peak. This is an
  independent, real-data reproduction of the root cause already diagnosed in
  that work (fast/modest moves outrun the multi-gate convergence time), now
  demonstrated on a concrete labeled example rather than aggregate stats.
  Not investigated further in this session -- flagged for whoever picks up
  the detection-gate work next.
- **Constraints:** Read-only against Alpaca's historical trades endpoint;
  writes only to a throwaway per-run SQLite file and a temp NDJSON dataset
  (deleted after use) under `data/replays/adhoc/` -- never touches the live
  findings database or sends notifications. 4-hour window cap keeps it
  interactive; a full trading day would need the batch
  `historical_backtest.py` path instead. `would_notify`'s edge-validation
  check for these ad hoc findings queries the real production store's
  historical track record, so results can differ from a from-scratch replay
  with no prior paper-trading history.
- **Affected area:** `app/live_replay.py` (new), `app/development.py`,
  `app/api.py`, `web/app/development/page.tsx`, `web/lib/api.ts`,
  `web/lib/types.ts`, `tests/test_live_replay.py` (new),
  `tests/test_development.py`.
- **Follow-up:** If the WEN escalation gap above turns out to reproduce on
  other examples, it's concrete evidence for the coordinated multi-gate
  fix already designed (per `SESSION-STATE.md`) but not yet run.

### 2026-08-22 — Automatic ground-truth momentum-zone annotation (Claude Code)

- **Status:** Implemented, verified against real Alpaca data (GOSS).
- **Decision:** The tier/would-notify markers only show what Scout's own
  detector produced -- on a session where nothing escalated past Tier 3
  (as WEN and GOSS both turned out to), the chart shows a wall of identical
  gray markers with nothing to visually compare against, which doesn't let
  the user audit accuracy. Added a second, independent annotation layer,
  computed straight from price/volume with no dependency on Scout's
  pipeline at all: `app/momentum_zones.py`. `find_momentum_zones()` reuses
  the same "objective move" shape already established in
  `app.replay.calibrate_pre_ignition` (rolling base-window low, minimum
  expansion_pct, deduplicated episodes) -- 2% expansion from a 5-minute
  rolling low, extended to the highest high within 15 minutes -- applied to
  bar closes instead of tick prices, since chart-level audit doesn't need
  tick resolution. `match_detections_to_zones()` then checks, for each real
  zone, whether any of Scout's own detections that would count as "caught
  this" (tier 1/2, or the would-notify preview firing) landed inside
  [onset - 120s, peak] -- attaching `caught`/`lead_seconds` to each zone.
  Wired into `evaluate_ticker`/`_render`: zones now shade automatically on
  every chart (green if caught, orange if missed, labeled with expansion %
  and lead/lag), with `momentum_zones_marked`/`_caught`/`catch_rate_pct` in
  the evaluation's metrics and new stat tiles on the Development page. This
  runs for both the stored-detection and live-detector-replay chart modes.
- **Verified:** `tests/test_momentum_zones.py` (7 tests: a clear expansion is
  found and measured correctly, flat/declining price produces no zones, a
  single fast thrust isn't double-counted as separate zones per bar, two
  separated expansions produce two zones, and the matching logic's
  earliest-qualifying-hit/miss/no-detections cases). Full suite 228/228.
  `tsc --noEmit` clean. End-to-end: re-ran GOSS's real 2026-08-12 session
  through the live backend with zero manual scripting -- it automatically
  found and shaded exactly one real momentum zone (13:01-13:14 ET, +3.0%)
  and correctly marked it MISSED, since none of GOSS's 68 live-detected
  findings that session ever cleared Tier 3.
- **Correction recorded during this same verification pass**: while manually
  reading the GOSS chart before this feature existed, a ~12:07-12:16 ET push
  was called out as "Tier 1, backed by the largest volume print" -- both
  claims were wrong (checked against the real per-minute data: the largest
  volume bar of the session is actually at 12:56, a down-then-recover flush,
  not that push; and the push itself only expanded ~1.95%, just under the
  2% real-move bar). Left as a demonstration of why this tool -- computing
  zones from the actual fetched bars -- is more reliable than reading a
  compressed chart image by eye, which is exactly the failure mode the user
  was trying to move past by asking for this feature.
- **Constraints:** The 2% / 5-minute / 15-minute thresholds are the same
  ones already validated elsewhere in this codebase for defining an
  "objective move" (not newly invented for this feature), but were not
  re-tuned specifically for chart-level bar-close granularity; a widen-the-
  base-window experiment (300s/600s/900s/1200s) on the GOSS data made no
  difference to the result, confirming the 12:07 push's exclusion is a
  genuine below-threshold call, not a base-window artifact. Advisory/display
  only -- does not affect live detection or delivery.
- **Affected area:** `app/momentum_zones.py` (new), `app/development.py`,
  `web/app/development/page.tsx`, `web/lib/types.ts`,
  `tests/test_momentum_zones.py` (new).
- **Follow-up:** Once the user has audited several tickers this way, the
  aggregate catch rate across many real momentum zones (not just one
  session) becomes a genuine, non-anecdotal accuracy metric for the
  detection-optimization work -- worth surfacing as a rollup (e.g. in the
  Insights tab) if the per-chart view proves useful.

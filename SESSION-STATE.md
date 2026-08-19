# Scout Session State — Continuation Notes

Written 2026-08-18, ~06:55 EDT, immediately before a planned computer restart, so work can
resume with full context. Read this first in any new session before continuing.

## Your goal (confirmed with the user directly)
Catch explosive bullish moves early enough to actually participate in the trade, and catch
all meaningful bullish moves early — not just the big ones. Scout is decision-support, not
an auto-trader: the goal is Scout surfacing a trustworthy early signal so the user can act,
not Scout executing anything.

## Hard constraint — read this before doing anything else
**Do not commit, push, deploy, or otherwise make live any change.** The user is switching
permission mode to auto (no per-prompt confirmation needed for implementation steps), but
that does NOT extend to shipping anything. All work stays local/uncommitted until the user
returns from work and reviews it with you. This applies to every file below.

## What has been established, in order

1. **Effectiveness baseline (live data)**: single-day live audit showed ~3.5% "useful" rate
   (95% CI 1.6–7.4%) on actionable-tier findings, ~6% promotion selectivity from the funnel.
   Too small a sample to trust on its own — this is what motivated the backtest below.

2. **Repo cleanup** (committed to git working tree, staged but not committed as of this
   writing — confirm status): removed ~29 stale one-off files (old validation transcripts,
   timestamped audit reports that should never have been tracked), moved one orphaned script
   into `release-history/`, extended `.gitignore`. See earlier conversation for the full list
   if needed; not critical to re-derive.

3. **Discovered uncommitted work already in the tree** (not built by this session, found by
   inspection): v6.7.2 (`scripts/recall_opportunity.py`, `scripts/aggregate_detection_quality.py`)
   and v6.7.3 (`scripts/independent_market_data.py`, MIXED-outcome decomposition in
   `scripts/detection_quality.py`). `VERSION` file reads 6.7.3. None of this is committed.
   The independent Alpha Vantage provider needs `ALPHAVANTAGE_API_KEY` set to activate.

4. **Built a historical backtest pipeline** (new this session, fully working, real Alpaca
   data, replays through Scout's actual production detector code via `app.replay.run_dataset`):
   - `scripts/historical_mover_finder.py` — detector-blind ground truth of real movers per
     date range, by price action alone (Alpaca 1-min bars).
   - `scripts/sample_movers.py` — stratified bounded sample (caps replay cost).
   - `scripts/historical_backtest.py` — replays real tick data through Scout's real detector,
     captures every finding including full `promotion_trace` gate detail. Caches raw NDJSON
     under `data/replay-datasets/backtest/` so reruns don't re-download.
   - `scripts/backtest_scorer.py` — joins ground truth + replay findings, computes recall
     (seen / seen-before-cross / actionable-before-cross per +5/10/20/50% tier) and precision.
   - `run-historical-backtest.ps1` — wrapper chaining all stages, `-Sample` flag enables it.
   - Full writeup: **`HISTORICAL-BACKTEST.md`** at repo root — read that for the complete
     methodology and result detail; this file only summarizes.
   - **Empirically confirmed cost**: Stage 1 (ground truth) ≈ 51 sec/trading day, full
     ~8,800-symbol universe. Stage 3 (replay) ≈ 55 minutes for a 240-symbol sample, cache-hit
     or not (CPU-bound, not network-bound) — budget for this every time it reruns.

5. **Real 6-day, 240-symbol result** (Aug 3–7 + Aug 14, 2026; 40 movers per tier + 80 control):

   | Threshold | Movers | Seen | Seen before cross | **Actionable before cross** |
   |---|---|---|---|---|
   | +5% | 160 | 95.0% | 52.5% | **18.8%** |
   | +10% | 120 | 95.0% | 69.2% | **26.7%** |
   | +20% | 80 | 93.8% | 77.5% | **46.2%** |
   | +50% | 40 | 92.5% | 90.0% | **70.0%** |

   Precision (n=304 actionable findings, mover-enriched sample so not directly comparable to
   the live baseline): useful 29.3%, false/fade 28.0%, ambiguous 42.8%.

   **Key finding**: Scout is not blind at any magnitude (flat 92–95% raw recall). The real
   gap is confirming *before the move happens*, and it's worse for smaller/faster moves —
   not because Scout is smarter about big moves, but because bigger moves simply take longer
   to finish, giving the same fixed gates more real time to converge. Scout has no adaptive
   "this looks promising" signal — it applies the same uniform bar regardless of a candidate's
   eventual size.

6. **Root cause, traced to specific gates** (105 real "seen but not actionable before
   +5%/+10% cross" events; full trace data at
   `data/optimization/backtest/findings-sample-traced.jsonl`):
   - `next_blocker` distribution: `relative_activity` 36.2%, `quality_clean` 25.7%,
     `participation` 21.9%, `fresh_impulse` 14.3%, `full_warmup` 1.9%.
   - Confirmed `relative_activity` was **never the sole blocker** — always co-blocked
     alongside `participation`/`quality_clean`/`quality_actionable`/`first_leg_release`
     (all four in all 38 cases where it was next_blocker).
   - **Read the actual gating code** (`app/market.py` ~1320–1400, ~1666–1760) to find the
     real mechanism, not just the trace labels:
     - `regular_participation` gate uses `MIN_30S_DOLLAR_VOLUME=1000` / `MIN_30S_TRADES=3`
       (fairly loose already).
     - The **quality-layer** "LOW PARTICIPATION" check is much stricter and acts as a **hard
       override** on `quality_label`: `illiquid = trades30 < QUALITY_MIN_TRADES_30S(=12) or
       dollar30 < QUALITY_MIN_DOLLAR_30S(=5000)`, and if `illiquid and not impulse_quality`,
       `quality_label` is forced to `"ILLIQUID"` **regardless of quality_score** — this
       directly blocks `quality_clean`/`quality_actionable`/`first_leg_release` (all
       downstream). This is the real mechanism behind why "LOW PARTICIPATION" was the single
       most common rejection reason (100/105 events).
     - There's an existing escape hatch, `impulse_quality`, but it's stricter than you'd
       expect: `trades15 >= QUALITY_IMPULSE_MIN_TRADES_15S(=10) and dollar15 >=
       QUALITY_IMPULSE_MIN_DOLLAR_15S(=5000) and (change5>=SURGE_MIN_CHANGE_5S_PCT(=0.70%) or
       change15>=EARLY_MIN_CHANGE_15S_PCT(=0.35%))`.
     - Second most common rejection reason, "SPARSE PRINTS" (86/105), driven by
       `active_bucket_ratio < QUALITY_MIN_ACTIVE_RATIO(=0.625)` — **not yet touched or
       investigated as a lever**, flagged for v2 if v1 isn't enough.

7. **Tested and DISPROVED**: lowering `WAKEUP_VOL_RATIO` alone (4.0→2.5) — full 240-symbol
   re-validation showed **zero measurable change** in any recall/precision number. This is
   why a single-knob change is not the answer; confirmed empirically, not just theorized.
   Findings file: `data/optimization/backtest/findings-sample-experiment.jsonl`, report:
   `data/optimization/backtest/report-experiment-vol2.5.json`.

8. **Shipped (uncommitted, build-verified, zero detection risk)**: `PromotionProgress`
   component in `web/app/page.tsx` (added after `FindingRow`'s `rejection_reasons` line) +
   type addition in `web/lib/types.ts` (`candidate_profile.promotion_trace`). Shows
   "N/M gates cleared · next: X" on non-actionable Radar rows, with a ⚡ highlight when one
   gate away. Verified with `bun run build` — compiles clean. This is the one change from the
   session that would have any live effect if pushed, and it's UI-only / zero detection risk.

9. **Found but not yet acted on**: `hybrid_score` (`app/market.py:576`, fused confidence
   score from multi-engine agreement) and `precursor_finding_id` (detection lineage) are
   both real, computed, typed in `lib/types.ts`, but rendered nowhere in the frontend.
   Lower priority than the gate work — offered to wire up, not yet done.

## In-flight when interrupted (will need to be restarted after reboot)

- ~~Background task scanning ground truth for Aug 10–14~~ **DONE** — completed just before
  the restart. `data/optimization/backtest/movers-wk2.jsonl` now holds 4,044 mover rows +
  215 control rows for Aug 10–14. Combined with `movers-wk1.jsonl` (Aug 3–7) and
  `movers-day-2026-08-14.jsonl`, there is now a large, mostly-unused pool to draw a genuine
  holdout sample from later (only the specific 240 symbols in `movers-sample.jsonl` have been
  used for iteration so far — draw the holdout from tickers NOT in that file).

- **Coordinated multi-gate v1 candidate — DONE, real mixed result, see Milestone 004.**
  Small (6/240 tickers, zero regressions) but genuine recall gain; broader precision cost
  (useful rate -2.4pp, false/fade +3.4pp, worse proportionally on the control sample than on
  real movers). A real trade-off, not a clean win -- report to the user as such, let them
  judge whether it's worth it. Full detail: `MILESTONES/2026-08-18-004-coordinated-v1-result.md`.
  **v2 done (Milestone 005): zero per-ticker differences from v1 -- `WAKEUP_VOL_RATIO`
  contributed nothing, the entire effect comes from the 4 `QUALITY_*` changes alone. v2
  supersedes v1 (same result, smaller blast radius).** Gate-tuning thread has reached a
  clean, well-understood stopping point: a real, modest, documented trade-off, not a clean
  win, awaiting the user's judgment call on whether to adopt it. Moving on to the queued
  reversal-detection and halt-candidate replays next.

- **[SUPERSEDED, kept for history] Coordinated multi-gate v1 candidate — designed, not yet run.** This was about to launch
  when the user interrupted to restart. The design, straight from reading the actual gating
  code above (not a guess):
  ```
  QUALITY_MIN_TRADES_30S=8        (was 12)
  QUALITY_MIN_DOLLAR_30S=3000     (was 5000)
  QUALITY_IMPULSE_MIN_TRADES_15S=7   (was 10)
  QUALITY_IMPULSE_MIN_DOLLAR_15S=3000 (was 5000)
  WAKEUP_VOL_RATIO=3.0            (was 4.0 — now paired with the above, unlike the failed
                                    solo test, since the diagnosis showed these gates fail
                                    together, not independently)
  ```
  Rerun command (same shape as the disproved vol-only experiment, set these env vars as
  Process-scope before invoking):
  ```powershell
  python -m scripts.historical_backtest --movers data/optimization/backtest/movers-sample.jsonl `
    --output data/optimization/backtest/findings-sample-coordinated-v1.jsonl `
    --cache-dir data/replay-datasets/backtest --replay-root data/replays/backtest-coord-v1
  ```
  Then score with `scripts.backtest_scorer` against the same `movers-sample.jsonl` and compare
  to the baseline in `data/optimization/backtest/report-6day-sample.json`. Budget ~55 min.

## Full day plan (still valid, resume where it left off)
1. ~~Widen ground truth (week 2)~~ — in flight, restart it.
2. ~~Design coordinated v1~~ — done, see above.
3. **Run v1, score it.** ← resume here.
4. Iterate (v2, v3...) if v1 doesn't fully close the gap — expect this, v1 rarely nails it.
   Next lever if needed: `QUALITY_MIN_ACTIVE_RATIO` (currently 0.625, drives "SPARSE PRINTS").
5. Once a candidate genuinely improves recall without hurting precision on the 240-symbol
   set, re-validate it on a **fresh holdout sample** drawn from the widened ground truth
   (week 2 + original 6 days combined), not reused from iteration — this is the real test.
6. Stretch goal if time allows: investigate whether an early adaptive magnitude-potential
   signal is feasible (catalyst score, gap%, RVOL trajectory) — Scout currently has none;
   its apparent skill on big moves is a timing artifact, not foresight (established and
   confirmed with the user).
7. Write a complete end-of-day report for the user's return. Do not commit/push/deploy
   anything without their review first.

## Expanded goal scope (action items from the user, 2026-08-18, post-restart-notice)

Added directly by the user as part of "catch all meaningful bullish moves early." Checked
the codebase before writing this down — two of these already have partial detector support,
one is genuinely new. Do not assume greenfield on the two that aren't.

1. **Candlestick pattern formation recognition — genuinely new, confirmed nothing exists.**
   Grepped `app/` and `rust/` for hammer/engulfing/doji/morning-star/shooting-star/etc. —
   zero hits. This is a new detection dimension to design from scratch: recognizing standard
   bullish reversal/continuation candlestick formations (hammer, bullish engulfing, morning
   star, three white soldiers, etc.) as part of the evidence Scout considers, with measured
   certainty/accuracy — meaning it must go through the same backtest validation discipline as
   everything else here, not be shipped on theory.

2. **Pre-halt detection/notification for uptrending stocks — extend existing `HALT_PRESSURE`
   stage, don't rebuild.** Scout already has a `HALT_PRESSURE` stage and real `HALT`/`RESUME`
   event handling (see `app/market.py` ~2044–2138, and gate weighting at line ~531/947-949).
   The ask is specifically: **notify before the halt actually happens**, for stocks halting
   during an uptrend (not just react to the halt after it's already occurred). Natural next
   step: extend the historical backtest methodology — build a detector-blind ground truth of
   real historical trading halts (Alpaca/exchange halt data) during uptrends, then measure
   Scout's `HALT_PRESSURE` lead time the same rigorous way the mover recall was measured
   (did it fire before the halt, how many seconds/minutes of lead time, false-positive rate).

3. **Bearish-to-bullish trend reversal detection — extend existing `REVERSAL_WATCH` /
   `EMA_RECLAIM` / `VWAP_RECLAIM` stages, don't rebuild.** These stages already exist and have
   real logic (README: "REVERSAL_WATCH — silent tracking after a material intraday selloff
   forms a local low"). The user explicitly flagged this as something they care about
   specifically ("we care about this one where there's action/movement happening") — so it
   should get the same rigorous ground-truth-and-replay validation treatment as the mover
   recall work, not be assumed to already work well just because the stage exists. Ground
   truth here = real historical bearish-selloff-then-reversal episodes, independent of
   Scout's own reversal-detection logic, then measure recall/lead-time/precision the same way.

4. **Standing testing discipline: test as many times as necessary to actually meet the goal.**
   Not a one-off item — a directive for how all of this work proceeds. Don't stop at one
   validation pass and call it done; iterate (as already being done with the coordinated
   gate work: v1, v2, v3 as needed) until there's real, validated confidence, not just a
   plausible-looking first attempt. Applies to all three items above as they get built too.

All four extend the same core discipline already established: measure with real historical
data through Scout's actual code, don't ship on theory, log validated progress in
`MILESTONES/`, never commit/push/deploy without the user's review.

## Pre-halt detection — first investigation (proxy method, inconclusive, needs a purpose-built sample)

Built `scripts/halt_gap_finder.py`: infers suspected halts from cached tick data (>=300s
regular-session trade-print gap after >=8% intraday gain, requiring real liquidity
immediately before the gap so illiquid lulls aren't mistaken for halts). Confirmed Scout
tracks real halt transitions live (`app/market.py` ~1014-1098, `market_status_events` table)
separately from the predictive `HALT_PRESSURE` stage — but the live `/api/market/halts`
endpoint only exposes the most recent 100 events, not a real historical range, so the proxy
approach was used instead against already-cached data (no new API calls).

Result on the existing 240-symbol sample: 182 plausible suspected-halt events found (spot-
checked, look real — multi-minute gaps after real gains, often reopening at a notably
different price), but `HALT_PRESSURE` fired **exactly once** across the entire 240-symbol
sample. **0/182 warned is not a real finding about Scout's halt-prediction quality — it just
means this sample (built for magnitude-tier testing) doesn't contain enough HALT_PRESSURE
occurrences to test the hypothesis at all.** Do not report "0% warn rate" as if it means
Scout can't predict halts; the honest state is "insufficient sample, inconclusive."

**Next step for this item**: build a purpose-sampled dataset specifically enriched for
candidates likely to halt (very large intraday gains, matching `historical_mover_finder.py`'s
existing +50%+ tier, which is exactly where halts cluster) rather than reusing the general
stratified sample. Report at `data/optimization/backtest/halt-precursor-report.json`.

**Done, staged, not yet replayed**: combined all 8 scanned trading days (wk1 + wk2 + Aug 14)
into `data/optimization/backtest/movers-all-8day.jsonl` (10,020 rows). Filtered to the +50%
tier, excluding the 240 symbols already used for gate-tuning iteration, giving **203 fresh
+50% movers** at `data/optimization/backtest/movers-halt-candidates.jsonl` — this pool serves
double duty: it's the enriched sample for a real halt-precursor test, AND (being disjoint
from `movers-sample.jsonl`) a candidate holdout set for validating whatever gate change wins.
Did not launch its replay yet — avoided running two CPU-heavy ~55min replays concurrently
with the coordinated-v1 validation. Run after v1 finishes:
```powershell
python -m scripts.historical_backtest --movers data/optimization/backtest/movers-halt-candidates.jsonl `
  --output data/optimization/backtest/findings-halt-candidates.jsonl `
  --cache-dir data/replay-datasets/backtest --replay-root data/replays/backtest-halt
python -m scripts.halt_gap_finder --findings data/optimization/backtest/findings-halt-candidates.jsonl `
  --output data/optimization/backtest/halt-precursor-report-v2.json
```

## Candlestick pattern recognition — built, unit-tested, real-data validated

New module `app/candlestick.py`: 9 standard bullish patterns (Hammer, Inverted Hammer,
Dragonfly Doji, Bullish Engulfing, Bullish Harami, Piercing Line, Tweezer Bottom, Morning
Star, Three White Soldiers), pure/testable functions operating on OHLC `Candle` data
compatible with Scout's existing `Bucket` shape. Built as **shadow/observational only**,
matching Scout's own established convention for new unvalidated signals (see the "V6.3
shadow recipe" comment in `app/market.py`) — does not touch live detection, gates, or
notifications. 22 unit tests in `tests/test_candlestick.py`, all passing (caught and fixed a
real bug: the hammer wick-ratio check was body-relative, which breaks down for near-doji
small-body hammers — fixed to range-relative). Full project test suite still green (123/123).

**Real-data validation** (`scripts/candlestick_backtest.py`, scanned 295 cached datasets from
today's backtest work, 1-min resampled candles, 15-min forward return): result is genuinely
mixed, not a clean win — report faithfully, don't oversell:

| Pattern | n | avg 15m return | positive rate |
|---|---|---|---|
| THREE_WHITE_SOLDIERS | 149 | +0.68% | 45.0% |
| **INVERTED_HAMMER** | **3,793** | **+0.43%** | **55.6%** |
| BULLISH_HARAMI | 2,062 | +0.08% | 45.9% |
| TWEEZER_BOTTOM | 5,495 | -0.06% | 45.1% |
| DRAGONFLY_DOJI | 4,265 | -0.10% | 40.9% |
| HAMMER | 3,638 | -0.26% | 44.0% |
| BULLISH_ENGULFING | 2,423 | -0.37% | 38.6% |
| MORNING_STAR | 311 | -0.57% | 34.4% |
| PIERCING_LINE | 92 | -0.65% | 42.4% |

**Honest read**: `INVERTED_HAMMER` is the one pattern with both a large sample and a genuine
edge (positive average return AND >50% hit rate) — worth investigating as real additional
evidence. Most of the classically "famous" bullish reversal patterns (Bullish Engulfing,
Hammer, Morning Star, Piercing Line) show negative average forward returns and sub-45% hit
rates on this specific universe (low-priced, volatile intraday stocks) and timeframe (1-min
candles) — the opposite of their textbook reputation. This does not mean the patterns are
coded wrong (unit tests confirm correct shape-matching against hand-built examples) — it
means textbook pattern theory, largely developed for daily bars in calmer markets, may not
transfer to this instrument class without more context (e.g. requiring volume/participation
confirmation alongside the shape, which is exactly Scout's existing philosophy for every
other signal). **Do not wire any of this into live detection yet** — it needs the same
gate-style validation discipline as everything else before it could be trusted as evidence.
Report: `data/optimization/backtest/candlestick-report.json`.

## Infrastructure lesson: always split large replays into concurrent batches

A sequential single-process run of the coordinated v1 replay (240 symbols, one process) got
stuck at ~4/240 after 20 minutes of continuous near-100% CPU — projected to take ~20 hours.
Diagnosed properly before assuming a code regression: the replay itself was fast (96s for a
1.5M-trade symbol, 16k events/sec) -- the real cost is `app/replay.py`'s `load_events()`
parsing large cached NDJSON files line-by-line in pure Python, which is slow for
million-trade symbols and was never included in the per-symbol benchmark numbers shown
during the day's earlier work (they only timed post-load replay). The original baseline run
avoided ever noticing this because it was split into 2 concurrent batches (87 + 153 symbols),
which hides the load cost via parallelism. **Always split any full-sample replay into at
least 2 concurrent background batches using the existing
`movers-sample-part-aa`/`movers-sample-part-ab` split (or an equivalent split for a new
sample file) — never run a 200+ symbol replay as one sequential process.**

## Notification cross-platform sync — one real fix made, one bigger question still open

**Fixed, tested, real (not committed)**: `app/notifiers.py` + `app/dispatch.py`. Every
notification eligibility check hardcoded the literal platform string `"android"`, including
for Web Push (`send_web_push_all`), even though Web Push is subscribed to identically from
any platform's browser (the `web_push_subscriptions` table already stores `user_agent` per
subscriber -- always did, just was never used for this). Before the fix, ALL webpush
subscribers were gated by one shared `platforms.android.enabled` toggle regardless of their
actual device; a desktop subscriber's own platform preference was never consulted. Fixed by
splitting eligibility into platform-agnostic shared gates (`_allowed_platform_agnostic`) plus
a per-subscriber platform check (`_platform_allowed` + new `infer_platform(user_agent)`),
applied individually inside `send_web_push_all`'s per-subscription loop. ntfy is left
intentionally unchanged -- gating it by "android" specifically is correct by product design
(see below). 9 new tests in `tests/test_notification_platform.py`, all passing; full suite
132/132 green.

**Important nuance discovered while verifying this (don't overclaim the fix above)**: checked
the actual Settings UI before assuming impact. The "Windows native toast" row is a *different*
mechanism (the Tauri desktop app's local OS toast, driven client-side by `web/lib/native.ts`
when the app is open and receiving the live SSE stream) and is **deliberately frozen off**
since v6.5.3, with an explicit stated policy right in the UI: *"Primary alert channel: Scout →
ntfy. Desktop OS toasts are suppressed by default to avoid duplicate alerts."* So by current
product design, **ntfy is the intended shared channel across mobile AND desktop**, not Web
Push -- the webpush fix above is a real correctness improvement (and matters if Web Push
usage grows, or if the frozen Windows-toast feature is ever re-enabled) but is not the
dominant lever for actual day-to-day cross-platform sync today.

**Not yet investigated, and likely the higher-value next step for "sync accuracy" specifically**:
does ntfy itself actually reach every platform reliably and consistently? Open questions:
is the ntfy topic shared identically across web/desktop/mobile subscribers, does the desktop
Tauri app actually subscribe to it, and are there any silent per-platform delivery gaps in
the ntfy path itself (rate limiting, topic misconfiguration, etc.)? `E2E-VALIDATION.md`
already flags that Windows toast/sound "requires manual confirmation inside the installed
Scout client" -- meaning delivery reliability there has never been verified by automation,
only by a human checking. This is the real next piece of the notification-sync mandate.

## Bearish-to-bullish reversal detection — ground truth built and validated, replay not yet run

New `scripts/reversal_ground_truth.py`: detector-blind ground truth for real intraday
reversal episodes (peak → >=5% drawdown → >=0.75%/2.0% bounce off the low), using the exact
same math Scout's own `REVERSAL_WATCH`/`RECLAIM` logic uses (`app/market.py` ~1464-1472,
`settings.reversal_*`) so the ground truth is a fair, apples-to-apples yardstick. Smoke-tested
on 300 symbols/1 day: 228 real episodes found, 133 reached the confirmed-reclaim bar; spot-
checked and internally consistent (low always after peak, thresholds respected).

**DONE (Milestone 006): real, honest, weaker-than-upward-moves result.** Watch bar (n=215):
64.7% seen, 10.2% actionable-before-cross. Reclaim bar (n=140): 9.3% actionable-before-cross.
Notably weaker than the upward-move numbers (92-95% seen, 18.8-70.0% actionable-before-cross).
Verified real via spot-check (FUFU had 48 general findings that day, reversal-specific stages
just never fired for the real reversal event -- not blindness, a specific gap). Root-cause
trace not yet done (next step, same discipline as the gate-tuning diagnosis).

~~In progress~~ full 6-day ground truth scanned (`reversals-wk1.jsonl` + `reversals-day-
2026-08-14.jsonl`, ~23,900 total episodes, ~14,700 reaching the reclaim bar). Sampled 215
unique ticker/days (`scripts/sample_reversals.py`, reclaim-cap 150 + watch-only-cap 90) into
`reversals-sample.jsonl`, split into 2 concurrent batches, replaying now through
`scripts.historical_backtest` at **baseline settings** (measuring current production
behavior, not a gate experiment) via tasks `bwhun02lx`/`b2kelmw9a`. New scorer built:
`scripts/reversal_scorer.py`, scores against `watch_crossed_at`/`reclaim_crossed_at` and
Scout's `REVERSAL_WATCH`/`RECLAIM`/`EMA_RECLAIM`/`VWAP_RECLAIM`/`REARM`/`FIRST_PULLBACK`
stages. Score once both batches land:
```powershell
cat data/optimization/backtest/findings-reversal-a.jsonl data/optimization/backtest/findings-reversal-b.jsonl > data/optimization/backtest/findings-reversal.jsonl
python -m scripts.reversal_scorer --reversals data/optimization/backtest/reversals-sample.jsonl --findings data/optimization/backtest/findings-reversal.jsonl --output data/optimization/backtest/reversal-report.json
```

## MAJOR CORRECTION (found late, after user shared v6.4.13 architecture docs): today's
## backtest was Python-only, not the true hybrid system — see Milestones 009-011

The entire backtest pipeline (`historical_backtest.py` -> `app.replay.run_dataset`) never
invokes Rust -- confirmed by grep, zero "rust"/"hybrid" references in `app/replay.py`. Rust
only wires in via `app/main.py`'s live production startup. Built `scripts/hybrid_replay.py`
to fix this: merges Rust's pre-computed candidates (`data/replays/rust-batch/`, built via
`rust/market-replay/target/release/scout-market-replay.exe`, ~3min for 501 datasets) into
the trade timeline and feeds them through the real `MarketWatcher.handle_rust_candidate`
call path, exactly matching production.

**Result: raw awareness improves substantially with Rust in the loop (seen-before-cross
52.5-90.0% -> 73.1-92.5%), but actionable-before-cross (the metric that matters) is
BYTE-IDENTICAL to Python-only (18.8/26.7/46.2/70.0%, unchanged) -- proof, not theory, that
the shared Python quality gate is the sole bottleneck regardless of which engine finds the
candidate.** Also tested and DISPROVED my own hypothesis that the v2 gate fix would help
more under hybrid (identical 2/1/2/1 ticker flips either way; precision actually worse under
hybrid: 19.6%->17.1% vs Python-only's 29.3%->26.9%).

All daily-summary numbers updated to reflect this. `data/optimization/backtest/report-hybrid.json`
and `report-hybrid-v2.json` hold the corrected data.

## Key file locations
- Ground truth / samples / findings / reports: `data/optimization/backtest/`
- Cached raw tick data (reusable, no re-download needed): `data/replay-datasets/backtest/`
- Replay run reports: `data/replays/backtest*/`
- Pipeline scripts: `scripts/historical_mover_finder.py`, `sample_movers.py`,
  `historical_backtest.py`, `backtest_scorer.py`
- Full methodology writeup: `HISTORICAL-BACKTEST.md`
- This file: `SESSION-STATE.md` (delete or update once the day's work is reconciled with the
  user, don't leave it stale)

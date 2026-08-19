# Milestone 007 — Reversal detection root cause: same systemic pattern as upward-move lateness

Date: 2026-08-18

## Achieved
Traced *why* reversal detection under-fires (Milestone 006's 10.2%/9.3% actionable-before-
cross result), using real replay data already captured rather than guessing.

Spot-checked `FUFU` 2026-08-14 (5.8% drawdown, real reversal): at the exact moment of the
ground-truth low (`low_at=1786714380`) and watch-bounce crossing (7 seconds later), Scout
emitted `ACTIVITY_WATCH`, not `REVERSAL_WATCH`. Read the actual gating code
(`app/market.py` ~1836-1906) to find out why: `REVERSAL_WATCH` requires a **third, separate**
participation bar (`reversal_participation`: `vol15/vol30 >= REVERSAL_MIN_VOL_RATIO(3.0)`,
`dollar30 >= REVERSAL_MIN_DOLLAR_30S(5000)`, `trades30 >= REVERSAL_MIN_TRADES_30S(12)`) on
top of the drawdown/bounce math -- distinct from both the general `regular_participation`
gate (loose: $1000/3 trades) and the quality-layer "LOW PARTICIPATION" bar diagnosed earlier
for upward moves. **`REVERSAL_MIN_DOLLAR_30S`/`REVERSAL_MIN_TRADES_30S` are numerically
identical to `QUALITY_MIN_DOLLAR_30S`/`QUALITY_MIN_TRADES_30S` (5000/12)** -- the same strict
bar, duplicated as a separate independent gate for the reversal path.

When `reversal_watch_qualifies` is False but the looser `activity_watch` condition is True,
the code falls through to generic `ACTIVITY_WATCH` (app/market.py:1906) -- exactly what
happened to FUFU, and very plausibly the majority of the 76/215 "missed entirely" reversal
episodes from Milestone 006.

## Honest scope of this finding
This is a well-grounded qualitative diagnosis (real code + a real matching observed case),
not a full quantitative trace like the upward-move one (which counted `next_blocker` across
all 105 events using captured `promotion_trace` data). Reversal-family findings don't carry
the same `promotion_trace` structure, and `historical_backtest.py`'s current field capture
doesn't include `reversal_participation`'s inputs (`vol15`, `vol30`, `dollar30`, `trades30`
at the moment of each attempt). A full quantitative version would need those fields added to
the replay capture and a fresh replay -- not done today, flagged as the natural next step for
whoever picks up gate-tuning for the reversal path.

## Why this matters beyond just reversals
This is the second time today the same failure pattern turned up: a strict 30s participation
bar, independently duplicated across multiple unrelated gates (quality-layer for upward
moves, `reversal_participation` for reversals), each blocking real, wanted signals on
low-liquidity penny stocks in their first ~30 seconds of real activity. That repetition
itself is useful evidence -- it suggests the underlying architectural issue (many
independently-thresholded participation checks that must all clear together) is systemic
across Scout's detector, not specific to one stage. Worth keeping in mind for any future
gate-tuning work on ANY stage, not just the two investigated today.

## Not yet done
- Quantitative trace (needs richer field capture + a fresh replay).
- A coordinated fix candidate for `reversal_participation` specifically, tested the same
  before/after way as the upward-move gate work.

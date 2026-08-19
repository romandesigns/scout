# Reentry safety gate blind spot found and fixed: VWAP-distance check for REARM/RECLAIM

**Date:** 2026-08-19
**Status:** Implemented and unit-tested locally. Retroactively validated against today's
real live findings. **NOT deployed** — `EXPERIMENT_REENTRY_VWAP_SAFETY_GATE` defaults off;
standing instruction requires explicit confirmation before commit/push/deploy.

## What the user found

A live dashboard screenshot of BIVI showing `C - DEVELOPING` (later screenshots showed
`STRONG MOMENTUM`, `12/12 gates cleared`, `FRESH ENTRY`, `RECLAIM RELEASE · 2m ago`) at
$1.49, next to a wide-view chart showing BIVI had run from ~$1.10 to a peak of **$1.96**
earlier in the day and had been fading for hours since — MACD solidly red, price still
**-10.8% below VWAP** ($1.49 vs $1.67), well below the longer EMA9/EMA21. A human glancing
at that chart would never call this a fresh entry.

## Diagnosis

Pulled the actual finding record: at 21:53:06 UTC it briefly flipped to
`EMA_RECLAIM, rank=B, quality=CLEAN` — then reverted to `PRE_IGNITION, rank=C,
quality=DEVELOPING` two seconds later, at 21:53:08. That two-second window is what produced
the badges. Live diagnostics moments after showed it back to `ILLIQUID`, rejection reasons
`LOW PARTICIPATION`/`CHOPPY PATH`/`BULLISH STRUCTURE UNCONFIRMED`, 1 trade in the last 30s.

Traced the code path: `evaluate_reentry_safety()` in `app/market.py` is an existing
"damage-control gate for REARM/RECLAIM alerts" — its own docstring says it exists because a
prior production audit "exposed severe adverse excursions in REARM/VWAP/EMA reclaim." It
checks two things: `is_late_promotion_risk()` (extension from the *local* base) and
immediate 5s continuation. Confirmed via live diagnostics that neither caught BIVI:
`base_extension_pct: -0.21`, `extension: 0.11` — both read as "not extended" because BIVI
had spent hours fading into a *new, tight local base* near its depressed price. The gate
has no separate check against the session's VWAP, so a ticker can be deeply below its own
session-long volume-weighted average and still pass as "not late risk," as long as its most
recent few minutes look locally unremarkable.

**Root cause: `is_late_promotion_risk` measures the wrong reference frame.** Local extension
resets every time a new consolidation range forms, including consolidation ranges that only
exist because a stock has been declining for hours. VWAP does not reset — it is exactly the
session-relative reference this gate was missing.

## Fix (revised after retroactive validation — see below)

[app/market.py](../app/market.py) `evaluate_reentry_safety()`: added two independent
blockers, both using the already-computed `vwap_gap_pct` metric, scoped to the same
REARM/VWAP_RECLAIM/EMA_RECLAIM stages the existing gate covers:
- `deeply_below_vwap` — the original BIVI-motivated case, price meaningfully below VWAP
  (default tolerance `REENTRY_MAX_BELOW_VWAP_PCT` = 2.0%)
- `chasing_above_vwap` — added after retroactive validation showed this was the actual
  dominant real-world loss pattern, price already extended well above VWAP (default
  `REENTRY_MAX_ABOVE_VWAP_PCT` = 3.0%)

Both gated behind the same `EXPERIMENT_REENTRY_VWAP_SAFETY_GATE` (default `False`) in
[app/config.py](../app/config.py).

`tests/test_reentry_vwap_safety.py` (8 tests): flag-off default behavior reproduces both
the BIVI (below-VWAP) and CDTG (above-VWAP) bugs as documented current behavior; each
blocker fires correctly when enabled on its real reconstructed case; genuine near-VWAP
reclaims in both directions (BIVI-style within tolerance, and the real OSRH case at +2.38%
above VWAP with a +7.89% real outcome) are confirmed NOT penalized; blockers stay scoped to
reentry stages only; threshold boundaries respected. Full suite: 144/144 passing
(136 + 8 new).

## Retroactive validation against a real regular-hours session

Couldn't re-run the already-closed session with the flag enabled, so instead: re-fetched
full records (vwap, stage) for all 321 actionable findings already scored in
`live-full-day-report.json` (`scripts/validate_vwap_safety_gate.py`), identified which ones
the new gate would have blocked, and compared the aggregate net_opportunity_pct with and
without them.

**First pass (below-VWAP only) was a genuine miss**: zero of that day's 25 reentry-stage
findings were meaningfully below VWAP — BIVI's own flicker happened after regular hours, so
it wasn't even in this dataset. The below-VWAP mechanism is still real (it's a live case,
directly observed), it just wasn't what was hurting that particular session.

**Full distribution of all 25 reentry-stage findings that day** showed the actual pattern:
almost all were *above* VWAP, and the worst outcomes were concentrated at the largest
positive gaps — most dramatically **CDTG, which alone produced two findings at +20-22%
above VWAP with -7.4% and -8.0% real outcomes**, together accounting for the majority of
the entire session's actionable-cohort loss.

**With both blockers active**, 10 of 321 actionable findings that day (3.1%) would have
been blocked — all 10 for `chasing_above_vwap`:

| | n | mean | sum | positive_rate |
|---|---|---|---|---|
| Before (all findings that day) | 321 | -0.081 | **-26.0** | 38.6% |
| After (excluding the 10 blocked) | 311 | +0.011 | **+3.4** | 39.5% |

Removing 3.1% of findings flips the entire session's aggregate outcome from net negative to
net positive. This is the single highest-leverage result validated this week — a small,
targeted, well-evidenced blocker fully accounts for that session's precision deficit.

**Honest caveats**: n=25 for the reentry cohort, and n=10 for the blocked set, is a small
sample dominated by one ticker (CDTG's two findings alone are -15.4 of the -26.0 total).
This is real, specific, and worth fixing regardless, but a single session is not proof the
+3.0% above-VWAP threshold is optimally calibrated — it should be re-validated against
additional sessions before being trusted as a permanent default. `CONL` (+12.05% above VWAP,
still a `+0.303` positive real outcome) is a live counter-example showing the relationship
isn't perfectly clean, just a real, exploitable, net-positive trade-off.

## Scope note

This fix addresses the *reclaim/rearm mislabeling* mechanism specifically — it does not
address the other findings from today (Rust bridge queue stall, SIP websocket flapping,
universe-refresh cadence gaps). Those remain open, tracked in
[2026-08-19-006](2026-08-19-006-rust-bridge-queue-deadlock-incident.md) and the SIP
instability note in [2026-08-19-007](2026-08-19-007-reconcile-connectionclosed-log-noise-fixed.md).

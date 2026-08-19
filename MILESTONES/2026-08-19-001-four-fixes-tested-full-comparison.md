# Milestone 2026-08-19-001 — All 4 proposed fixes tested against the true hybrid baseline

Date: 2026-08-19

## What was done
Built all four ideas proposed yesterday as real code, each behind an independent feature
flag defaulting OFF (`app/config.py`: `EXPERIMENT_ADAPTIVE_PARTICIPATION_BAR`,
`EXPERIMENT_TIME_DECAY_PARTICIPATION_BAR`, `EXPERIMENT_UNIFIED_PARTICIPATION_GATE`,
`EXPERIMENT_RUST_FAST_CONFIRM`). Production behavior is unchanged unless a flag is
explicitly set -- confirmed via full test suite (132/132 passing with flags unset). Each was
tested in isolation through the true hybrid replay (`scripts/hybrid_replay.py`, both engines,
same 240-symbol ground truth as every prior test) and scored against the same true-hybrid
baseline (`report-hybrid.json`, Milestone 010).

A real bug was caught and fixed before any test ran: the time-decay implementation initially
referenced `relative_activity`/`fast_single_bucket`, variables that belong to a different
method (`_maybe_emit`) and are not in scope inside `_metrics` where the edit lives. Caught by
scope-checking before running, not after getting a wrong answer.

## Full comparison

| | Baseline (hybrid) | #1 Adaptive | #2 Time-decay | #3 Unified gate | #4 Rust fast-confirm |
|---|---|---|---|---|---|
| +5% actionable-before-cross | 18.8% | 20.0% | 18.8% | **26.9%** | 18.8% |
| +10% | 26.7% | 27.5% | 26.7% | **38.3%** | 26.7% |
| +20% | 46.2% | 48.8% | 47.5% | **57.5%** | 46.2% |
| +50% | 70.0% | 72.5% | 72.5% | **77.5%** | 70.0% |
| Actionable findings (n) | 489 | 550 | 497 | **760** | 567 |
| Useful rate | 19.6% | 17.3% | 19.1% | **13.6%** | 17.8% |
| False/fade rate | 20.4% | 20.9% | 20.7% | 20.1% | 19.2% |

## Honest result for each

**#1 Adaptive bar — no better than the simple version.** Produces the *exact same* 6 ticker
flips as yesterday's blunt static-threshold fix (verified: identical (ticker, date) pairs at
every tier). The "smarter, corroboration-gated" design added real code complexity for zero
measurable benefit over just lowering the number. Likely because real movers near this
boundary already show the corroborating signals chosen (price acceleration, EMA structure,
above-VWAP), so the adaptive formula converges to nearly the same population as a blunt cut.

**#2 Time-decay — doesn't help where it was designed to help.** +5%/+10% (the tiers this was
specifically built for) are completely unchanged from baseline. Only +20%/+50% show modest
gains. Root cause, in hindsight: the whole reason modest moves are hard to catch is they
often finish *before* a 60-second decay window has elapsed -- there's no time left to decay
through. Only helps tiers that already had enough natural runway anyway.

**#3 Unified gate — by far the largest effect, and by far the largest cost.** +7.5 to +11.6
percentage points across every tier -- 4-10x the size of every other experiment. But n=760
actionable findings (+55% vs baseline) and useful rate drops to 13.6% (worse than the
Python-only live-audit's original ~3.5%-29% range at its low end). This isn't a "smarter"
fix like #1/#2 were intended to be -- it's a genuinely more aggressive relaxation
(eliminating the strict duplicate check entirely rather than partially reducing it), and the
result scales accordingly in both directions.

**#4 Rust fast-confirm — zero recall benefit, some cost.** The idea with the highest
hypothesized upside produced literally no change in any actionable-before-cross number
(byte-identical to baseline on all 4 tiers), while adding 78 more actionable findings (489→
567) at slightly worse precision. The new tier's extra promotions never turn out to be the
*first* actionable finding for any of these movers before their threshold crossed -- either
a normal-path finding already covered it, or the fast-confirm finding still lands late.

## Overall verdict
None of the four is a clean win. Ranked by honest outcome:
1. **#3** has real, large recall impact but at a precision cost large enough to need careful
   scrutiny before ever being considered -- not a quiet tuning change, a substantial shift.
2. **#1** and **#2** are both essentially neutral-to-marginal — real but small, and #1 adds
   complexity for no benefit over the much simpler static change already known.
3. **#4** is a genuine miss relative to its hypothesis -- the highest-theorized-upside idea
   produced the least measurable benefit of the four.

No code change is recommended for adoption from this test alone. #3 is the only one with a
large enough effect to be worth a serious follow-up conversation about whether that much
recall is worth that much added noise -- and that is a product decision, not a technical one.
All four remain in the codebase behind their default-off flags for further experimentation.

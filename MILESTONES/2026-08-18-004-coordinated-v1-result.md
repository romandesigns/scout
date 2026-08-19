# Milestone 004 — Coordinated multi-gate v1: real, small, one-directional recall gain; real, broader precision cost

Date: 2026-08-18

## Achieved
Full 240-symbol replay of the coordinated v1 candidate (`QUALITY_MIN_TRADES_30S=8,
QUALITY_MIN_DOLLAR_30S=3000, QUALITY_IMPULSE_MIN_TRADES_15S=7,
QUALITY_IMPULSE_MIN_DOLLAR_15S=3000, WAKEUP_VOL_RATIO=3.0`), scored against the same ground
truth as the baseline. This is a genuine, measured result -- unlike the single-gate
`WAKEUP_VOL_RATIO`-only test, this one moved real numbers.

### Recall (aggregate)
| Threshold | Baseline actionable-before-cross | V1 | Δ |
|---|---|---|---|
| +5% | 18.8% | 20.0% | +1.2pp |
| +10% | 26.7% | 27.5% | +0.8pp |
| +20% | 46.2% | 48.8% | +2.6pp |
| +50% | 70.0% | 72.5% | +2.5pp |

### Recall (paired, per-ticker -- the more trustworthy view)
Exactly which candidates changed status, same 240 tickers both runs:
- +5%: 2 flipped no→yes, 0 regressed
- +10%: 1 flipped no→yes, 0 regressed
- +20%: 2 flipped no→yes, 0 regressed
- +50%: 1 flipped no→yes, 0 regressed

**Zero regressions across all 240 candidates, all 4 tiers.** Small (6 total flips) but
genuinely one-directional -- if this were noise, some tickers would have flipped the other
way. This is real, if modest, signal.

### Precision cost, split by real movers vs control sample (the important nuance)
| | Baseline | V1 |
|---|---|---|
| Mover actionable findings | n=226, useful 37.6%, bad 31.9% | n=247, useful 34.0%, bad 34.4% |
| Control actionable findings | n=78, useful 5.1%, bad 16.7% | n=84, useful 6.0%, bad 22.6% |

Both mover and control-sample "bad" (false-positive/fade) rates rose, but **proportionally
more on the control side** (16.7%→22.6%, +35% relative) than the mover side (31.9%→34.4%,
+8% relative) -- meaning part of the cost is genuine noise creeping in on non-movers, not
purely a fair trade against real winners. The mover-side useful rate also dropped (37.6%→
34.0%): the newly-unlocked candidates are lower quality on average than what was already
getting through, which is expected (they were previously blocked for being marginal).

## Honest verdict
Real, validated, modest recall improvement (6 tickers out of 240 caught early that weren't
before, zero regressions) bought at a real, broader precision cost (useful rate down ~2.4pp,
false/fade up ~3.4pp across ~330 actionable calls, worse proportionally on the control
sample). The cost touches more decisions than the benefit reaches. This is a genuine
trade-off, not a clean win -- do not present it as one.

## Not yet done (deliberately, given the trade-off above)
No per-parameter attribution -- 5 thresholds moved together, so which one(s) actually drove
the 6 real flips vs which contributed to the broader precision cost is unknown without an
ablation study (expensive: another ~55min replay per parameter tested). A more targeted v2
that keeps the highest-conviction levers and reverts the rest could plausibly capture most
of the recall benefit at lower cost -- worth trying next if pursuing this further, but this
result itself is real, reportable, and should be shown to the user as-is (a genuine trade-off
requiring a judgment call, not something to resolve unilaterally by picking a side).

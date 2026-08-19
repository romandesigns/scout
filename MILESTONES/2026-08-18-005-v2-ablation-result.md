# Milestone 005 — v2 ablation: WAKEUP_VOL_RATIO contributed nothing; v2 supersedes v1

Date: 2026-08-18

## Achieved
Replayed v2 (v1's 4 `QUALITY_*` changes, `WAKEUP_VOL_RATIO` reverted to baseline 4.0) across
the same 240-symbol sample. Result: **zero per-ticker differences from v1** -- every single
candidate's actionable-before-cross outcome, at every threshold tier, is byte-identical
between v1 and v2. Precision is likewise statistically indistinguishable (n=331 both;
useful 26.6% vs 26.9%; false/fade 31.7% vs 31.4%).

**Conclusion, definitive**: `WAKEUP_VOL_RATIO` was inert in the v1 combination -- it rode
along contributing nothing, consistent with the original diagnosis (Milestone --
`next_blocker` trace) that `relative_activity` was never the sole blocker for any candidate
in the affected cohort. The entire v1 effect, both the recall gain and the precision cost,
comes from the 4 `QUALITY_*` parameter changes alone.

## Recommendation
**v2 supersedes v1.** Same measured outcome, one fewer changed parameter, smaller blast
radius, less to reason about or regress later. If either candidate is ever adopted, it
should be v2, not v1.

## The underlying trade-off is unchanged from Milestone 004 and still requires a judgment call
- Recall: 6/240 tickers caught early that weren't before, zero regressions, spread across
  all 4 magnitude tiers.
- Precision: useful rate down ~2.4pp, false/fade up ~3.4pp across ~330 actionable calls,
  worse proportionally on the control (non-mover) sample than on real movers.

Neither this milestone nor Milestone 004 makes a ship/no-ship recommendation -- that's the
user's call, to be made on return with the real numbers in hand, not decided unilaterally.

## Next
Proceed to the queued reversal-detection and halt-candidate replays (not yet started, to
avoid CPU contention with this work) since the gate-tuning thread has reached a clean,
well-understood stopping point for now.

# Milestone 001 — Real backtest pipeline, baseline numbers, root-cause diagnosis

Date: 2026-08-18

## Achieved
- Built and validated a working historical backtest pipeline: detector-blind ground-truth
  mover finder, stratified sampler, real-detector replay (Scout's actual production code
  against real Alpaca history), and a recall/precision scorer. Full detail in
  `HISTORICAL-BACKTEST.md`.
- Established a real, evidence-based baseline (6 trading days, 240-symbol stratified sample):
  recall is flat 92–95% across all move sizes; actionable-before-cross ranges from 18.8%
  (+5%) up to 70.0% (+50%).
- Traced the root cause of lateness on modest moves to specific gate mechanics in
  `app/market.py` (the quality-layer "LOW PARTICIPATION" hard override on `quality_label`,
  with an under-calibrated `impulse_quality` escape hatch) — not a guess, read directly from
  the code and cross-checked against 105 real traced events.
- Tested and **disproved** a single-gate fix (`WAKEUP_VOL_RATIO` 4.0→2.5): zero measurable
  change, because the gate was never the sole blocker. Real negative result, prevents wasted
  effort repeating it.
- Shipped (uncommitted, build-verified, zero detection risk): a "gates cleared" UI indicator
  surfacing existing-but-previously-hidden `promotion_trace` data, so the user can act on
  near-actionable candidates manually.

## Not yet achieved (queued)
- Coordinated multi-gate v1 candidate — designed, not yet run/validated.
- Holdout validation on data not used for iteration.
- The four goal-scope additions in `SESSION-STATE.md` (candlestick pattern recognition,
  pre-halt detection/notification, bearish-to-bullish reversal detection, standing
  test-until-confident discipline).

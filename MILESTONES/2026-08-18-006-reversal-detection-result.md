# Milestone 006 — Reversal detection: real, honest, and notably weaker than upward-move detection

Date: 2026-08-18

## Achieved
Full pipeline built and run for bearish-to-bullish reversal detection, mirroring the mover
recall methodology exactly: `scripts/reversal_ground_truth.py` (detector-blind, using
Scout's own reversal math as the yardstick), `scripts/sample_reversals.py`, and
`scripts/reversal_scorer.py` (new). Ground truth: 6 trading days, ~23,900 real reversal
episodes found (peak → ≥5% drawdown → bounce), ~14,700 reaching the confirmed 2% reclaim
bar. Replayed a 215-episode stratified sample through Scout's real detector at baseline
settings (no gate experiment -- this measures current production behavior as-is).

## Result (real, verified, not spun)
| | n | Scout ever saw it (any reversal-stage finding) | Seen before bounce crossed | **Actionable before bounce crossed** |
|---|---|---|---|---|
| WATCH bar (≥0.75% bounce) | 215 | 64.7% | 50.7% | **10.2%** |
| RECLAIM bar (≥2.0% bounce) | 140 | -- | 50.0% | **9.3%** |

**This is meaningfully weaker than the upward-move numbers** (Milestone from earlier today:
92-95% raw recall, 18.8-70.0% actionable-before-cross depending on magnitude). Reversal
detection recognizes barely 2/3 of real episodes at all, and even when it does, becomes
confidently actionable before the bounce happens only ~1 time in 10.

## Verified this is real, not a measurement artifact
Spot-checked several "completely missed" episodes before trusting the result (same diligence
as the earlier halt-proxy investigation, which caught two real bugs before being trusted).
`FUFU` on 2026-08-14 (5.8% drawdown, reached reclaim) had **48 findings that day**
(ACTIVITY_WATCH, PRE_IGNITION, EARLY) -- Scout was actively watching and detecting general
activity on this exact ticker throughout the session. The reversal-specific stages
(`REVERSAL_WATCH`/`RECLAIM`/`EMA_RECLAIM`/`VWAP_RECLAIM`) simply never fired for the real
reversal event. **This is not "Scout is blind to these stocks" -- it's specifically that the
reversal-family detection logic under-fires relative to real reversal episodes**, distinct
from Scout's general momentum-detection activity which is clearly working on the same names.

## Not yet done (root-cause diagnosis, same discipline as the gate-tuning work)
Have not yet traced *why* the reversal stages under-fire the way the promotion-gate blockers
were traced for upward moves. Plausible candidates worth checking against the actual code
next: `REVERSAL_WATCH` requires `s.reversal_phase == "IDLE"` plus a cooldown
(`quality_watch_cooldown_seconds`) that could suppress re-detection after an earlier false
start on a volatile name; `RECLAIM`/`EMA_RECLAIM`/`VWAP_RECLAIM` likely inherit the same
`quality_actionable` (CLEAN + bullish_confirmed) requirement diagnosed earlier as the
convergent-evidence bottleneck for upward moves -- plausible this is the same root cause
wearing a different stage name, but not confirmed by tracing real `promotion_trace`/gate data
the way the mover-lateness diagnosis was. This is the natural next step, not yet done.

## Report location
`data/optimization/backtest/reversal-report.json`

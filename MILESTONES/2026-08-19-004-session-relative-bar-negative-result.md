# Experiment #5 (session-relative participation bar): negative result under the real objective

**Date:** 2026-08-19
**Status:** Tested, NOT adopted. `EXPERIMENT_SESSION_RELATIVE_PARTICIPATION_BAR` stays default `False`.

## What was tested

The user's own proposal: "Build a cross-sectional participation-percentile feature... test
whether gating on relative abnormality instead of an absolute bar improves the
magnitude-weighted recall/precision trade-off."

Built `scripts/build_participation_baseline.py`, computed real session-specific p50-p95
percentiles of `dollar30`/`trades30` from 501 cached tick datasets (a rolling-30s window
mirroring Scout's own gate math exactly), split by premarket/regular/afterhours. Notable
side-finding: premarket percentiles run *higher* than regular-hours percentiles in this
mover-enriched sample (dollar30 p90: premarket 139,024 vs regular 65,467) — this session's
data disagrees with the naive assumption that premarket is always thinner.

Wired the p60 values (premarket {4128.3, 16}, regular {2517.14, 15}, afterhours {1990.24, 8})
into `app/market.py` as a new `elif` branch in the existing composable participation-bar
experiment block, gated behind `EXPERIMENT_SESSION_RELATIVE_PARTICIPATION_BAR` (default
`False`). Ran the full 240-symbol true-hybrid replay, scored two ways.

## Result

**Flat classification** (recall by threshold, 5%/10%/20%/50% moves):
| | 5% | 10% | 20% | 50% |
|---|---|---|---|---|
| baseline | 18.8% | 26.7% | 46.2% | 70.0% |
| exp5 | 20.0% | 26.7% | 46.2% | 72.5% |

Looked like a small win — modest recall gain, precision roughly flat (n=518 vs 489,
useful_rate 18.3% vs 19.6%, ambiguous_rate 59.7% vs 59.9%).

**Magnitude-weighted** (`net_opportunity_pct = mfe_300s_pct + mae_300s_pct` per finding —
the corrective for flat classification's blindness to trade quality, established
[2026-08-19 earlier this day]):

| report | n | mean | median | **sum** | positive_rate |
|---|---|---|---|---|---|
| baseline (hybrid) | 489 | 0.281 | -0.050 | **137.3** | 45.6% |
| **exp5 (session-relative)** | 518 | **0.228** | -0.091 | **117.9** | **43.8%** |

Under the real objective, exp5 is worse than baseline on *every axis*: lower mean value per
finding, lower median, lower total net opportunity captured across the whole sample (137.3 →
117.9, a 14% decline), and a lower share of net-positive findings. The extra 29 findings it
unlocks are, on average, lower-quality than what the existing gate already keeps — the
recall gain in the flat view was real but came from noise, not missed opportunity.

**Verdict: gating on session-relative percentile abnormality does not improve the
recall/precision trade-off. It is a genuine negative result — do not adopt.**

## The more important thing this run surfaced

Re-running the full magnitude-weighted comparison across every gate experiment tested this
week (data unchanged, just tabulated together for the first time):

| report | n | mean | **sum** |
|---|---|---|---|
| **exp2 (time-decay)** | 497 | **0.293** | **145.8** |
| baseline (hybrid) | 489 | 0.281 | 137.3 |
| exp1 (adaptive) | 550 | 0.253 | 139.2 |
| exp4 (rust-fast-confirm) | 567 | 0.245 | 139.1 |
| exp5 (session-relative) | 518 | 0.228 | 117.9 |
| exp3 (unified gate) | 760 | 0.023 | 17.2 |
| exp123 (combined) | 818 | 0.010 | 8.4 |

**exp2 (time-decay participation bar) is the only experiment that beats baseline on both
mean value-per-finding and total value captured.** It was previously characterized (under
flat classification, earlier in the week) as "marginal, doesn't help where needed" — that
read undersold it. Under the objective that actually matters (net opportunity captured, not
a binary useful/not-useful label), it is the best-performing gate variant tested all week,
edging out even the currently-shipped baseline.

This is not a recommendation to ship it yet — it has not been re-validated end-to-end since
that early characterization, and the user has not been consulted. It is flagged here as the
most promising lead to revisit next, ahead of any further session-relative or cross-sectional
work.

## Discipline note

Consistent with this week's standing practice of reporting negative results plainly
(see [2026-08-18-011](2026-08-18-011-gate-fix-under-hybrid-hypothesis-corrected.md)): this
result is reported as-is, without softening the framing that was used when the experiment
was proposed ("most promising avenue"). It wasn't, empirically.

# Experiment #6 (market-relative percentile participation gate, p80): negative result — worse than both baseline and #5

**Date:** 2026-08-19
**Status:** Tested, NOT adopted. `EXPERIMENT_MARKET_RELATIVE_PARTICIPATION` stays default `False`.

## What was tested

Direct follow-up to #5's rejection. #5 gated on the p60 percentile — close to, and in some
cases below, the existing fixed bar, so it was a mild adjustment. This tests a materially
stricter operating point: gate on whether a candidate's 30s participation is genuinely
**abnormal** for its session (top ~20%, p80) rather than merely "at or above par."

Reused the same real percentile table computed in #5
(`scripts/build_participation_baseline.py`, 501 cached datasets), but extended it to store
the full p50-p95 spread per session (`MARKET_RELATIVE_PARTICIPATION_PERCENTILES` in
`app/market.py`), and added `EXPERIMENT_MARKET_RELATIVE_PERCENTILE` (default 80, snaps to
the nearest available table key) so the operating point is tunable without a code change.
Wired as a new `elif` branch ahead of #5 in the same composable participation-bar block,
behind `EXPERIMENT_MARKET_RELATIVE_PARTICIPATION` (default `False`). Full 134-test suite
passed before running. Ran the same 240-symbol true-hybrid replay (2 concurrent batches),
scored two ways.

## Result

**Flat classification:**

| | +5% | +10% | +20% | +50% | n (actionable) | useful_rate | ambiguous_rate |
|---|---|---|---|---|---|---|---|
| baseline | 18.8% | 26.7% | 46.2% | 70.0% | 489 | 19.6% | 59.9% |
| exp5 (p60) | 20.0% | 26.7% | 46.2% | 72.5% | 518 | 18.3% | 59.7% |
| **exp6 (p80)** | **16.2%** | **21.7%** | **37.5%** | **62.5%** | **317** | **24.9%** | **51.1%** |

At first glance this looks like a real precision/recall trade favoring exp6: fewer, more
selective findings (317 vs 489), and a meaningfully higher useful_rate (24.9% vs 19.6%) —
exactly what a stricter, more "legitimate" bar should produce. Recall drops hard across every
threshold, which is expected and, on its own, would be an acceptable trade if the survivors
were genuinely higher-value.

**Magnitude-weighted** (`net_opportunity_pct = mfe_300s_pct + mae_300s_pct`):

| report | n | mean | median | **sum** | positive_rate |
|---|---|---|---|---|---|
| baseline (hybrid) | 489 | 0.281 | -0.050 | **137.3** | 45.6% |
| exp5 (p60) | 518 | 0.228 | -0.091 | **117.9** | 43.8% |
| **exp6 (p80)** | 317 | **0.114** | -0.093 | **36.0** | **44.8%** |

The flat-classification story reverses completely. exp6's total net opportunity captured
collapses to 36.0 — a ~74% decline from baseline's 137.3, and worse than exp5's already-
rejected 117.9. Average value per finding (mean) is barely a third of baseline's. The higher
useful_rate did not come from finding better trades; it came from filtering out a large
number of smaller-but-real early moves that flat classification doesn't credit much value to,
along with the actual noise.

**Verdict: reject. Gating on session-relative abnormality at p80 is worse than both the
shipped baseline and the already-rejected p60 variant on the metric that matters.** The
higher percentile does not trade recall for quality — it trades recall for *less total
value*, which is the opposite of the goal ("catch all meaningful bullish moves early, not
just the big ones").

## Working explanation (not fully verified — noted as a hypothesis, not fact)

A quick look at exp6's own admitted A/B-rank findings shows they trigger with substantial
`extension_pct`/`change_15s_pct` already present (median ~0.53-0.56 at admit time) — i.e. the
percentile-abnormality requirement, by construction, can usually only be satisfied *after* a
move has already been running for a bit and participation has caught up, not at the ignition
point itself. That would explain why exp6's findings skew toward already-extended entries
(smaller remaining upside, more downside from chasing) rather than genuinely earlier ones. A
rigorous baseline-vs-exp6 side-by-side on this metric was not completed — `findings-
hybrid.jsonl` predates the richer per-finding feature enrichment added mid-week and would
need a baseline re-run under current code to compare like-for-like. Flagging this as the
likely mechanism, not a proven one.

## Where this leaves the participation-gate line of work

Every percentile/abnormality-based variant tested this week (p60 in #5, p80 in #6) has now
underperformed the fixed absolute baseline under the real objective. Combined with #3's
catastrophic result (unified loose bar) and #1/#4's marginal gains, the only gate variant
that has actually beaten the shipped baseline on both mean and total value is **#2
(time-decay bar)** — see [2026-08-19-004](2026-08-19-004-session-relative-bar-negative-result.md)
for the full cross-experiment table. That remains the standout lead. Further session/market-
relative percentile tuning (p70, p85, etc.) is not recommended without a different underlying
idea — the pattern across two tested operating points (p60, p80) both moving in the wrong
direction suggests this is not primarily a percentile-calibration problem.

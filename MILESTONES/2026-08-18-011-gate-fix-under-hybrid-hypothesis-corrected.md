# Milestone 011 — Gate fix tested under true hybrid: my own hypothesis was wrong, corrected with data

Date: 2026-08-18

## What was tested
Milestone 010 ended with a stated hypothesis: since Rust-triggered candidates hit the same
downstream quality gate as Python-native ones, the v2 gate-tuning fix (Milestones 004/005)
should plausibly help *more* under the true hybrid replay than it did in the Python-only
test, since it would unlock Rust-fed candidates too, not just Python's own. Tested this
directly rather than leaving it as a plausible-sounding claim.

## Result: the hypothesis was wrong
| | Baseline (hybrid) | v2 (hybrid) | Δ | Δ vs. Python-only v2 test |
|---|---|---|---|---|
| +5% actionable-before-cross | 18.8% | 20.0% | +1.2pp | **identical** (+1.2pp in Python-only) |
| +10% | 26.7% | 27.5% | +0.8pp | **identical** |
| +20% | 46.2% | 48.8% | +2.6pp | **identical** |
| +50% | 70.0% | 72.5% | +2.5pp | **identical** |
| Per-ticker flip count | -- | 2/1/2/1 | -- | **identical to Python-only (2/1/2/1)** |
| Precision (useful rate) | 19.6% (n=489) | 17.1% (n=561) | **-2.5pp, worse** | Python-only: 29.3%→26.9% (-2.4pp) |

The recall gain is byte-identical in magnitude to the Python-only test -- the exact same 6
tickers flip, regardless of whether Rust is in the loop. **But precision is worse under
hybrid** (17.1% useful vs. an already-degraded 19.6% hybrid baseline) than under Python-only
(26.9% vs 29.3%). The extra Rust-fed candidate volume that the loosened gate lets through
skews toward late/low-quality, same pattern established all day, just now proven to compound
rather than amplify the fix's benefit.

## Honest correction
My own stated hypothesis in Milestone 010 was reasonable-sounding but wrong when actually
tested. This is exactly why every claim in this session gets tested rather than accepted on
reasoning alone -- including my own. **Corrected conclusion: the coordinated gate-tuning fix
is not more attractive under the real deployed system than the earlier Python-only test
suggested. If anything, the precision trade-off is slightly less favorable.** The recall
benefit is real and stable (proven twice now, under two different replay conditions,
identical flip count both times) but small, and the cost is, if anything, worse than
originally measured.

## Practical implication
This does not change the recommendation from Milestones 004/005 (still the user's judgment
call, real trade-off either way) -- it removes one argument that might have pushed toward
adopting it (the hoped-for larger benefit under the real system didn't materialize) while
confirming the recall benefit itself is genuine and reproducible.

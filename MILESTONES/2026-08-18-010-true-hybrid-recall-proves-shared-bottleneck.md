# Milestone 010 — True hybrid replay proves the quality gate, not detection, is the bottleneck

Date: 2026-08-18

## Achieved
Built `scripts/hybrid_replay.py`: replays the same cached tick data used all day through
BOTH engines in correct time order, exactly mirroring production (`app/main.py`'s
`RustPerceptionBridge` -> `MarketWatcher.handle_rust_candidate`), closing the gap found in
Milestone 009. Rust's pre-computed candidates (from the 501-dataset batch, ~3 minutes total)
are merged into the trade-event timeline and fed through the real production call path.
Full 240-symbol replay: **under 5 minutes** (in-memory SQLite, no downloads) vs. ~55 minutes
for the Python-only equivalent.

## Result: recall and precision, Python-only baseline vs true hybrid

| Threshold | Seen (Python-only) | Seen (hybrid) | **Actionable-before-cross (Python-only)** | **Actionable-before-cross (hybrid)** |
|---|---|---|---|---|
| +5% | 95.0% | 98.8% | 18.8% | **18.8% (unchanged)** |
| +10% | 95.0% | 99.2% | 26.7% | **26.7% (unchanged)** |
| +20% | 93.8% | 98.8% | 46.2% | **46.2% (unchanged)** |
| +50% | 92.5% | 100.0% | 70.0% | **70.0% (unchanged)** |

Precision: n=304→489 actionable-rank findings (+61%), useful rate 29.3%→19.6% (worse),
ambiguous rate 42.8%→59.9% (worse).

## What this proves, precisely
Rust genuinely improves raw awareness -- "seen" and "seen-before-cross" both jump
substantially, exactly matching the union numbers from Milestone 009 and confirming the
documented architecture rationale. **But the actionable-rank recall number -- the one that
matters for "did Scout give you a confident, notification-worthy call in time" -- did not
move by a single decimal point.** This is not a coincidence: `handle_rust_candidate`
(`app/market.py:595-649`) requires Python's own live `quality_label == "CLEAN"` regardless of
which engine originated the candidate. Since the quality-layer participation gate (diagnosed
in Milestone 001's root-cause work as the actual blocker for Python-native candidates) is
identical for Rust-triggered candidates, feeding in more/earlier candidates from Rust cannot
help -- they hit the exact same wall. This is direct, quantitative confirmation, not
speculation, that **the bottleneck is the shared downstream quality gate, not either engine's
detection capability.**

The precision cost is the corollary: Rust surfaces many more candidates that eventually
clear the gate, but predominantly *late* (after the move is largely over), which is why
volume rose 61% while useful rate fell. More awareness without fixing the shared gate just
means more low-quality late calls, not more early good ones.

## Why this raises the stakes on the gate-tuning work (Milestones 004/005)
The coordinated v2 gate change tested earlier only benefits Python-native candidates in that
test (it never touched Rust-fed candidates, since that test used the Python-only replay).
Given this result, the SAME gate fix should benefit Rust-triggered candidates equally, since
they hit the identical gate. **The true impact of the v2 gate change is very likely larger
than the 6/240-ticker result measured earlier** -- that measurement only captured half the
system's candidate volume. Testing this directly is the natural next step.

## Not yet done
- Re-run the v2 gate-tuning candidate through the hybrid replay (not just Python-only) to
  measure its true combined effect. Queued next.
- The precision degradation from hybrid volume (489 actionable findings, 59.9% ambiguous)
  is itself worth understanding better before recommending anything ship -- more findings to
  review, not free performance.

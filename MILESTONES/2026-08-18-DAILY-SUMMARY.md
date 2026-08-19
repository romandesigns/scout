# Scout — Daily Summary, 2026-08-18

Read this first. `SESSION-STATE.md` (repo root) has the full chronological technical log if
you need it; the individual `MILESTONES/2026-08-18-00N-*.md` files have full detail per item.
This is the organized version for your return.

**Nothing is committed, pushed, or deployed. Everything below is local, tested, and waiting
for your review.**

---

## 0. Major correction, found late in the day: today's first numbers were Python-only

Everything in section 1 below was originally measured by replaying only Python's detector —
the backtest pipeline never actually invoked Rust, even though production is Rust-primary.
Found this by checking the architecture doc you shared, built a true hybrid replay that
feeds Rust's real output through the exact same production code path
(`MarketWatcher.handle_rust_candidate`), and reran everything. Two real findings from that:

- **Raw awareness is dramatically better than first measured.** "Seen before the move
  crossed its threshold" jumps from 52.5%/69.2%/77.5%/90.0% (Python-only) to
  73.1%/85.8%/87.5%/92.5% (true hybrid) — Rust genuinely delivers on its documented role.
- **But the number that actually matters — confirmed, notification-worthy recall — did not
  move by a single decimal point.** 18.8%/26.7%/46.2%/70.0%, identical in both tests. Proven,
  not guessed: every path to "actionable" (Python-native or Rust-triggered) runs through the
  same Python quality gate already identified as the bottleneck — feeding it more candidates
  from a second engine doesn't help if they all hit the same wall.
- Also tested my own follow-on hypothesis — that the gate-tuning fix would help *more* once
  it could unlock Rust-fed candidates too — and it was **wrong**: the recall gain is
  identical under hybrid (same 6 tickers flip, either way), and precision is actually a bit
  worse. Correcting my own claim here since it turned out to not hold under test.

The numbers in section 1 are now the corrected, true-hybrid ones. See Milestones 009-011 for
full detail.

## 1. The core question: how good is Scout at your actual goal?

Built a real historical backtest pipeline (replays real Alpaca market history through
Scout's *actual* production detector code, not a simulation of it) and got real numbers for
the first time.

**Upward moves** (6 trading days, 240-symbol sample, 40 real movers per magnitude tier;
true hybrid — both Rust and Python replayed together, matching production):

| Move size | Scout ever notices it | Notices before the move happens | **Confirms it before the move happens** |
|---|---|---|---|
| +5% | 99% | 73.1% | **18.8%** |
| +10% | 99% | 85.8% | **26.7%** |
| +20% | 99% | 87.5% | **46.2%** |
| +50% (explosive) | 100% | 92.5% | **70.0%** |

Scout is essentially never blind, and its raw early-awareness (middle column) is genuinely
strong thanks to Rust. The gap is entirely in the last mile: turning that awareness into a
*confirmed* call is weak on smaller/faster moves and strong on explosive ones — not because
Scout is smart about big moves, but because they simply take longer to play out, giving
Scout's fixed quality gate more time to catch up. **That gate is the one and only bottleneck
— proven today by testing both engines together, not assumed.**

**Reversals (bearish → bullish)** — you specifically asked about this one. It's weaker:

| | Scout ever notices it | **Confirms it before the bounce** |
|---|---|---|
| Watch-level bounce (≥0.75%) | 65% | **10.2%** |
| Confirmed reclaim (≥2%) | — | **9.3%** |

Verified this is real, not a measurement bug (spot-checked a "missed" case — Scout had 48
other findings on that ticker that day, so it wasn't blind, the reversal-specific logic just
didn't fire for the actual reversal). **This is the weakest part of Scout's detection and
matches exactly what you said you cared about most.**

**Root cause traced**: reversal detection requires its own separate 30-second participation
bar (on top of the drawdown/bounce math), and that bar's exact numbers
(`REVERSAL_MIN_DOLLAR_30S`/`REVERSAL_MIN_TRADES_30S` = 5000/12) are **identical** to the
quality-layer bar already diagnosed as the root cause of upward-move lateness — the same
strict participation check, independently duplicated across two unrelated code paths. This
showing up twice in one day is itself informative: it suggests Scout's detector has a
systemic pattern of many independently-thresholded participation gates that must all clear
together, not a one-off issue in either path. See Milestone 007.

## 2. Did we make Scout better today? Honest answer: not yet, but we know exactly what to try.

Tested a coordinated change to 4 quality-gate thresholds (the ones a root-cause trace showed
were jointly blocking modest movers) — twice: once Python-only, once under the true hybrid
replay, to see if it helps more once it can unlock Rust-fed candidates too. Real result, not
spin, either time:
- **Gain**: 6 out of 240 real movers caught early that weren't before — identical count under
  both tests. Zero regressions anywhere. Small but genuine and reproducible.
- **Cost**: precision dropped, and it's slightly *worse* under the real hybrid system
  (useful-call rate 19.6%→17.1%) than the Python-only test suggested (29.3%→26.9%) — the
  extra Rust-sourced volume the loosened gate lets through skews toward late, low-quality
  calls, same pattern as everything else diagnosed today.

**This is a real trade-off, not a win — your call on whether it's worth adopting, and the
fuller test made the case for adopting it slightly weaker, not stronger.** A cleaner version
exists (dropped one inert parameter, identical result, smaller footprint) if you do want it:
see Milestone 005.

One thing we're confident is **not** worth trying again: a single-parameter fix
(`WAKEUP_VOL_RATIO` alone) was tested and produced *zero* measurable change — proven, not
guessed, so don't re-litigate that specific idea.

## 3. A real bug found and fixed: notification platform sync

Every notification eligibility check was hardcoded to check the "Android" preference,
including Web Push — which is subscribed to identically from any device's browser. A desktop
browser subscriber's own preference was never actually consulted. Fixed, tested (9 new
tests), full suite green (132/132).

**Important honesty check on this one**: before assuming this was "the" cross-platform fix,
verified against the actual Settings UI. Turns out Windows desktop notifications are a
*different*, deliberately-disabled mechanism (frozen off since v6.5.3 specifically to avoid
duplicate alerts) — the product's real primary shared channel is **ntfy**, not Web Push. So
this fix is real and correct, but the bigger open question — does ntfy itself reliably reach
every platform? — is still unverified and is the actual next step for "sync accuracy."

## 4. New capability built: candlestick pattern recognition

9 standard patterns (Hammer, Engulfing, Morning Star, etc.), unit-tested (22 tests, caught
and fixed one real bug in the process), then validated against real historical data — not
just shipped on theory. Result is genuinely mixed:

- **`INVERTED_HAMMER` shows a real edge**: large sample (3,793 occurrences), +0.43% average
  forward return, 55.6% win rate. Worth investigating further as real evidence.
- Several of the "textbook famous" patterns (Bullish Engulfing, Hammer, Morning Star) showed
  the *opposite* of their reputation on this specific universe — negative average returns.
  Not a coding bug (unit-tested against hand-built examples); more likely textbook pattern
  theory (built for calm daily bars) doesn't transfer cleanly to volatile penny-stock ticks.

Sitting in shadow mode — computed but not fed into live detection, same discipline as
everything else here. Nothing here should go live without the same gate-style validation the
rest of this work went through.

## 5. Pre-halt detection: investigated, correctly inconclusive, not a wasted effort

Wanted to test whether Scout's `HALT_PRESSURE` stage gives real lead time before actual
halts. Built a proxy-based halt detector, caught and fixed two real bugs in my own
methodology before trusting any output (an initial version flagged 5,945 "halts" that were
just normal thin trading — fixed to 182 plausible ones, spot-checked as real). But
`HALT_PRESSURE` only fired once in the whole sample used, so the honest conclusion is
"inconclusive, wrong sample size for this question" — not "Scout can't predict halts." A
purpose-built 203-symbol sample (real +50% movers, where halts cluster) is staged and ready
to actually test this properly.

## 6. What's genuinely ready for your decision right now

1. **Ship the "gates cleared" UI indicator?** Zero risk (no detection logic touched), shows
   near-actionable candidates so you can act before Scout's algorithm fully confirms. Build-
   verified. This is the most clearly positive, lowest-risk thing here.
2. **Adopt the coordinated gate change (v2)?** Real trade-off — more early catches, more
   noise. Needs your judgment, not mine.
3. **Ship the notification platform fix?** Correctness improvement, low risk, but the bigger
   ntfy question is still open — probably ship this regardless of that, since it's strictly
   more correct than the current behavior either way.
4. Everything else (candlestick, halt, reversal work) is measurement/research, not yet at a
   ship decision — needs more iteration first.

## 7. What's queued next, not yet done

- A coordinated fix candidate for the reversal-specific participation bar, tested the same
  before/after way as the upward-move gate work (root cause now known, fix not yet designed).
- Purpose-built halt-precursor replay (staged, ready to run).
- Holdout validation of the gate change on data never used for iteration (staged, ready).
- `hybrid_score` and `precursor_finding_id` — real computed data, never surfaced in the UI,
  lower priority, offered but not built.

## Everything is reproducible
All scripts, all data, all reports are in the repo (`scripts/`, `data/optimization/backtest/`,
`MILESTONES/`). Nothing here required guessing — every number came from replaying real market
history through Scout's real code.

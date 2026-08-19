# Milestone 002 — Candlestick pattern module (validated) + halt investigation (inconclusive, correctly identified as such)

Date: 2026-08-18 (post-restart, autonomous continuation)

## Achieved
- Built `app/candlestick.py`: 9 standard bullish candlestick patterns as pure, testable
  functions, shadow/observational only (does not touch live detection).
- 22 unit tests (`tests/test_candlestick.py`), all passing — including one real bug caught
  and fixed (body-relative wick threshold breaking down for small-body hammers).
- Full project test suite confirmed still green after the addition (123/123).
- Real-data validation across 295 cached historical datasets: found a genuine, well-sampled
  edge in `INVERTED_HAMMER` (n=3,793, +0.43% avg 15m return, 55.6% positive rate) — and
  equally importantly, found that several "textbook famous" bullish patterns (Bullish
  Engulfing, Hammer, Morning Star, Piercing Line) show *negative* average forward returns on
  this specific universe/timeframe, contrary to their reputation. Reported both findings
  honestly rather than only the flattering one. Full detail in `SESSION-STATE.md`.
- Built and debugged `scripts/halt_gap_finder.py` (proxy halt detection from trade-print
  gaps). Caught and fixed two real bugs before trusting any output: (1) an initial heuristic
  that was far too loose, flagging normal illiquid trading lulls as "halts" (5,945 false
  positives on 240 symbols); (2) an O(n²) performance bug after tightening it. Fixed both,
  verified the tightened version against spot-checked real examples before drawing any
  conclusion.

## Correctly identified as NOT achieved (avoided overclaiming)
- Halt-precursor lead time: the tightened proxy found 182 plausible suspected halts, but
  `HALT_PRESSURE` only fired once in the entire 240-symbol sample used — concluded this is an
  insufficient-sample problem, not evidence Scout can't predict halts, and did **not** report
  the resulting "0% warn rate" as a real finding. Built a purpose-sampled 203-symbol +50%-tier
  pool (`movers-halt-candidates.jsonl`) to actually test this properly, queued to run next.
- Candlestick patterns are validated as *measured on historical data*, not validated as *safe
  to add to live detection* — those are different bars, and only the first has been cleared.

## Queued next
- Run the coordinated multi-gate v1 gate-tuning replay to completion (in progress
  concurrently with this milestone's work) and score it.
- Run the purpose-sampled halt-candidate replay once v1 is done (avoiding concurrent
  CPU-heavy replays).
- Holdout validation for whichever gate change (if any) wins.

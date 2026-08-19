# Milestone 009 — Major methodology gap found and corrected: today's backtest never exercised Rust

Date: 2026-08-18

## What was found
Prompted by the user sharing the v6.4.13 architecture design rationale (Rust = primary
perception engine, Python = specialist/context layer, union of both beats either alone:
Rust 53.66% recall, Python 23.63%, union 62.10% on the frozen 1,422-event evaluation).
Checked whether today's backtest pipeline actually exercised both engines. **It did not.**

`app/replay.py`'s `run_dataset()` -- the function every single script built today
(`historical_backtest.py`) calls for every replay -- only constructs a Python `MarketWatcher`
and feeds it trade events directly. It never invokes Rust. Confirmed by grep: zero mentions
of "rust"/"hybrid" anywhere in `app/replay.py`. Rust only gets wired into the live system via
`app/main.py`'s production startup (`RustPerceptionBridge`).

**This means every recall/precision number reported today (the +5/10/20/50% table, the
reversal detection numbers, the gate-tuning before/after comparisons) measured Python's
detection alone -- not the full Rust-primary hybrid system that's actually deployed.**

## Real, fresh data confirming this matters
Built and ran the actual Rust replay binary (`cargo build --release`, confirmed working,
`rust/market-replay/target/release/scout-market-replay.exe <dataset> --output <report>`)
against all 501 cached tick datasets already downloaded today -- same data, same tickers,
same dates as every Python test run today. Processed in **under 3 minutes** (vs. Python's
~55 minutes for less than half as many datasets), empirically confirming the documented
"Rust for fast broad perception" design choice.

Compared against the same 240-symbol ground truth used all day:

| Threshold | n | Python "seen" | Rust "seen" | Union | Python before-cross | Rust before-cross | **Union before-cross** |
|---|---|---|---|---|---|---|---|
| +5% | 160 | 95.0% | 98.8% | 98.8% | 52.5% | 69.4% | **73.1%** |
| +10% | 120 | 95.0% | 99.2% | 99.2% | 69.2% | 82.5% | **85.8%** |
| +20% | 80 | 93.8% | 98.8% | 98.8% | 77.5% | 86.2% | **87.5%** |
| +50% | 40 | 92.5% | 100.0% | 100.0% | 90.0% | 90.0% | **92.5%** |

Rust's raw perception alone beats Python's raw perception on every single row, and the union
substantially beats either alone on "before-cross" timing -- directly consistent with the
documented v6.4.13 rationale, now confirmed with current data instead of only the original
freeze-time evaluation.

## Important, precise caveat -- do not overstate this
The "before-cross" numbers above are **raw detection timing** (did any candidate/finding
exist at all, from either engine, before the threshold crossed) -- **not** the
"actionable-rank" timing reported earlier today (18.8%-70.0%). Rust's candidates are always
`shadow_mode: true` PRE_IGNITION-caliber signals in isolation; in production they only become
a real, non-shadow, user-facing "AWAKENING" alert after passing back through Python's live
`quality_label`/`vol15`/etc. checks (`app/market.py:595-649`, `handle_rust_candidate`,
already noted in Milestone 008). Reconstructing that full path faithfully in replay would
require actually feeding Rust's output through `handle_rust_candidate` in true event order --
not done here, and not something to fake with an approximation.

**So the honest conclusion is bounded correctly**: today's numbers understate the *raw
awareness* half of the system substantially (this is now proven, not guessed) and this
strongly suggests -- but does not yet prove with the same rigor as the rest of today's work --
that true actionable-rank recall is also meaningfully better than the Python-only numbers
reported earlier. That gap between "strongly suggests" and "proven" is exactly where the
next piece of work should go.

## Recommended next step (not yet built, well-scoped)
Build a true hybrid replay harness: feed the same cached NDJSON through both engines in
correct time order, routing Rust's candidates through `MarketWatcher.handle_rust_candidate`
exactly as `app/main.py` does live, so the actionable-rank recall table can be recomputed
for the *actual deployed system*, not just Python's half of it. This is the single highest-
value remaining piece of work from today -- everything else (gate tuning, reversal detection,
notification fix) remains valid on its own terms, but the headline "how good is Scout"
numbers from earlier today should be understood as a Python-only lower bound, not the full
picture, until this is built.

## Scope note on the user's request to "implement if it outranks the documentation"
This finding does not compete with or outrank the documented architecture -- it **validates**
it with fresh data (the ensemble/union benefit the docs describe is real and still holds
today). What it changes is confidence in *today's own testing methodology*, not Scout's
actual design. No code change is proposed here; the corrective action is building the proper
hybrid-inclusive replay measurement, not altering the Rust/Python architecture itself.

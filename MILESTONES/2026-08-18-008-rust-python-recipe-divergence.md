# Milestone 008 — Rust/Python "shadow recipe" divergence is live, not just theoretical

Date: 2026-08-18

## Achieved
Investigated the Rust engine (`rust/market-replay/src/lib.rs`) directly, prompted by the
user asking for optimization areas beyond the Python-side gate work already done today.

Found that Rust's `evaluate()` function is an independent reimplementation of Python's "V6.3
shadow recipe" (`app/market.py` ~1704-1723) — same 8 named checks, but at least 2 of them use
**structurally different formulas**, not just different constants:

| Check | Python (current, env-configurable) | Rust (hardcoded, frozen at v6.4.13) |
|---|---|---|
| "relative volume is waking up" | `vol15 >= max(1.5, FIRST_LEG_MIN_VOL_RATIO(3.0)*0.6)` — a ratio-to-rolling-baseline | `volume15*2.0 >= volume30` — raw 15s sum vs raw 30s sum, no baseline at all |
| "participation is broadening" | `dollar15 >= 1250 AND trades15 >= 4` | `trades15.len() >= 3` — no dollar-volume requirement at all |

These are not numerically-drifted versions of the same test -- they measure genuinely
different things. `extension<=0.75`, `trigger_distance` in `-0.35..0.75`, and `score>=7` do
still match Python's current equivalents exactly (checked directly against `app/config.py`).

## Why this matters more than it might sound
Checked where Rust's `recipe_score` actually gets used (`app/market.py:595-649`,
`handle_rust_candidate`) before writing this up, since "two engines compute things
differently" is only actionable if it affects real output. It does:
`recipe_score >= 7` (Rust's own formula) is a **direct, hard gate** on whether a candidate
becomes a real, live, non-shadow **"AWAKENING"** finding (`shadow_mode = not actionable`,
`actionable_rank` A or B). Meanwhile, Python's own equivalent PRE_IGNITION recipe is
explicitly commented as **deliberately silent**: *"intentionally silent until lead-time and
false-arm rates are measured across representative sessions."*

So: the exact kind of early, statistically-unvalidated signal Python's engineers decided to
withhold from live notifications is, via the Rust path using different math for 2 of 8
checks, **already live and generating real AWAKENING alerts today** — not measured with the
same rigor as everything else built this session, and not something either engine's authors
can currently audit against the other, because there's no automated numeric-formula parity
check (the existing `scripts/compare_replay_parity.py` compares check *names* and aggregate
outcome recall/precision, not the underlying per-check formulas).

Also found: `data/replays/parity-v6.4.12.json` / `parity-v6.4.13.json` are the only parity
validation artifacts, dated to the Rust engine's original freeze. Python's thresholds have
been tuned repeatedly since (v6.6.1 through v6.7.3 per `CHANGELOG.md`) with no re-validation
against Rust. The two engines' agreement has not been re-checked in a long time.

## Not yet done
- No fix applied -- this is a diagnosis, same as the gate-lateness and reversal root causes.
- A real fix has real design choices to make (should Rust's formulas be brought in line with
  Python's current ones, or is the divergence intentional/acceptable given Rust runs as a
  fast first-pass filter? That's a legitimate architecture question, not just a bug to patch).
- Re-running `compare_replay_parity.py` with current thresholds to get a fresh, current
  recall/precision comparison (last known result is from the v6.4.12/v6.4.13 freeze point)
  would be the natural first step before deciding on a fix.

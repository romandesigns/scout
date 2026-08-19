# Scout Historical Backtest — Recall, Precision & Optimization Findings

Status: **built and validated against real Alpaca history; not committed to git; no detector code changed in production.**

This documents a working backtest pipeline plus a real, evidence-based investigation into
why Scout is sometimes late to confirm real bullish moves — including a hypothesis that was
tested and disproven, reported honestly rather than dropped.

## 1. Why this exists

Scout already had two families of self-audit:

- **Live precision audits** (`detection-quality`, `promotion-trace`) — grade findings Scout
  already emitted. They cannot see what Scout never flagged.
- **Live recall audit** (`recall_opportunity.py`, v6.7.2/6.7.3) — measures misses, but only
  as fast as real live sessions accumulate data. A single session rarely produces enough
  +20%/+50% events to say anything statistically meaningful.

Neither can answer the actual question: **of real historical bullish moves, especially
explosive ones, how many did Scout catch early enough to act on — and why not the rest?**

This backtest answers that by replaying real historical Alpaca tick data through Scout's
*actual production detector code* (`app.replay.run_dataset`, the same engine the Replay
Spine uses), fully isolated from production storage/notifications, against a ground truth
built independently of Scout's own detection logic.

## 2. Pipeline

| Stage | Script | What it does |
|---|---|---|
| 1. Ground truth | `scripts/historical_mover_finder.py` | Scans real Alpaca 1-min bars across the full tradable $0.15–$10 universe for a date range. Detector-blind — finds movers by price action alone, records the exact timestamp each of +5/10/20/50% was first crossed. Also samples non-mover control rows. |
| 2. Sampling | `scripts/sample_movers.py` | Draws a bounded, tier-stratified random sample (e.g. 40 per magnitude tier + control) so the expensive replay stage doesn't have to process every mover found — necessary because Stage 1 is cheap (~1 min/trading day) but Stage 3 is not. |
| 3. Replay | `scripts/historical_backtest.py` | Downloads real tick-level trades per (ticker, date) — cached to disk so reruns are free — and replays them through Scout's real `MarketWatcher` detector, capturing every finding (stage, rank, quality, and full `promotion_trace` gate detail) exactly as production would have computed it. |
| 4. Scoring | `scripts/backtest_scorer.py` | Joins ground truth + replay findings. Computes recall (seen at all / seen before threshold crossed / actionable before crossed) per magnitude tier, and precision (useful / false-positive-fade / ambiguous) using the same classification logic as the live `detection-quality` audit. Rows never actually replayed are excluded from the denominator rather than being miscounted as "missed." |
| Wrapper | `run-historical-backtest.ps1` | Chains all four stages for a given date range; `-Sample` enables the bounded sampling stage. |

All raw tick data and replay reports are cached under `data/replay-datasets/backtest/` and
`data/replays/backtest*/` so any stage can be rerun without re-downloading.

## 3. Real result: 6 trading days, 240-symbol stratified sample

Ground truth scanned the full tradable universe (~8,800 symbols) across **Aug 3–7 and Aug
14, 2026**, finding **5,761 real mover/control rows**. From that, a stratified sample of
**240 symbols** was drawn and fully replayed: 40 real movers at each of the +5/10/20/50%
tiers, plus 80 non-mover controls.

### Recall — does Scout catch it, and early enough to act?

| Threshold | Real movers (n) | Scout ever saw it | Saw it before threshold crossed | **Actionable before crossed** |
|---|---|---|---|---|
| +5% | 160 | 95.0% | 52.5% | **18.8%** |
| +10% | 120 | 95.0% | 69.2% | **26.7%** |
| +20% | 80 | 93.8% | 77.5% | **46.2%** |
| +50% | 40 | 92.5% | 90.0% | **70.0%** |

**Scout is not blind at any magnitude** — raw "ever saw it" is flat at 92–95% across all
tiers. But its ability to be *early enough to actually participate* rises sharply with move
size: only **18.8%** of modest +5% moves are confirmed actionable before the move happens,
versus **70%** of true +50% explosive moves. For your stated goal, this is a real, mixed
answer: Scout is comparatively good at catching genuinely explosive moves early, and
comparatively weak at catching smaller-but-still-meaningful moves before they're already
over.

### Precision (n=304 actionable findings from the same sample)
Useful: 29.3% · False-positive/fade: 28.0% · Ambiguous: 42.8%
(This sample is deliberately mover-enriched for the recall test, so this precision number
runs higher than the ~3.5% seen in the unconditioned live-snapshot audit — it is not
directly comparable to that baseline.)

## 4. Root-cause diagnosis: why the +5%/+10% tier is late

Isolating the 105 real events where Scout *saw* the mover before the +5%/+10% threshold
crossed but never made it *actionable* in time, and reading each one's `promotion_trace`
gate detail:

| First (blocking) gate | Share of the 105 late events |
|---|---|
| `relative_activity` (15s/30s volume ≥ 4.0× baseline) | 36.2% |
| `quality_clean` | 25.7% |
| `participation` | 21.9% |
| `fresh_impulse` | 14.3% |
| `full_warmup` | 1.9% |

`relative_activity` — the requirement that trailing 15s or 30s traded volume reach 4.0×
baseline (`WAKEUP_VOL_RATIO`, floor 1.5) — was the single largest first-blocker, ahead of
Scout's existing `fast_single_bucket`/`staircase` fallbacks for that same gate.

## 5. Tested fix: lower `WAKEUP_VOL_RATIO` 4.0 → 2.5 — **result: no measurable change**

This is a single, reversible, already-exposed config parameter, so it was the right first
thing to test rather than restructuring detector code. The exact same 240-symbol sample was
replayed again with `WAKEUP_VOL_RATIO=2.5`, using the same cached tick data.

| Threshold | Actionable-before-crossed, baseline | Actionable-before-crossed, vol=2.5 |
|---|---|---|
| +5% | 18.8% | 18.8% (unchanged) |
| +10% | 26.7% | 26.7% (unchanged) |
| +20% | 46.2% | 46.2% (unchanged) |
| +50% | 70.0% | 70.0% (unchanged) |

Precision moved negligibly (useful_rate 29.3% → 29.8%, n=304 → 305).

**Why it did nothing, confirmed directly from the trace data:** of the 38 late events where
`relative_activity` was the first-listed blocker, **all 38** also had `participation`,
`quality_clean`, `quality_actionable`, and `first_leg_release` failing at the same moment
(and `fresh_impulse` in 33/38). `relative_activity` was never the *sole* blocker. Loosening
it alone leaves the candidate blocked by four other simultaneously-unmet gates — the
threshold change had nothing to bite on.

## 6. Honest conclusion

The lateness on modest (+5%/+10%) moves is not caused by one misconfigured threshold. It's
a **convergent-evidence requirement**: several independent gates (volume, participation,
clean structure, fresh impulse, first-leg release) typically all mature at roughly the same
time, late in a modest move's short life, by design — Scout requires multi-factor
confirmation before calling something actionable, which is presumably deliberate
precision-protective behavior, not an oversight. A real fix would need to loosen or
reorder *multiple* gates together and be validated the same rigorous way (replay before/after
on the same sample) to see whether earlier confirmation is achievable without letting
precision collapse — a larger, more careful piece of work than a single-parameter change,
and outside what this session attempted.

**What actually held up well:** Scout's handling of genuinely explosive (+50%) moves — 70%
caught early enough to act — is a real strength worth preserving in any future gate change,
not something to risk while chasing the smaller-move problem.

## 7. Known limitations of this backtest

- Sample: 6 trading days, 240 stratified symbols (real, but not yet multi-week/multi-regime).
- Session coverage: pre-market through after-hours SIP (4am–8pm ET); overnight/BOATS not replayed.
- Precision numbers here are from a mover-enriched sample, not a neutral live population.
- `+50%` tier (n=40) is solid for a first pass but would benefit from a larger sample before
  treating 70% as a stable number.

## 8. How to reproduce or extend

```powershell
# Full pipeline, sampled, for a date range:
.\run-historical-backtest.ps1 -Start 2026-08-03 -End 2026-08-14 -Sample -CapPerTier 40 -ControlCap 80

# Re-score an existing replay after a config/code change:
python -m scripts.backtest_scorer --movers <movers.jsonl> --findings <findings.jsonl> --output <report.json>
```

Any future gate-tuning proposal should be validated with this same replay-before/after
method before being recommended for production.

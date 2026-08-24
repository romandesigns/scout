# Codex Handoff — 2026-08-23/24 (late session, updated post-release)

Purpose: bring a fresh agent up to speed on everything covered this session, what's
still running unattended, what's genuinely pending a decision, and what to double-check
before trusting any prior "pending"/"not deployed" notes elsewhere in the repo.

## 0. Read this first: stale-status trap

Earlier in this session, `SESSION-STATE.md` and files under `MILESTONES/` described the
VWAP reentry safety gate and the event-loop starvation fix as "locally implemented,
awaiting approval to commit/deploy." **That was wrong** — both were verified to already
be live in the codebase and shipped in v6.7.6 (see CHANGELOG.md). Those files were never
updated after the work actually finished and released. **Do not trust "pending" language
in milestone/session docs without grepping the actual code and CHANGELOG.md first.**

## 1. Current repo state

- Version: `6.11.1` (see `VERSION`) — **fully deployed**: VPS backend, Rust hybrid, and
  PWA are healthy at `6.11.1` with 2,406 symbols; Windows desktop installed locally.
  Pre-deploy backup: `/home/wavystack/scout-backups/scout-before-6.11.1-20260824-044216.tar.gz`.
- Latest commits on `main` (all pushed to `origin/main`):
  - `be31719` — "Redesign market lists and opportunity inbox" (from a separate Codex
    session running without SSH access; bumped VERSION to 6.11.1, further edited
    `web/app/page.tsx` / `web/app/globals.css` on top of the Watchlist work below).
    Codex built the Windows installer but could not reach the VPS (no SSH key in its
    environment) — this session completed that VPS/PWA deploy leg afterward.
  - `85486c1` — gated local-only `/api/development/simulate-finding` test endpoint
  - `6a1c981` — Watchlist panel, mobile-first list readability, item dividers, `scripts/train_outcome_gate.py`
  - `baa25ae` and earlier — pre-existing, unrelated to this session
- `git status` should be clean as of this handoff (verify with `git status --short`)

## 2. What was actually accomplished this session

### 2a. Docker log performance review (early session)
Analyzed ~9 min of live Docker logs. Findings: 154 lifecycle events, 12/103 actionable
(11.7%), 30 HTTP 502s (all on ticker `QS`, external data-source issue, not a Scout bug),
22.4% duplicate-detection rate. No action items came out of this beyond confirming the
system was healthy.

### 2b. ML / "learn from objectives" investigation
- Confirmed Scout already has a shadow ML classifier (`app/imminent_gate.py` +
  `scripts/train_imminent_model.py` / `train_imminent_alert_gate.py`), trained on a narrow
  "+2% in 15-30s" proxy label from replayed detections. Shadow-only, does not gate alerts.
- `paper_trades` table is **empty (0 rows)** — Alpaca paper trading exists as a feature
  (`app/trader.py`) but isn't active, so there's no real P&L to train on yet.
- `outcomes` table **is** rich: 167,793 rows with `max_1m/5m/15m/session_pct` and
  `time_to_peak_seconds` for nearly every finding (170,853 findings total).
- Built `scripts/train_outcome_gate.py`: trains a `HistGradientBoostingClassifier`
  directly against `data/state.db`'s `findings` JOIN `outcomes`, using
  `outcomes.max_5m_pct >= 3.0%` as the label (configurable via `--label-field` /
  `--expansion-pct`). Reuses the same feature contract as the existing shadow gate
  (`app.learning_features.FEATURES`), so the artifact is a drop-in for
  `app/imminent_gate.py::score_finding`.
- First run (6 live days, 2026-08-17→08-21 train, 08-21 validation, 08-24 test):
  134,885 train rows, **40.4% precision / 27.4% recall on validation (ROC AUC 0.938)**,
  40.0%/13.0%/0.744 on the small test split. Base rate is ~1%, so this is a ~40x lift in
  signal concentration. **Not deployed anywhere — model+report only exist at
  `data/optimization/outcome_gate.joblib` / `outcome_gate_report.json`.**
- Only 6 distinct calendar dates of live data existed, which is thin. This led to the
  historical-backtest expansion below.

### 2c. Historical backtest pilot (still running — see §3)
Discovered Scout already has a full offline pipeline for exactly this
(`scripts/historical_mover_finder.py` → `scripts/sample_movers.py` →
`scripts/historical_backtest.py` → `scripts/backtest_scorer.py`, orchestrated by
`run-historical-backtest.ps1`), which pulls real Alpaca SIP data and replays it through
Scout's actual production detector. Kicked off a 5-trading-day pilot
(2026-08-10→08-14) redirected to an external drive (`H:\scout-backtest\`, 931 GB free)
since the default cache/output paths point at the repo's `data/` folder.

**This is still in progress — see §3 for exact state and how to resume.**

The eventual next step (not started): once `findings-pilot.jsonl` exists, write a small
script to compute `max_1m/5m/15m/session_pct` labels from the cached `.ndjson` tick data
for each backtest finding (same definition as the live `outcomes` table), then pool that
with the live DB data and retrain `train_outcome_gate.py` on a much larger date range.

### 2d. Web UI changes (committed, deployed locally, deploy to VPS in progress — see §3)
- `web/app/page.tsx`, `web/app/globals.css`, `web/lib/types.ts`:
  - Mobile-first readability pass (bigger touch targets/padding/font sizes under
    `max-width:1023px`, applies to `.market-row`/`.ticker-decision`/`.market-metrics`).
  - Persistent divider between list items (`box-shadow: inset 0 -1px 0 var(--line-soft)`
    on every `.market-row`/`.event-row`; previously explicitly suppressed).
  - New **Watchlist** feature: `localStorage`-persisted ticker list
    (`stockhunter-scout-watchlist-v1`), a `WatchlistPanel` component, wired into both the
    desktop `ActivityRail` and the mobile bottom nav (now 6 destinations, was 5), plus a
    right-click "Add/Remove from watchlist" action on `FindingRow` (Radar + Ross
    Screener call sites only — not yet wired into 24H/Gainers/Halts rows).
- Verified with `bun run tsc --noEmit` — no TypeScript errors.
- Rebuilt and redeployed the **local** Docker container (`docker compose build scout &&
  up -d --force-recreate scout`) — confirmed healthy and serving the new build.
- VPS deploy of this + the notification test endpoint is part of the in-progress
  `release-all.ps1` run — see §3.

### 2e. Notification cross-platform sync verification
Verified the architecture (not a guess): exactly one SSE broadcast (`/api/events`
`finding` event) reaches every connected client (Tauri desktop, installed PWA, plain
browser tab) simultaneously; each client independently applies **mirrored** eligibility
logic — client-side `coreAllowed()` (`web/lib/native.ts`) vs. server-side
`notification_allowed_any_platform()` (`app/notifiers.py`) — confirmed to have parity
(same stage/quality/edge-validation/session/quiet-hours checks).

Because `paper_trades` is empty, the profitability-validation gate
(`edge_validation.validated`) can basically never pass for ordinary momentum stages —
meaning **real FIRST_MOVE/SECONDARY_ENTRY notifications may not fire in this environment
until paper trading accumulates 30+ completed brackets per cohort.** Only
CATALYST/HALT-type "EVENT" stages are exempt from that gate.

To actually test the pipeline live, added a **gated, local-only** test endpoint:
- `app/api.py`: `POST /api/development/simulate-finding` — constructs a synthetic
  `Finding` (ticker `ZZTEST`, stage `CATALYST_ACTIVE` so it bypasses the profitability
  gate) and fires it through the **real** `Dispatcher.emit()` path (persist → gate →
  queue ntfy/webpush → publish SSE) — the same code every live finding uses.
- `app/config.py`: `enable_finding_simulation: bool = _b("ENABLE_FINDING_SIMULATION",
  False)` — off unless explicitly set. Turned on **only in the local `.env`** (not
  committed; `.env` is gitignored).
- **Verified working end-to-end** on 2026-08-23: fired one test finding, confirmed (a)
  SSE broadcast reached the open browser tab and rendered the in-page toast
  (`client-displayed` event logged with `"web","browser-toast"`), and (b) server-side
  `notification_delivery_events` showed `ntfy: queued → sending → provider_accepted`.
  Tauri desktop and installed-PWA paths were **not** directly tested (no such runtime
  available in this environment) but consume the identical SSE event + gate logic.
- **This endpoint and flag are now committed to the repo** (commit `85486c1`) as a
  permanent-but-inert dev tool. To use it again: ensure `ENABLE_FINDING_SIMULATION=true`
  is set in the target environment's `.env`, then `POST /api/development/simulate-finding`
  with no body.

## 3. Operations currently in progress (as of this handoff)

### 3a. Historical backtest pilot — background OS process, NOT tied to this chat session
- Command: `scripts.historical_backtest` replaying `H:\scout-backtest\output\movers-pilot-sample.jsonl`
  (793 ticker-day rows, sampled from 2026-08-10→08-14 with `CapPerTier=150`,
  `ControlCap=300`) into `H:\scout-backtest\output\findings-pilot.jsonl`, caching raw
  Alpaca SIP ticks under `H:\scout-backtest\cache\` and per-run replay state under
  `H:\scout-backtest\replays\`.
- Started ~2026-08-23 22:30 local time. Measured pace: ~2.25 min/ticker-day →
  **estimated total runtime ~29 hours** (i.e., likely finishes sometime 2026-08-24 evening
  or into 2026-08-25). This is a real, unavoidable pace — it's bound by sequential Alpaca
  API pulls, not compute.
- A separate PowerShell monitor loop is also running (10-minute poll interval), printing
  progress and will print `BACKTEST_COMPLETE: ...` once
  `H:\scout-backtest\output\findings-pilot.jsonl` exists.
- **To check progress**: `Get-ChildItem H:\scout-backtest\cache -File | Measure-Object
  -Property Length -Sum` (file count ≈ ticker-days completed out of 793), or read
  `H:\scout-backtest\output\stage3.log`.
- **To resume/finish this thread once it completes**: write the tick→outcome labeling
  script described in §2c, pool with live DB data, retrain
  `scripts/train_outcome_gate.py` across the combined dataset, and report validation
  metrics vs. the 6-day baseline (40.4% precision / ROC AUC 0.938).
- This process survives the chat session ending (it's a plain Windows background
  process) but **will not survive a reboot or the terminal being killed**. If the user's
  machine restarts, this needs to be restarted from `scripts.historical_mover_finder`
  (stage already done, output cached at `H:\scout-backtest\output\movers-pilot-sample.jsonl`)
  → re-run `scripts.historical_backtest` with the same args (it will skip any
  `.ndjson` files already cached, so resuming is cheap for completed tickers).

### 3b. Full release pipeline (`release-all.ps1`) — COMPLETE
- Triggered by explicit user request ("push everything live... rebuilt tauri"), then
  completed a second time after a separate Codex session pushed `be31719` (6.11.1) but
  couldn't finish the VPS leg (no SSH key in its environment).
- Final state: `Build=SKIPPED` (reused an already-built installer for the second run),
  `Desktop=INSTALLED`, `VPS=DEPLOYED`, `PWA=DEPLOYED`. Confirmed healthy at 6.11.1 with
  2,406 symbols. Installer:
  `D:\wavystack\scout-v6.2.0-repo\release\windows\StockHunter Scout_6.11.1_x64-setup.exe`.
- Output logs `release-all-output.log` and `release-all-output-2.log` are leftover in
  the repo root — see open item below.
- **Lesson for future agents**: if VPS deploy fails specifically with an SSH/key error,
  it usually means the current environment lacks `~/.ssh/id_ed25519` — check whether a
  different session/environment (like this one) already has access before assuming the
  deploy needs a fix. Re-running with `-SkipBuild` (and `-SkipDesktopInstall` if the
  installer isn't needed either) avoids rebuilding from a session that already produced
  a valid artifact.

## 4. Open items / considerations for whoever picks this up

1. **Outcome-gate model is not wired into production.** It's a research artifact
   (`data/optimization/outcome_gate.joblib`). Before considering deployment as a real
   shadow gate (via `IMMINENT_GATE_MODEL_PATH`), it needs: (a) the backtest-expanded
   training set from §2c/§3a, (b) more than 6-8 calendar dates of validation, and (c)
   explicit user sign-off per their standing instruction ("implement but wait for
   confirmation before committing/pushing/deploying").
2. **Paper trading is inactive.** If the user wants the ML pipeline to eventually learn
   from real P&L (their original stated goal), Scout Trader needs to actually be enabled
   (`app/trader.py`, gated by Alpaca paper credentials + a dashboard toggle) and run for
   a while to accumulate `paper_trades` rows. Nobody has decided to do this yet.
3. **Notification profitability gate blocks momentum alerts entirely in this
   environment** until paper trading accumulates 30+ completed brackets per cohort (see
   §2e). This is expected/by-design (v6.8.7), not a bug, but is worth knowing before
   concluding "no alerts fired" means something is broken.
4. **`scripts/_tmp_*.py` throwaway files were created and deleted during diagnostics
   this session** — none should remain, but worth a `git status` / `Get-ChildItem
   scripts/_tmp_*` sanity check.
5. **The `simulate-finding` endpoint writes real rows into `data/state.db`** tagged
   ticker `ZZTEST` — harmless (excluded from any real analysis by ticker name) but not
   auto-cleaned. Consider a periodic cleanup or leave as-is; it's cosmetic only.
6. **Local `.env` now has `ENABLE_FINDING_SIMULATION=true`** appended — intentionally
   local-only, not committed. If the VPS `.env` is ever synced from a snapshot of this
   machine's `.env`, make sure that line is stripped first (it should stay off in
   production).
7. **`release-all-output.log` and `release-all-output-2.log`** are leftover artifacts of
   this session in the repo root — not gitignored; delete them or `git rm --cached` if
   they show up in `git status`.
8. **Open question raised at the end of this session, not yet resolved**: whether the
   platform is genuinely "ready for live sessions." Infrastructure/deploy health is
   confirmed good, but whether the VPS's own `paper_trades` table has enough history to
   satisfy the 30-bracket profitability gate (item 3 above) was **not verified** — the
   user declined an SSH check into the VPS database at the time. Whoever picks this up
   should check `docker exec stockhunter-scout python -c "import sqlite3; c=sqlite3.connect('/data/state.db'); print(c.execute('SELECT status, COUNT(*) FROM paper_trades GROUP BY status').fetchall())"` on the VPS (read-only, safe) before concluding readiness either way.

## 5. Quick reference: key files touched or discussed this session

| File | Role |
|---|---|
| `scripts/train_outcome_gate.py` | New: trains shadow gate on live `outcomes` table data |
| `app/api.py` | New `simulate_finding` handler + route |
| `app/config.py` | New `enable_finding_simulation` flag |
| `web/app/page.tsx` | Watchlist panel, mobile CSS hooks, FindingRow watch toggle |
| `web/app/globals.css` | Item dividers, mobile-first sizing, watchlist styles |
| `web/lib/types.ts` | Added `"watchlist"` to `selection_context` union |
| `H:\scout-backtest\` | External-drive staging area for the historical backtest pilot |
| `data/optimization/outcome_gate.joblib` / `_report.json` | First-pass trained model + metrics (6-day dataset) |

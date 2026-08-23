# Scout Replay Spine 1.0

The Replay Spine feeds historical trade events through the same Python detector path used by live Scout. Every run is isolated in its own SQLite database and report directory. Replay dispatch cannot reach production notification providers.

## Offline smoke replay

From PowerShell 7 in the project directory:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
python -m unittest tests.test_replay -v
python -m scripts.run_replay .\tests\fixtures\replay-smoke.ndjson --output .\data\replays
```

The summary is printed to the terminal. The complete report is written beneath `data\replays\<run-id>\report.json`; `data\replays\latest.json` powers Scout's `REPLAY READY` status.

## Build an Alpaca dataset

Ensure `.env` contains `ALPACA_API_KEY`, `ALPACA_API_SECRET`, and the intended `ALPACA_FEED`. Load those values into the PowerShell process or run the helper below.

```powershell
.\run-replay.ps1 -Symbol STKH -Date 2026-08-14
```

The helper creates `data\replay-datasets\STKH-2026-08-14-sip.ndjson` and then replays it into `data\replays`.

If the reviewed STKH chart belongs to a different market date, pass that exact date instead. Historical replay validity depends on matching the screenshot/session date.

## Canonical event contract

Each NDJSON line contains:

```json
{
  "schema": "scout.market-event.v1",
  "event_type": "trade",
  "symbol": "STKH",
  "source_ts": 1786728600.0,
  "received_ts": 1786728600.0,
  "sequence": 1,
  "feed": "sip",
  "payload": {"price": 3.27, "size": 100}
}
```

Only `trade` is enabled in Replay Spine 1.0. The versioned envelope deliberately reserves quote, bar, news and market-status expansion for the next increment.

## Safety properties

- Replay uses a capture-only dispatcher.
- Findings never enter production `state.db`.
- Notification workers are never started.
- Web Push, ntfy and email functions are never called.
- Every finding is tagged `SIMULATION` in the report.
- Dataset SHA-256, schema, Scout version and replay-engine version are recorded.
- Duplicate sequences are skipped and counted; out-of-order input is counted and deterministically sorted.

## Current limitation

Replay Spine 1.0 records all findings and performance data but does not yet expose an interactive run launcher inside Inspector. The API exposes `/api/replay/status`, and Scout displays whether a replay baseline is available. Inspector result browsing is the next UI increment after the STKH baseline is captured.

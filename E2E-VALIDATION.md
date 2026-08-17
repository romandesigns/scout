# Scout End-to-End Validation

`validate-e2e.ps1` is the production-facing validation harness for Scout. It complements the release validators; it does not replace unit, Rust, or web build tests.

## What it checks

- production `/healthz` and source/production version agreement;
- live SIP/BOATS ingest progress over a timed window;
- Rust hybrid queue depth and dropped-event count;
- watchdog recovery/lag telemetry;
- status, findings, attention, catalysts, and notification-preference APIs;
- Scout's ntfy provider health and optional Android notification test;
- VPS container health/resource snapshot through SSH;
- presence of the production `ix_findings_hybrid_key_time` index;
- ntfy subscriber telemetry when available;
- coarse independent recomputation of price/change metrics from detection-window candles.

## Independent detection/data comparison

The harness deliberately does **not** claim to own an independent SIP/BOATS feed. It independently recomputes candle-level price changes from the data returned by Scout's snapshot endpoint and compares those numbers with persisted finding metrics. This catches many transformation, timestamp, stale-chart, and persistence inconsistencies without reusing Scout's internal feature functions.

A true provider-vs-provider comparison requires credentials for a second independent market-data source. Add that as a separate adapter rather than describing the Scout snapshot recomputation as an independent feed.

## Notifications

Use `-TestAndroidNotification` to ask Scout's own `/api/notifications/test` endpoint to publish through its configured Android delivery path. A `PASS` proves provider acceptance; only the user/device can prove the OS actually displayed and sounded the notification.

Windows Tauri notifications are also a presentation-layer check. The harness verifies the installed desktop binary and reports that Windows toast/sound requires manual confirmation inside the installed Scout client.

## Recommended run

```powershell
pwsh -ExecutionPolicy Bypass -File .\validate-e2e.ps1 -TestAndroidNotification
```

A timestamped JSON report is written to the repository root. Reports are ignored by Git via `e2e-validation-*.json`.

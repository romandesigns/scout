# Scout Detection-Quality Audit v2

The v6.5.9 audit separates Scout's **actionable A/B cohort** from Developing/C observations and refuses to assign final quality labels without adequate forward candle coverage.

## Default run — actionable A/B only

```powershell
pwsh -ExecutionPolicy Bypass -File .\validate-detection-quality.ps1 -Limit 100
```

## Include Developing/C as a separate cohort

```powershell
pwsh -ExecutionPolicy Bypass -File .\validate-detection-quality.ps1 -Limit 200 -IncludeDeveloping
```

The evaluator forces a detection-centered historical snapshot (`historical=1`) so live-memory truncation cannot masquerade as a matured outcome. It independently recomputes point returns and MFE/MAE at 30s, 1m, 2m, 5m, and 15m, and cross-checks Scout's persisted outcome tracker.

Coverage states are explicit:

- `UNMATURED`: insufficient 5-minute forward data; no quality label is assigned.
- `PROVISIONAL_5M`: a complete 5-minute window exists but 15-minute confirmation is not yet available; the label is prefixed `PROVISIONAL_`.
- `FINAL_15M`: both 5-minute and 15-minute point/excursion data are available; aggregate quality rates use only these rows.

Reports include separate cohort and stage summaries plus timestamped JSON/CSV outputs.

This remains an independent recomputation from Scout-exposed market history, not a second external SIP provider.

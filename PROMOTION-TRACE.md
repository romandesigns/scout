# Promotion Gate Trace

v6.6.0 instruments the path from early awareness to A/B promotion without changing any production thresholds.

Every new PRE_IGNITION / ACTIVITY_WATCH finding contains `candidate_profile.promotion_trace`, including gate booleans, blocker names, quality rejection reasons, candidate age, trigger distance, extension, and a late-risk flag. Promoted findings retain the same trace plus selected stage and measured promotion delay when a first-leg candidate timestamp exists.

Run:

```powershell
pwsh -ExecutionPolicy Bypass -File .\validate-promotion-trace.ps1 -Limit 300
```

The report answers which promotion gates fail most often and whether candidates remain blocked until they are already extended. This release is observational only; it intentionally does not loosen detection or notification rules.

# Scout product and evaluation contract

This contract describes behavior enforced by current executable code. It is not a roadmap.

## Runtime purpose

Scout consumes market trades, quotes, snapshots, status events, and catalyst sources; creates and ranks bullish findings; persists their evidence and outcomes; and exposes them through its API, workstation, and notification transports. Rust and Python detector findings are associated through hybrid ticker episodes before persistence and dispatch.

## Evaluation vocabulary

- **Finding:** Any persisted detector or event result, including shadow and informational records.
- **Group A:** A non-shadow, Rank A, `CLEAN` finding whose multi-timeframe evidence is qualified when present and whose opportunity class is `FIRST_MOVE` or `SECONDARY_ENTRY`.
- **Actionable:** A Group A finding. This is a technical-quality classification; it does not by itself establish profitable notification performance.
- **Confirmed execution candidate:** A Group A finding at `IGNITION`, `BREAKOUT`, or `SURGE`.
- **Evaluating:** A technically eligible cohort without sufficient profitable completed paper outcomes.
- **Profit validated:** A matched cohort with the minimum completed sample, positive average R, and a conservative win-rate bound above its bracket break-even rate.
- **Notification eligible:** A user-facing stage that passes opportunity, profitability, preference, session, quiet-hour, deduplication, and channel gates. Verified catalyst and market-status events are exempt from the paper-profitability gate.
- **Paper-execution eligible:** A confirmed execution candidate that also passes trader enablement, regular-session, duplicate, capacity, daily-loss, tradability, and stop-geometry checks. Paper sampling is intentionally independent of notification profitability validation.

## Trading boundary

Scout Trader is optional and disabled by default. It accepts only `paper-api.alpaca.markets`, validates that the paper account is active, and submits bracket orders only after explicit activation. There is no implemented live-account execution route.

## Trust boundary

The HTTP API has no application-level user authentication. Deployments must treat every client capable of reaching the API as a trusted operator, particularly because the API can change notification and paper-trader settings. The supplied container mapping binds the service to loopback.

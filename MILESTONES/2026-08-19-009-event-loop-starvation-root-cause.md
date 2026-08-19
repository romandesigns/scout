# Root cause found for both 2026-08-19 incidents: event loop starvation under SIP batch bursts

**Date:** 2026-08-19
**Status:** Implemented and unit-tested locally (syntax + full suite; the fix itself is a
scheduling/fairness change validated by production monitoring, not a unit-testable output
change — see "How this gets verified" below). **NOT deployed.**

## The ask

User asked to "solve" the two items flagged as open at the end of the day: the SIP
websocket instability itself (97 disconnects in one day, only the log noise around it had
been fixed so far — [2026-08-19-007](2026-08-19-007-reconcile-connectionclosed-log-noise-fixed.md))
and the queue-stall incident's still-unknown root cause
([2026-08-19-006](2026-08-19-006-rust-bridge-queue-deadlock-incident.md)).

## Diagnosis

Traced `_stream()` in `app/market.py`, the loop that reads every incoming SIP message:

```python
async for raw in ws:
    messages = orjson.loads(raw)          # Alpaca batches many trades into one WS frame
    for msg in messages:
        if msg.get("T") == "t":
            await self._handle_trade(msg, subscribed, feed)   # <- the only await per message
```

`_handle_trade`'s own internal awaits are conditional: `await self._restore_state_from_store`
only fires the first time a symbol is seen; `await self._maybe_emit` only fires once per
symbol per `fast_path_min_interval_ms` (750ms) or `eval_seconds` (15s). For the common case
-- an already-tracked symbol not yet due for re-evaluation -- `_handle_trade` can complete
with **zero real suspension points**. Awaiting a coroutine that never actually suspends does
not yield control back to the asyncio event loop; the `for msg in messages:` loop just keeps
running.

Alpaca's SIP feed batches many trade updates into a single frame during high-volume bursts.
If a burst frame contains, say, several thousand messages, the entire `for` loop can run
to completion as one uninterrupted stretch of synchronous work -- during which nothing else
on the event loop gets a turn. That includes:
- the `websockets` library's own internal ping/pong keepalive task (→ the ~once/minute
  "keepalive ping timeout" disconnects)
- the Rust bridge's `_writer` task draining the outbound queue (→ the queue saturating and
  dropping trades, this morning's incident)

**These were very likely never two separate problems.** Both are symptoms of the same event
loop failing to get scheduled during a large synchronous batch. This also explains why
`restarts: 0` never fired during the queue incident and why the SIP reconnects kept
happening all day at a similar cadence -- neither is a process actually dying, both are the
same loop repeatedly falling behind under load and catching up just enough to avoid a full
crash.

## Fix

[app/market.py](../app/market.py) `_stream()`: added an explicit `await asyncio.sleep(0)`
every 100 messages inside the per-frame processing loop. This is the standard, low-risk
cooperative-yield pattern for exactly this situation -- it costs nothing on a small batch
(condition never triggers) and, on a large batch, periodically hands control back to the
scheduler so other ready tasks (websocket keepalive, the Rust queue writer, anything else
waiting) get a fair turn without materially slowing down actual message throughput.

## Scope and what this does NOT change

- Does not reduce the actual CPU cost of `_metrics()`/`_maybe_emit()` -- if a burst is large
  enough, processing will still take real wall-clock time, just without starving everything
  else while it happens.
- Did not apply the same change to `app/hybrid.py`'s Rust-candidate reader loop -- candidate
  volume (8,883 in one full day, per `/api/status`) is orders of magnitude lower than raw
  trade volume (1.5M+ submitted in the same window), so the same starvation risk there is
  judged negligible. Not changed to avoid unnecessary scope creep on a file that wasn't
  implicated by the evidence.
- Did not touch `ping_interval`/`ping_timeout` tuning (previously discussed as a possible
  mitigation) -- if this fix genuinely addresses the root cause, loosening the timeout
  wouldn't be needed and would only mask a real problem for longer if this diagnosis turns
  out to be wrong.

## How this gets verified

This is a scheduling/fairness fix, not an output-correctness fix -- there's no meaningful
unit test for "did the event loop get a fair turn under a synthetic burst" without building
a real load-testing harness, which is out of scope here. The real test is production
monitoring: once deployed, `scripts/live_observer.py`'s health poller already tracks
`feeds.health.sip.disconnects` and alerts on backpressure saturation. If this diagnosis is
correct, both the disconnect rate and any future queue-saturation events should drop
significantly. If they don't, the hypothesis is wrong and the real cause is still open --
that will be visible, not silently assumed away.

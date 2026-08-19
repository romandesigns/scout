# Log review: "Universe refresh failed" ERROR spam fixed — was noise, not the real bug

**Date:** 2026-08-19
**Status:** Fixed locally, tested, NOT deployed. Standing instruction: no commit/push/deploy
without explicit confirmation.

## What the user pasted

A ~4-minute slice of production container logs (`docker logs stockhunter-scout`), asking to
review and fix the issues. The log showed, repeating every ~45-100 seconds:

```
ERROR scout.market Universe refresh failed
Traceback ...
  File "/srv/app/market.py", line 1160, in _reconcile
    await send({"action": "unsubscribe", "trades": chunk})
  ...
websockets.exceptions.ConnectionClosedError: sent 1011 (internal error) keepalive ping timeout; no close frame received
```

plus, at roughly the same cadence, the already-known/self-healing pattern:

```
ERROR scout.market Alpaca SIP stream disconnected; retry in 2s
TimeoutError: timed out while closing connection
```

## Diagnosis

Two distinct things were happening, not one:

1. **The Alpaca SIP websocket connection is disconnecting frequently via keepalive ping
   timeout** (`ping_interval=20, ping_timeout=20` in `_stream()`) — roughly once a minute in
   this log window. `_stream()`'s own reconnect loop already handles this correctly:
   it logs the disconnect, backs off, reconnects, and resubscribes (visible in the log as
   `Alpaca SIP auth: success` / `SIP subscriptions updated` shortly after each disconnect).
   This part is working as designed. The frequency itself (~once/minute) is higher than
   ideal and points at network-path instability between the VPS and Alpaca's SIP endpoint,
   but that is not something fixable from the application code with the evidence available
   here — flagged as a separate, unresolved, likely-infrastructure-level concern below.

2. **The actual code defect**: `universe_loop()` calls `_reconcile()` against the *same
   shared* `self.ws` object independently of the `_stream()` reader loop. When the SIP
   connection is mid-disconnect (or has just died and `_stream()` hasn't yet replaced
   `self.ws` with a fresh connection), `_reconcile()`'s `ws.send()` call raises
   `websockets.exceptions.ConnectionClosed`. `_reconcile()` had no special handling for this
   -- it just re-raised, which propagated up through `universe_loop`'s
   `except Exception: log.exception("Universe refresh failed")`, logging a full ERROR-level
   stack trace **every single time**, even though:
   - the universe refresh itself had already succeeded one line earlier
     (`Universe refreshed: N symbols` always logs right before the error)
   - the condition is entirely transient and self-healing — the next
     `universe_loop` cycle (`settings.universe_refresh_seconds`, default 60s) retries
     cleanly against whatever connection `_stream()` has since re-established

   Net effect: given how often the SIP connection was flapping, **nearly every universe
   refresh cycle in this log window logged a misleading "failed" error**, when the real
   story was "reconcile skipped once because the shared socket was mid-reconnect, retried
   fine next cycle." This is pure noise that actively worked against the exact kind of live
   log review the user was just asked to do -- a genuine problem (SIP instability) buried
   under a much larger volume of false-alarm stack traces for a non-problem.

## Fix

[app/market.py](../app/market.py) `_reconcile()`: catch `websockets.exceptions.ConnectionClosed`
specifically, record it in `status["last_error"]` (still visible via internal status if
anyone wants it), and log a single quiet `INFO` line instead of letting it propagate as an
`ERROR` with a full traceback. Any *other* exception during reconcile still propagates and
still logs as an ERROR via `universe_loop` -- this only silences the one specific,
already-self-healing race condition, not real bugs.

Added `tests/test_reconcile_connection_closed.py` (2 tests): confirms a
`ConnectionClosed`-raising websocket is swallowed cleanly (no exception escapes
`_reconcile`), and confirms an unrelated exception (`RuntimeError`) still propagates as
before -- so this fix can't silently start swallowing real reconcile bugs later. Full suite:
136/136 passing (134 pre-existing + 2 new).

## Not fixed, flagged separately

The underlying SIP keepalive-ping-timeout frequency (~once/minute in this sample) is not
addressed by this fix -- only the misleading logging around it. Root cause is most likely
network-path instability (VPS ↔ Alpaca), not visible from application-level logs alone. One
possible mitigation worth considering (not applied, has real trade-offs): increasing
`ping_timeout` in `_stream()`'s `websockets.connect(...)` call would make the client more
tolerant of transient network hiccups at the cost of being slower to detect a genuinely dead
connection. Not changed without discussing the trade-off first.

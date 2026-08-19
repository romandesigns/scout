# Production incident: Rust perception bridge queue stalled, 100% trade drop, ~unknown duration

**Date:** 2026-08-19, discovered ~12:31 UTC (~8:31 AM ET, premarket), fixed ~12:38 UTC.
**Severity:** High — total detection blind spot while active, self-inflicted by an
operational gap, not a detection-logic problem.
**Status:** Mitigated (container restart). Root cause of the stall itself not yet identified.
Follow-up hardening proposed below, not yet built.

## How this was found

In response to the user asking "is it possible you can watch the application live output to
analyze how accurate it's being" — while scoping that out, polled the already-existing
`/api/status` production endpoint (`srv1170872.tail86523.ts.net:8444`) directly, three times
over 15 seconds, and found:

```
depth=50000/50000  util=1.00  backpressure=saturated
submitted=1577380 (frozen across all 3 polls)   written=1527124 (frozen across all 3 polls)
dropped=120709 -> 122026 -> 122672 (climbing)   candidates=8907 -> 8912 -> 8919 (still climbing, slowly)
last_submit_at: 227s old and not advancing        last_candidate_at: 10s old
restarts: 0 since process start (06:42 UTC, ~5h50m earlier)
```

## Diagnosis

`app/hybrid.py`'s bounded `asyncio.Queue` (`RUST_BRIDGE_QUEUE_MAX=50000`) sits between every
incoming trade tick (`submit_trade`, called from the live market feed) and the `_writer` task
that pipes them to the Rust subprocess's stdin. `submit_trade` does `queue.put_nowait()`;
on `QueueFull` it silently increments `self.dropped` and returns `False` — no exception, no
log spam by design ("Logging every dropped trade during an outage would create its own
failure mode"), only visible via `/api/status` counters.

`submitted` and `written` being *completely frozen* while `dropped` kept climbing means the
`_writer` task itself had stopped making progress — almost certainly stuck inside
`await process.stdin.drain()`, i.e. the Rust subprocess was not consuming stdin fast enough
(or at all) for some stretch of time. The Rust process was still alive (`process.wait()`
never returned, so the supervisor's own restart path — `self.restarts` — never triggered) and
apparently still emitting occasional candidates from already-buffered state, just far slower
than live market throughput. Net effect: **100% of new trade ticks were being dropped before
either Rust or Python's quality gate ever saw them**, for at least the several minutes directly
observed, and plausibly longer — there is no historical telemetry to establish exactly when
the stall began, only that queue utilization was already fully saturated at first
observation.

**This is very likely the actual explanation for the user's own live observation
("I noticed there are not actionable items at the moment") — not a symptom of any gate
threshold discussed this week.** It sits entirely upstream of the Python quality layer that
every experiment (#1-#6) this week has been tuning; a dropped trade never reaches that layer
at all.

## Fix applied (with explicit user confirmation before acting)

`docker compose restart scout` on the VPS (`/opt/apps/scout`) — a plain process restart, no
code change, no rebuild, no redeploy. User ran the command directly after confirming ("yes").

**Verified recovered** (~90s after restart): `queue_depth=0/50000`, `backpressure=healthy`,
`dropped=0`, `submitted==written` continuously (no backlog forming), `last_submit_at` age 6s
(actively flowing), SIP feed connected with 0 disconnects since restart. `tracked_states`
dropped from 1888 to 438 as expected (fresh in-memory state rebuilding) — not itself a
concern.

## What's still open

1. **Root cause of the stall itself is not identified.** Candidates: a pathological/expensive
   input in the Rust engine for a specific symbol or burst pattern, a slow memory/state leak
   inside the ~6-hour-old process eventually starving throughput, or an OS-level pipe/stdio
   stall unrelated to Rust's own logic. Needs Rust-side logging/profiling to actually
   diagnose, not attempted here — this session's fix was operational, not root-cause.
2. **No automated detection of "alive but stalled."** The supervisor's restart logic
   (`app/hybrid.py::_supervisor`) only reacts to the subprocess actually exiting
   (`process.wait()` returning). A subprocess that's alive but not draining stdin — exactly
   what happened here — produces zero automatic recovery; `restarts` stayed at 0 through the
   entire incident. This means the same failure mode can recur silently, at any hour,
   including outside active human observation, and nothing currently self-heals it.
3. **No alerting on queue saturation.** `/api/status` exposed the problem clearly once
   queried directly, but nothing pushes a notification when `backpressure == "saturated"`
   persists — it required someone to manually poll the endpoint to discover it, which is how
   this was actually found (a side effect of scoping the user's live-monitoring request, not
   a targeted investigation).

## Recommended follow-up (not yet built, proposed for next step)

A lightweight watchdog that treats "queue saturated AND `last_submit_at` not advancing for
N seconds" as equivalent to a dead process and forces a restart of the Rust subprocess (or
sends a notification if a full container restart is out of scope for the Python process
itself to trigger). This is an operational reliability fix, not a detection-quality gate
change, but it directly serves the same underlying goal every experiment this week has
served ("catch moves early") — a stalled pipeline has zero recall, worse than any gate
tuning outcome measured all week. Per standing instruction: build and test locally only,
no deploy without explicit confirmation.

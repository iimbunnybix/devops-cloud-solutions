# 001 · Database recovers from a 2-minute blip, then goes down again under a retry storm

## Problem
The orders database restarted for 2 minutes. When it came back, it
immediately hit 100% connections and fell over again — three times in a row.
The outage stretched from 2 minutes to 25.

## Root cause
All 400 service pods used the same retry policy: retry **immediately**, up
to 10 times, then retry every 1 s. When the DB returned, every pod's backlog
hit it in the same instant (a **thundering herd**). Even with plain
exponential backoff (1 s, 2 s, 4 s …) pods that failed together retry
together, so the load arrives in synchronized spikes.

## Solution
A retry helper with:

- **Exponential backoff with full jitter**: wait `random(0, min(cap, base·2^attempt))`.
  Randomness spreads retries out so the recovering service sees a smooth ramp.
- A **retry budget / max attempts** and a **total deadline**, so callers fail
  fast instead of hanging.
- Retry **only on retryable errors** (timeouts, 503) — never on 4xx validation errors.
- A small **circuit breaker**: after N consecutive failures, stop calling for a
  cool-down period and fail immediately; then let one trial request through.

The tests simulate 400 clients failing together and measure the peak load
in any 100 ms slice once the DB is back: **400 requests without jitter vs
about 12 with full jitter** (seed 0).

## Trade-offs
- Jitter makes individual retries slower on average; that is the point.
- Breaker thresholds need tuning per dependency; too sensitive = flapping.
- Server-side protection (load shedding, connection limits) is still needed.

## Run
```bash
python -m unittest -v test_solution.py
```

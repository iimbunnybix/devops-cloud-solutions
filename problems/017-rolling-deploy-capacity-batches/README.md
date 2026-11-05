# 017 · Checkout returned 503s for 20 minutes every time a release went out during the evening peak

## Problem
The web tier runs 40 instances; each one tops out at about 250 requests/second.
The deploy pipeline does a rolling update with a fixed "replace 25% at a time"
setting. Releases at 3 a.m. were always smooth, but a hotfix shipped at the
evening peak (8,000 requests/second) took 10 instances out at once. The other
30 could carry only 7,500 requests/second, latency climbed, health checks
started timing out, and checkout returned 503s for the whole rollout. The
team's workaround was "never deploy between 6 and 10 p.m.", which delayed
urgent fixes by hours.

## Root cause
The batch size was a fixed fraction of the fleet, chosen without looking at
traffic. Whether removing 10 instances is safe depends on how much load the
remaining instances must absorb at that moment, and at peak there was no spare
capacity at all. The rollout also had no health gate, so a bad build would
have been pushed to every batch.

## Solution
`solution.py` plans each batch from live numbers:

1. `instances_needed` = traffic ÷ (per-instance capacity × 80% target
   utilization), rounded up. At 8,000 rps that is 40 instances.
2. `safe_batch_size` = serving instances − instances needed, capped at a
   maximum batch size. If it is 0, the rollout **waits** for the next traffic
   reading instead of overloading the fleet, and aborts after
   `MAX_WAIT_STEPS` waits so it never hangs forever.
3. Each newly started instance must pass a health check before the rollout
   continues; the first failure halts it with the rest of the fleet still on
   the old, working version.

Replaying the incident (40 instances × 250 rps):

| Rollout | Traffic | Capacity while a batch is out | Overloaded batches |
|---|---|---|---|
| Fixed 25% | 8,000 rps peak | 7,500 rps | 4 of 4 |
| Capacity-aware, max 5 | 3 readings at 8,000, then 5,000 | waits at peak, then 8,750 rps | 0 of 8 |

## Trade-offs
- Deploys at peak are slower or wait entirely; that is the price of not
  overloading users. The common alternative is **surge** capacity
  (`maxSurge`): start new instances *before* removing old ones, which costs
  extra machines during the rollout but never reduces serving capacity.
- Per-instance capacity must come from load tests and be kept up to date; a
  wrong number makes the planner either too slow or unsafe.
- Traffic can rise during a batch; the 20% headroom absorbs normal spikes,
  not a sudden 2x surge.
- A single readiness check per instance misses slow failures (memory leaks,
  error-rate creep); combine with a canary analysis gate (problem 003).

## Run
```bash
python -m unittest -v test_solution.py
```

# 037 · Marketing emails arrived three hours late while the autoscaler said the workers were idle

## Problem
A notification service sends transactional and marketing emails from a
queue. Workers call an external email provider's API, which takes about
200 ms per message, so one worker sends about 5 emails a second (300 a
minute). A Kubernetes HPA scaled the workers on CPU with a 60% target,
between 4 and 40 pods. Normal traffic is 1,000 emails a minute.

A marketing campaign raised traffic to 3,000 emails a minute for two hours.
The HPA never added a single pod. Password-reset and order-confirmation
emails, which share the queue, arrived up to three hours late; support
tickets spiked and the campaign's time-limited offer expired before many
customers received it. Dashboards showed worker CPU at a calm 15%.

## Root cause
**The autoscaler measured the wrong resource.** The HPA formula is
`desired = current × utilisation / target`. That is right for CPU-bound
work, but these workers spend almost all their time waiting on network I/O:
even 100% busy, a worker uses about 15% CPU. So `4 × 0.15 / 0.60 = 1`, and
the HPA always wanted *fewer* pods than it had; only `min_workers` kept 4
running. The real constraint, messages waiting in the queue, was not an
input to scaling at all.

The minute-by-minute simulation in `solution.py` reproduces it: with the
CPU scaler the fleet stays at 4 workers, the backlog reaches **216,000
messages**, the worst queueing delay is **180 minutes**, and 180,000
messages are still waiting at the end of the six-hour simulated day,
three hours after the campaign ended.

## Solution
`backlog_scaler` sizes the fleet from the queue, the same idea as KEDA's
queue-length scalers or an HPA on an external "messages visible" metric:

```
desired = ceil(arrivals_per_minute / per_worker_rate
               + backlog / (per_worker_rate × target_drain_minutes))
```

- The first term keeps up with new messages; the second empties whatever is
  already waiting within `TARGET_DRAIN_MINUTES` (10).
- Neither term depends on CPU, so I/O-bound and CPU-bound workers are scaled
  the same way.
- The result is clamped to `[min_workers, max_workers]`, so a huge backlog
  cannot request more pods than the provider's rate limit or the database
  can handle.

In the same scenario the fleet goes from 4 to **11 workers** one minute
after the peak starts, the backlog never exceeds **1,800 messages** (one
minute of overflow), the worst delay is **1.5 minutes**, and the fleet
returns to 4 workers when the campaign ends.

## Trade-offs
- `MESSAGES_PER_WORKER_PER_MINUTE` is an estimate. If the email provider
  slows down, each worker does less and the scaler under-provisions; measure
  the real processing rate (messages acked per worker per minute) and use
  that, or also scale on the age of the oldest message.
- More workers mean more calls to the provider. Cap `max_workers` at what
  its rate limit allows, otherwise scaling turns into HTTP 429s and retries.
- The scaler drops from 10 to 4 workers in one step when traffic falls; add
  a scale-down stabilization window (see problem 008) to avoid flapping on
  bursty traffic.
- Mixing urgent and bulk emails in one queue is the other half of the
  incident: separate queues (or priorities) keep password resets fast even
  when the bulk queue is behind.
- The simulation ignores pod start-up time; real new workers take tens of
  seconds to start, which adds slightly to the first minute's backlog.

## Run
```bash
python -m unittest -v test_solution.py
```

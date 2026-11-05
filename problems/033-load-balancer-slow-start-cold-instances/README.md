# 033 · Every scale-out and every deploy caused a p99 latency spike and a burst of timeouts on the newest instances

## Problem
A product API runs behind an internal load balancer that spreads traffic
evenly over every instance that passes its health check. At peak it
serves about 1,000 requests/s on ten instances.

Each time the autoscaler added an instance, and on every deploy, the
latency dashboard showed the same shape: p99 latency spiked for roughly
half a minute, and clients logged timeouts.
The timeouts all came from the instances that had just started. The
fleet as a whole had plenty of headroom, so scaling out (meant to *protect*
latency) actually caused small incidents, and the team started delaying
deploys to quiet hours.

## Root cause
A new instance is healthy long before it is fast: its local caches are
empty, its connection pools are not yet open and its hot code paths are
not yet JIT-compiled. In the simulation a fresh instance can serve 15
requests/s (10% of a warm instance's 150 requests/s), reaching full speed
linearly over 60 s.

The load balancer gave it a full equal share from the first second:
**100 requests/s** to an instance able to serve 15. The excess queued
past the client deadline. With nine warm instances and one new one, the
naive balancer produced **1,663 timeouts** spread over the first **38
seconds**, until the instance's capacity caught up with its share.

## Solution
`SlowStartBalancer` in `solution.py` adds **slow-start** to the balancer:

1. Each backend records when it joined. Its weight is
   `floor + (1 - floor) * age / warmup` and reaches full weight at the end
   of the warm-up window (`floor` = 0.1, `warmup` = 60 s). The floor is
   above zero on purpose: the instance needs real traffic to warm up.
2. A **smooth weighted round robin** (the algorithm nginx uses) turns the
   weights into picks. It is deterministic, so tests can assert exact
   counts, and it interleaves picks evenly instead of sending bursts.
3. A restarted instance re-registers and ramps again.

The cold instance's share, out of 1,000 requests/s:

| Second after joining | Naive | Slow-start | Instance capacity |
|---|---|---|---|
| 0  | 100 | 11 | 15 |
| 30 | 100 | 58 | 82.5 |
| 60 | 100 | 100 | 150 |

With slow-start there are **0 timeouts**. The nine warm instances carry
the difference: at most 110 requests/s each, below their 150 capacity.

## Trade-offs
- The ramp is a guess at the warm-up curve. Too short and the spike comes
  back; too long and the new capacity is wasted for longer, which hurts
  when you are scaling out *because* the fleet is overloaded. Measure how
  long a new instance takes to reach normal latency and set the window to
  that.
- Slow-start only shifts load onto warm instances. If the whole fleet
  starts at once (cold start of a region, or a deploy that replaces every
  instance together) all weights are equal and nothing is gained; rolling
  deploys in small batches (problem 017) avoid that.
- Alternatives and complements: warm the instance before it receives
  traffic (pre-load caches, send synthetic requests, then mark it ready),
  or balance by outstanding requests (least-request), which reacts to a
  slow instance without needing to know it is new. Managed balancers offer
  this built in, e.g. AWS ALB target group "slow start duration", HAProxy
  `slowstart` and Envoy's `slow_start_config`.
- The capacity model here is linear and per-second; real warm-up is
  noisier, so leave headroom on the warm instances as well.

## Run
```bash
python -m unittest -v test_solution.py
```

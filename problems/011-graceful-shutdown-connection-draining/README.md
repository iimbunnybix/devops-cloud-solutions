# 011 · Every deploy caused a burst of 502s, so the team stopped deploying during business hours

## Problem
A checkout API runs as 3 pods on Kubernetes behind an ingress, serving about
200 requests/second. Each rolling deploy produced a short spike of 502 and 503
errors on the load balancer: a few hundred failed requests per deploy,
including some report downloads that were cut off halfway. The errors were
small as a percentage but always visible on the dashboard, and customers hit
failed payments at checkout. The team's workaround was to deploy only at
night, which slowed every fix and made deploys larger and riskier.

## Root cause
**The app exited as soon as it received SIGTERM, but the load balancer kept
sending it traffic for a few more seconds.** When Kubernetes deletes a pod,
it sends SIGTERM and removes the pod from the Service endpoints *in parallel*.
The endpoint removal has to propagate to kube-proxy on every node and to the
ingress controller, which takes seconds. So:

- requests already being processed were killed with the process (502,
  connection reset);
- new requests routed to the pod during propagation found nothing listening
  (502/503, connection refused).

The simulation in `solution.py` (3 pods, 200 rps, 3 s endpoint propagation,
95% of requests 50–500 ms and 5% report downloads of 2–8 s) shows, for **one**
pod replaced:

| Shutdown behaviour | Refused | Cut off | Failed |
|---|---|---|---|
| Exit on SIGTERM (original) | 200 | 38 | **238** |
| Drain only (no pre-stop delay) | 200 | 0 | 200 |
| Pre-stop delay 5 s only (no drain) | 0 | 8 | 8 |
| Pre-stop 2 s (shorter than propagation) + drain | 66 | 0 | 66 |
| **Pre-stop 5 s + drain up to 20 s** | 0 | 0 | **0** |

A rolling deploy of 3 pods repeats this three times.

## Solution
A shutdown sequence, implemented by `DrainController` (to embed in the
server) and modelled by `ShutdownPolicy` / `simulate_rolling_restart`:

1. **On SIGTERM, fail readiness but keep serving** for `pre_stop_delay`
   (5 s), longer than endpoint propagation. In Kubernetes this is often a
   `preStop` hook (`sleep 5`) so the delay happens before SIGTERM reaches
   the app.
2. **Stop accepting** new connections.
3. **Drain:** wait for in-flight requests to finish, up to
   `drain_timeout` (20 s), and exit as soon as they are done (in the example
   run the pod exited about 10 s after SIGTERM).
4. **Exit before SIGKILL:** `pre_stop_delay + drain_timeout` must be below
   `terminationGracePeriodSeconds` (30 s default). `check_timings` flags
   settings that break any of these rules.

Middleware should wrap each request in `with controller.track() as accepted:`
(return 503 when `accepted` is false). Only accepted requests are counted
down, and an unmatched `end_request()` raises instead of letting the
in-flight count reach zero while real requests are still running.

The tests reproduce each failure mode, check zero failures across 10 seeds,
show that the grace period caps shutdown (a 40 s request is still killed at
30 s), and exercise `DrainController` with a fake clock and multiple threads.

## Trade-offs
- Deploys get slower: each pod now takes up to `pre_stop + drain` seconds to
  stop. With `maxSurge` replacement pods start in parallel, so capacity is not
  reduced.
- The right pre-stop delay depends on your LB; cloud load balancers with
  their own deregistration delay (e.g. an ALB target group's
  `deregistration_delay`, 300 s by default) need the pod to outlive that
  delay or to have it reduced.
- Long-lived connections (WebSockets, gRPC streams, HTTP keep-alive) need
  extra handling: send `Connection: close` / GOAWAY during drain so clients
  reconnect elsewhere.
- Requests longer than any reasonable drain timeout (large exports) should be
  moved to background jobs; no shutdown policy can save a 40-minute request.
- Clients should still retry idempotent requests on 502/503, since pods also
  die for reasons other than deploys.

## Run
```bash
python -m unittest -v test_solution.py
```

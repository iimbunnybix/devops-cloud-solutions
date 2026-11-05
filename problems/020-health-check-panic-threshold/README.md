# 020 · Every page on the site returned 503 for 40 seconds while all web servers were actually up

## Problem
A storefront runs 10 web servers behind a load balancer that health-checks
each one every 5 seconds and removes any server whose check fails. The
`/healthz` endpoint also pinged the shared session cache. During a routine
40-second failover of that cache, every server failed its check at the same
moment, the load balancer removed all 10, and every request — including
product pages that never touch the session cache — got a 503 from the load
balancer itself. Monitoring showed 100% errors and zero load on the servers.

## Root cause
Two things combined:
1. A single failed check ejected a server, so one slow probe was enough.
2. The load balancer trusted health checks unconditionally. A failure in a
   dependency **shared by all backends** makes every check fail together, and
   "route only to healthy backends" then means "route to nobody". The health
   signal was wrong, but the load balancer had no way to notice.

## Solution
`LoadBalancer` in `solution.py` adds the two guards that proxies such as
Envoy and HAProxy provide:

- **Consecutive thresholds**: a backend is ejected after 3 failed checks in a
  row and re-added after 2 passed checks in a row, so one slow probe or a
  short blip does not change the pool.
- **Panic threshold (50%)**: if fewer than half the backends are healthy, the
  load balancer assumes the health data is unreliable and routes to **all**
  backends. When all 10 fail together it keeps sending traffic to all 10;
  when 1 truly dies it is still removed (9/10 healthy is above 50%). Exactly
  5/10 healthy stays out of panic; 4/10 enters it.

The naive rule (`naive_routable_backends`) is kept for comparison; the tests
show it returns an empty pool after one round of shared failures.

Also fix the check itself: a liveness-style `/healthz` should test only what
the server alone controls (see problem 013); the panic threshold is the
safety net for when a shared failure slips in anyway.

## Trade-offs
- In panic mode some requests go to backends that really are broken. That is
  the point — partial failure beats a guaranteed total outage — but if 6 of 10
  servers are truly dead, about 60% of requests fail instead of overloading 4.
- The thresholds delay ejection of a truly dead server by 3 check intervals
  (15 s here); outlier detection on real request errors reacts faster.
- 50% is a default, not a law. Pools that can run on a small fraction of
  capacity can use a lower threshold; tiny pools (2-3 backends) may prefer
  failing open only when zero are healthy.

## Run
```bash
python -m unittest -v test_solution.py
```

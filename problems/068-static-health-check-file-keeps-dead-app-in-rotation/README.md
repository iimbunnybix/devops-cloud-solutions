# 068 · One web server returned 502 for 10% of all requests for two hours while the load balancer reported every target healthy

## Problem
A content site runs 10 instances behind a cloud load balancer. Each
instance runs nginx in front of the application process. One night the
application on one instance crashed after running out of memory; nginx kept
running.

For two hours, about one request in ten failed with **502 Bad Gateway**.
The load balancer console showed **10/10 healthy targets**, the
target-health alarm stayed green, and the autoscaling group did not replace
the instance, because as far as it knew nothing was wrong. The on-call
engineer found it from customer reports and an error-rate graph, then had
to find which instance was bad by reading nginx logs on each one.

## Root cause
The health check requested `/health.html`, a **static file that nginx
served from disk** (`naive_health_check`). It proved that nginx was up and
the disk was readable. It said nothing about the application that serves
every real request. With the app down, the check still returned 200, the
target never reached the failure threshold, and round robin kept sending it
its share of traffic.

In the simulation (10 instances, one crashed app, 600 requests per
10-second check interval for 5 minutes), **1,800 of 18,000** requests fail
(exactly 10%), and the crashed instance is still in rotation at the end.

## Solution
Point the load balancer at **`/healthz`, which nginx proxies to the app**
(`app_health_check`):

1. If nginx cannot reach the app process, the check gets the same **502** a
   user would.
2. The app itself answers **503** while it is still warming up or is
   draining for shutdown, so it is added only when ready and removed
   before it stops.
3. Otherwise **200**. The endpoint deliberately does not check the
   database or other shared dependencies: if it did, one database blip
   would fail every instance at once (see problem 013).

With the usual thresholds (3 failures to remove, 2 successes to re-add),
the crashed instance is out after the third check round. In the same
simulation, errors drop from 1,800 to **120** (the first two intervals) and
stop. The load balancer also **fails open**: if every target is unhealthy
it routes to all of them rather than to none, which is how most cloud load
balancers behave (see problem 020).

## Trade-offs
- The check now costs an app request every interval from every load
  balancer node. Keep `/healthz` cheap (no database, no authentication,
  no logging per call) so it cannot become load itself.
- A process that is up but stuck (deadlock, exhausted worker pool) may
  still answer a lightweight endpoint if it runs on a separate thread.
  Serving `/healthz` through the same worker pool as real requests catches
  that, at the cost of failing checks when the pool is briefly saturated.
- Detection is not instant: with a 10 s interval and a threshold of 3, a
  dead instance takes up to about 30 s to remove. Shorter intervals react
  faster but flap more on brief pauses (garbage collection, deploys).
- Health checks remove bad targets; they do not tell anyone. Alert on the
  unhealthy-target count and on 5xx rate per target, and let autoscaling
  replace instances that stay unhealthy.

## Run
```bash
python -m unittest -v test_solution.py
```

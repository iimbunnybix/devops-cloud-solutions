# 060 · Half of all checkouts failed for an hour, yet the availability dashboard read 99.5% and the SLO alert never fired

## Problem
A shop's API has a 99% availability SLO, measured from the access logs of
its load balancer as "requests that were not 5xx / all requests". During a
payment-provider incident, `/checkout` returned 503 to half of the customers
for an hour. Support heard about it from customers on social media; the SLO
dashboard showed 99.5% and the alert stayed green the whole time.

In the test hour there are 90,000 health checks, 9,000 product-page requests
and 1,000 checkouts, 500 of which failed. The original SLI reads
**99.5%**: 500 failures out of 100,000 log lines.

## Root cause
Most lines in the access log were not users. Every load balancer node checks
every target every few seconds, and uptime probes hit the service too. These
requests are cheap, frequent and almost always succeed, so they made up 90%
of the denominator and pushed the ratio towards 100% no matter what real
callers saw. A single broken route is also invisible in a service-wide ratio
when most user traffic goes to other routes.

## Solution
- **Exclude synthetic traffic before computing the SLI.** `is_synthetic`
  drops requests to probe paths (`/healthz`, `/readyz`, ...) or tagged by the
  edge proxy with a known probe source (`lb-health-check`, `uptime-probe`,
  `kubelet`). The same hour now reads **95%**, below the SLO, and the alert
  fires.
- **Report per route as well.** `availability_by_route` shows `/checkout`
  at **50%** and `/products` at 100%, so the dashboard points at the broken
  endpoint.
- Only 5xx count as bad: 4xx are caller errors. A slice with no user
  traffic returns `None` instead of 100%.

## Trade-offs
- The classification must be maintained: a new probe path or a monitoring
  vendor with a new user agent will dilute the SLI again. Tag synthetic
  traffic at the edge (a header or source field) rather than guessing by path.
- Health checks still matter, just not for the user SLI: alert on them
  separately (target health), and treat "no user traffic" as its own
  no-data alert rather than as healthy.
- Per-route SLIs on low-traffic routes are noisy; use them for dashboards
  and diagnosis, or weight routes by business importance, rather than paging
  on every small route.
- Server-side logs never see requests that failed before reaching the
  balancer (DNS, TLS, client network). Real-user monitoring from the app is
  the complementary signal.

## Run
```bash
python -m unittest -v test_solution.py
```

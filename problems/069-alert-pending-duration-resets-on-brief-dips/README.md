# 069 · Checkout errors stayed near 8% for two hours and the error-rate alert never fired

## Problem
An online shop's checkout service had one main alert: error rate above 5%
**for 10 minutes** (a Prometheus-style rule with `for: 10m`), evaluated
once a minute. The `for` clause was added after a noisy month, to stop
pages for short spikes.

During a partial outage of a payment dependency, about 8% of checkouts
failed for two hours. Client retries and a burst of cache hits made the
error rate drop to about 3% for one minute in every six. The alert never
fired. Support escalated after customers complained on social media, and
the on-call engineer found the dashboard full of red with the alert still
"pending".

## Root cause
A pending duration needs **consecutive** breaching evaluations. Any single
evaluation under the threshold resets the pending timer to zero
(`naive_pending_alert`). An outage that dips under the threshold more
often than every 10 minutes can therefore last forever without paging.

In the simulation (30 healthy minutes, 120 minutes at 8% with a dip to 3%
every sixth minute, 30 healthy minutes), the `for: 10m` rule sends
**0 pages**. The usual "quick fix", shortening the pending time to 3
minutes, sends **20 pages** for the same single incident, which is the
noise the `for` clause was added to stop.

## Solution
`WindowedAlert` decides on the **share of breaching minutes in a sliding
10-minute window** instead of an unbroken run:

- It **fires** when at least 80% of the last 10 evaluations breached
  (8 of 10), so one or two good minutes cannot hide an outage.
- It **resolves** only when at most 20% breached. The gap between the two
  levels (hysteresis) stops it flapping between firing and resolved.
- It waits for a **full window** after a restart, so it never pages on a
  couple of minutes of data.

On the same incident it sends **one page, 9 minutes into the outage**. On a
steady outage it pages after 8 minutes, two minutes earlier than the
original rule (10). A 3-minute spike still sends no page.

In PromQL the same idea is an `avg_over_time` over a 0/1 breach series, for
example `avg_over_time((rate_errors > bool 0.05)[10m:1m]) >= 0.8`, combined
with `keep_firing_for` to delay resolution.

## Trade-offs
- A fraction rule is slower than a short `for` for very sharp, total
  outages. Pair it with a separate high-threshold, short-window rule
  (for example above 50% for 2 minutes) for hard down.
- The 80% / 20% levels are a choice: a lower fire level pages sooner on
  flappy incidents but lets more noise through.
- Burn-rate alerts on an SLO (problem 002) handle this more generally by
  measuring the budget consumed rather than counting breaching minutes.
  This rule is the small change when a team still has threshold alerts.
- If the rate dips because traffic itself drops, the rule still sees
  noisy ratios; low-traffic handling is covered in problem 039.

## Run
```bash
python -m unittest -v test_solution.py
```

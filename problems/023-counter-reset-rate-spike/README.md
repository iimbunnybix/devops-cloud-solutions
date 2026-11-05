# 023 · Error-rate alert stayed quiet while a restarted pod failed half its requests, and the traffic panel showed a negative request rate

## Problem
The checkout API's request and error counts come from two cumulative
counters, `http_requests_total` and `http_errors_total`, scraped every 15
seconds. A home-grown alerting job computed "requests in the last 5 minutes"
as the last scraped value minus the first one, and paged when more than 5% of
requests failed.

During a deploy a pod that had served 1,000,000 requests restarted with a bad
config. After the restart it failed 50% of its requests, but nobody was
paged. At the same time the traffic dashboard showed a large *negative* rate
and a "traffic dropped to zero" alert fired for a service that was in fact
busy. On-call muted the noisy alert and missed the real one for 25 minutes.

## Root cause
A counter only goes up while its process lives. A restart sets it back to
zero. `last - first` over a window that contains a restart subtracts the old
lifetime total from the small new value:

| 5-minute window (20 scrapes, restart halfway) | `last − first` (`naive_increase`) | reset-aware |
|---|---|---|
| requests | −986,500 | 28,500 |
| errors | −2,515 | 7,635 |
| error ratio | 0.25% (no page) | 26.8% (page) |

Dividing one negative number by another gave a small positive ratio, so the
error alert stayed silent while the restarted pod was failing.

## Solution
`reset_aware_increase` walks the samples in time order and adds up each
step. When a value is **lower** than the one before, it treats that as a
reset: the process restarted and counted from 0 to the new value, so the whole
new value is new increase. This is the same rule Prometheus uses in
`increase()` and `rate()`. `per_second_rate` divides that by the time the
samples cover, and `should_page_on_errors` uses the reset-aware increases of
both counters. A window with no traffic returns no ratio instead of dividing
by zero.

## Trade-offs
- Increments between the last scrape before a restart and the restart itself
  are lost; the counter never reported them. Short scrape intervals keep this
  small.
- Any decrease is treated as a reset. A counter that is reported wrongly
  (for example a gauge mislabelled as a counter) will produce inflated
  increases; only use this on real monotonic counters.
- Prometheus also extrapolates the increase to the edges of the window; this
  version reports only what the samples cover, which is simpler but slightly
  low for sparse samples.
- Aggregate per process before summing across pods (`sum(rate(...))`, not
  `rate(sum(...))`): summing raw counters first hides one pod's reset inside
  the others' growth and makes it undetectable.

## Run
```bash
python -m unittest -v test_solution.py
```

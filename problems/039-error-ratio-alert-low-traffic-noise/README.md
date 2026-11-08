# 039 · On-call was woken at 3 a.m. for "50% errors" when only two requests had arrived

## Problem
A checkout API serves up to 12,000 requests a minute at lunchtime, about
2,000 in the evening and almost nothing at night: one request every five
minutes between midnight and 6 a.m. Its paging alert was "more than 5% of
requests failed over the last 10 minutes".

At 03:05 one request timed out. The only other request in the window
succeeded, so the dashboard showed **50% errors** and the alert paged the
on-call engineer, who found nothing wrong. This happened several nights a
week; the team started ignoring the alert.

The team then switched to a raw count, "more than N failed requests in 10
minutes", and found there is no good N. At peak a healthy service still fails
0.2% of requests (client disconnects, the odd timeout), which is **240 failed
requests per 10 minutes**. With N = 50 the alert fired for **545 minutes**
straight on a perfectly healthy day (09:02 until after 18:00). With N = 300 it
was quiet at peak, but at 22:15 a bad cache node made **20% of requests fail
for 30 minutes**. Traffic was only 100 requests a minute by then, so the
window never held more than **200 failed requests**, the alert never fired,
and customers found out first.

## Root cause
**A ratio needs a sample size, and a count needs a scale.** With two requests
the error ratio can only be 0%, 50% or 100%; it says nothing about the
service. A raw count ignores traffic entirely: the same number is an
emergency at 10 p.m. and normal background noise at lunchtime. Neither signal
alone fits a service whose traffic varies by more than 1,000x over a day.

## Solution
`volume_aware_alert` keeps the ratio (it is what users experience) but only
trusts it when the window has enough data:

1. **Absolute floor**: fewer than `MIN_FAILED_REQUESTS` (10) failed requests
   in the window never page, whatever the ratio.
2. **Ratio with minimum volume**: with at least `MIN_REQUESTS` (200)
   requests in the window, page when more than 5% fail.
3. **Low-traffic rule**: with fewer requests than that, page only when at
   least half of them fail, so a real night-time outage still pages (after 10
   failed requests, which at one request a minute is 10 minutes).

Counts come from `SlidingWindowCounter`, which keeps per-minute buckets for
the last 10 minutes and drops older ones. It reads time from an injected
clock, so `replay` runs a whole simulated day in milliseconds.

On the same simulated day (`example_day`):

| Alert | Pages | When |
|---|---|---|
| ratio > 5%, no volume check | 2 | 03:05 (false), 22:17 |
| more than 50 failed | 2 | 09:02 (false, fires 545 min), 22:17 |
| more than 300 failed | 0 | incident missed |
| `volume_aware_alert` | 1 | 22:17 (the incident) |

## Trade-offs
- At night the alert is slower and less sensitive: a 30% error rate on 10
  requests does not page. That is deliberate; with that little traffic few
  users are affected, and a synthetic probe (a fake checkout every minute)
  is a better way to detect a night-time outage quickly.
- `MIN_REQUESTS` and `MIN_FAILED_REQUESTS` depend on the service's traffic.
  Choose them from real traffic graphs, and review them when traffic changes.
- This is a threshold alert. For SLO-based paging, multi-window burn-rate
  alerts (problem 002) are better, and they need the same minimum-volume
  guard.
- An empty window never pages; whether missing data should page is a
  separate question (problem 031).
- Per-minute buckets make the window step once a minute; finer buckets cost
  more memory but react a little faster.

## Run
```bash
python -m unittest -v test_solution.py
```

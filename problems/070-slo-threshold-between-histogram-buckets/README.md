# 070 · Users reported a slow search API for a week while the 300 ms latency SLO dashboard stayed at 99.6%

## Problem
A search API has a latency SLO: **99% of requests within 300 ms**. The SLO
dashboard and burn-rate alerts are computed from the service's latency
histogram, which uses buckets at 50, 100, 250 and 500 ms, 1, 2.5 and 5 s
(the layout it shipped with, years before the SLO was written).

A release added a synchronous call to a ranking service on cache misses,
about 8% of requests, costing 150 ms each. Product teams and customers
reported that search "felt slow" for a week. The SLO dashboard showed the
same **99.6%** as before the release and no budget burn, so the reports
were closed as "not reproducible".

## Root cause
There was **no histogram bucket at 300 ms**. A histogram only knows how
many requests fell into each bucket, not where inside it, so the SLO
query used the nearest bucket at or above the threshold, `le="0.5"`
(`naive_good_fraction`). Every request between 300 and 500 ms was counted
as meeting a 300 ms SLO, which is exactly where the regression put them.

In the simulation (100,000 requests, seed 70):

| | Real share within 300 ms | Dashboard (`le="0.5"`) |
|---|---|---|
| Before the release | 99.61% | 99.61% |
| After the release | **95.55%** | **99.61%** (unchanged) |

Linear interpolation inside the 250-500 ms bucket (what
`histogram_quantile` does) is no fix: it assumes requests are spread evenly
in the bucket, so it reports **90.37% for the healthy service** (a false
page) and 86.71% after the release. Neither number is the truth.

## Solution
Make the SLO threshold a **bucket boundary** and refuse to measure an SLO
the histogram cannot answer:

- `SLO_ALIGNED_BUCKETS` adds `le="0.3"`. The count at that bound is exact,
  so `good_fraction` gives **95.55% after the release**, identical to the
  ground truth from raw latencies.
- `require_slo_bucket` raises `SloBucketMissing` when no boundary equals
  the threshold. Run it in CI on the SLO definitions, so the next SLO
  cannot quietly read the wrong bucket.
- A latency exactly at 300 ms counts as good, matching Prometheus's `le`
  (less than or equal) semantics.
- With no traffic the SLO is treated as met rather than dividing by zero.

The regression now burns error budget at **4.45 times** the sustainable
rate (`error_budget_used`), which pages through the normal burn-rate
alerts (problem 002), instead of the 0.39 times the dashboard showed.

## Trade-offs
- Every extra bucket is one more time series per label combination.
  Add the boundaries your SLOs need, not a fine grid (see problem 041 on
  cardinality).
- Changing bucket boundaries breaks queries that sum old and new series
  over the change. Treat the first window after the change as unknown.
- Each threshold you may want later needs its own boundary. Native
  (exponential) histograms in recent Prometheus versions bound the
  error for any threshold, at the cost of newer tooling.
- Recording "good" and "total" counters directly in the service (a request
  is good if it finished within 300 ms) is exact too and cheaper, but
  fixes the threshold in code.

## Run
```bash
python -m unittest -v test_solution.py
```

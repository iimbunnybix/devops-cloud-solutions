# 012 · The latency dashboard said p99 was 275 ms while customers on one host waited over two seconds

## Problem
An API fleet of 20 hosts has a latency SLO of p99 < 300 ms. The fleet
dashboard showed p99 at about 275 ms, green, for two days. Meanwhile support
tickets said the app "randomly hangs for a couple of seconds", and a large
customer's synthetic monitor showed multi-second requests. The cause turned
out to be one host with a degrading disk serving every request in 1.2–2.5 s.
It got 1/20 of the traffic, so about 5% of all requests were slow, but no
alert fired.

## Root cause
**The dashboard averaged per-host p99 values.** Each host exported its own
p99 as a gauge; the dashboard query then averaged them across hosts (and the
hourly view averaged per-minute p99s again). Percentiles cannot be averaged:
the fleet's p99 is the value 99% of *all requests* are below, and averaging
summaries throws away the information needed to compute it.

Reproduction (`fleet()` in the tests: 19 healthy hosts with ~50 ms median and
~160 ms p99, one host at 1.2–2.5 s, 10,000 requests each):

| Method | Fleet p99 |
|---|---|
| Average of the 20 host p99s (dashboard) | **275 ms** (under the SLO) |
| Traffic-weighted average of host p99s | 264 ms with one host at 3x traffic, still under |
| True p99 over all 200,000 requests | **2,234 ms** |
| Merged histograms (fix) | 2,248 ms (0.6% off) |

Weighting by traffic, the first attempted fix, does not help: the error is in
averaging percentiles at all, not in the weights.

## Solution
Ship **mergeable histograms**, not percentiles. `LogHistogram` in
`solution.py`:

- counts requests in logarithmic buckets: bucket *i* covers
  `(γ^(i-1), γ^i]` with `γ = (1+α)/(1-α)`. Reporting the bucket's midpoint is
  within a relative error `α` (1% by default) of any value in it, whatever the
  latency range above a 1 µs floor (values at or below the floor are reported
  as 0, so sub-millisecond cache hits are still measured accurately);
- **merges** by adding counts per bucket, so hosts, minutes and regions can be
  combined in any order and the result equals one histogram of all requests
  (tested);
- computes any percentile once, from the merged counts;
- serializes to a small dict (37 buckets for the slow host's 10,000 requests,
  238 for the whole fleet).

The tests reproduce both wrong aggregations, check p50–p99.9 of the merged
histogram against exact values (within 1%), verify the error bound on random
data, and cover merging, serialization and edge cases (zeros, empty
histograms, mismatched layouts).

## Trade-offs
- Histograms cost more to store than one gauge per host (tens to hundreds of
  buckets), still far less than raw samples.
- Accuracy is relative, set by `α`: 1% is plenty for latency dashboards; a
  smaller `α` means more buckets.
- In practice use what your stack provides: Prometheus histograms with
  `histogram_quantile(0.99, sum by (le) (rate(..._bucket[5m])))` (sum the
  buckets first, then take the quantile), Prometheus native histograms,
  HdrHistogram, DDSketch or t-digest. Prometheus *summaries* compute quantiles
  on each instance and cannot be aggregated across instances.
- Classic fixed buckets (`le="0.1"`, `"0.25"`, ...) only give accurate
  percentiles if bucket boundaries are close around your SLO threshold.
- A fleet p99 can still hide a single bad host from small-traffic customers;
  also alert on per-host outliers (e.g. a host's p99 far above the fleet's).

## Run
```bash
python -m unittest -v test_solution.py
```

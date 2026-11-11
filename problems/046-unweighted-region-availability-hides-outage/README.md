# 046 · The status page said 90% available while 40% of requests in our busiest region were failing

## Problem
A SaaS API runs in four regions. `us-east-1` serves about 86% of traffic
(10 million requests in the incident hour); `eu-west-1`, `ap-south-1` and
`sa-east-1` serve 1 million, 500,000 and 100,000. The status page and the
executive SLO dashboard show one "global availability" number.

During a bad deploy in `us-east-1`, 40% of its requests failed for an hour.
The dashboard showed **89.99%**: bad, but leadership read it as "about one
in ten requests", and the status page stayed at "degraded performance".
The real figure was that **one in three** requests worldwide failed. The
opposite also happened: when a new region, `me-central-1`, had 100 errors in
2,000 requests, the global number dropped to **98.33%** and paged the on-call
engineer at night, although 99.999% of all requests had succeeded.

## Root cause
The dashboard averaged the per-region success percentages:
`(60% + 99.99% + 99.99% + 99.99%) / 4 = 89.99%`. An average of ratios gives
every region the same weight regardless of how many requests it served, so it
answers "how is the typical region doing?", not "what fraction of requests
succeeded?". Big regions are under-weighted and small ones over-weighted.

## Solution
`solution.py` keeps the raw counts and divides once:

1. **`fleet_availability`** sums good and total requests across regions:
   7,599,840 / 11,600,000 = **65.52%** for the outage, and 99.999% for the
   tiny-region blip. Every request counts once, so the number means what
   users experienced.
2. **`regions_below_slo`** checks each region against the 99.9% SLO on its own
   and lists breaches worst first. A traffic-weighted number alone would hide
   a small region that is completely down, so the report shows both.
3. **`availability_report`** says the SLO is met only if the fleet number is at
   or above target *and* no region is in breach. Regions with no traffic are
   listed as "no data" instead of being counted as 100% (or 0%).

## Trade-offs
- Request-weighted availability is dominated by the biggest region; a small
  region can be down entirely and barely move it. That is why per-region
  breaches are reported separately; some teams also weight by revenue or by
  customer instead of by request.
- It needs good/total counts, not percentages. If upstream systems only export
  percentages, they must also export the request count to weight correctly.
- Counting by requests lets a retrying client inflate both good and total;
  counting by user sessions or by time slices ("good minutes") is an
  alternative with different blind spots.
- Like any SLI, the result depends on what is counted as "good": 5xx only,
  or also slow responses and 429s.

## Run
```bash
python -m unittest -v test_solution.py
```

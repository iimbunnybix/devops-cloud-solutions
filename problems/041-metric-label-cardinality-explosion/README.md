# 041 · Every dashboard and alert went blank an hour after a release added user_id to a request metric

## Problem
A company runs one shared Prometheus server for about 40 services. The API
gateway exports a request counter, `http_requests_total`, with the labels
`route`, `method` and `status`: 10 routes × 2 methods × 5 statuses, so at most
100 time series.

To debug a customer complaint, a developer added `user_id` as a fourth label
and shipped it in the regular Thursday release. Within an hour Prometheus was
OOM-killed, restarted, replayed its write-ahead log, and was killed again.
Every team's dashboards went blank and every alert went silent (see 031: no
data is not healthy) until the release was rolled back. Nobody was paged for
the outage itself, because the alerting depended on the server that was down.

## Root cause
A metrics backend stores **one time series per unique combination of label
values**, and its memory and CPU grow with the number of active series. A
label with unbounded values (user ids, request ids, raw URLs, emails) multiplies
the series count by the number of distinct values.

In the simulation (100,000 requests in one hour from 40,000 users, seeded):

| Labels | Series |
|---|---|
| route, method, status | **100** |
| route, method, status, user_id | **98,757** |

That is almost one new series per request: about 990 times more for one
counter. The client library accepted any label value, so nothing stopped it
before it reached the shared server.

## Solution
`solution.py` adds a guard in the metrics client, inside the service, so a bad
label cannot leave the process:

1. **Allow-list of labels per metric.** `GuardedRegistry` keeps only the
   labels declared for that metric and records the dropped label names in
   `dropped_label_names` (export that as its own small metric, or log it once,
   so the developer learns why the label is missing). With the allow-list,
   the release above still produces **100** series, and the counter total
   is still exactly 100,000.
2. **Series budget per metric.** Even an allowed label can turn out to be
   unbounded. Once a metric has `max_series_per_metric - 1` real series, any
   *new* label combination is counted in a single overflow series whose label
   values are all `__overflow__`. Existing series keep counting normally. With
   `user_id` allowed and a budget of 500, the metric stops at **500** series;
   **99,487** samples land in the overflow series, and the total is still
   100,000, so error ratios and request rates remain correct.
   `overflowed_samples` per metric is the signal to alert on.

Per-user detail belongs in logs or traces (which are built for
high-cardinality data), not in metric labels.

## Trade-offs
- The overflow series loses detail: once the budget is full, a new route that
  appears later is merged into `__overflow__` instead of getting its own
  series. Size the budget well above the expected cardinality and alert on
  `overflowed_samples` so it is noticed.
- The budget is first come, first served for the life of the process; it
  never forgets old series. A long-running process with slowly changing
  values (for example pod names) may need series expiry instead.
- This only protects against well-behaved clients using this library.
  Server-side limits (Prometheus `sample_limit` / `label_limit` per scrape
  job, or per-tenant series limits in Mimir/Cortex) are the backstop for
  everything else; they drop the whole scrape or the excess series instead of
  keeping totals correct.
- A CI check that rejects new labels named like `*_id`, `email` or `url` catches
  most cases earlier, but cannot see values that only become unbounded in
  production.

## Run
```bash
python -m unittest -v test_solution.py
```

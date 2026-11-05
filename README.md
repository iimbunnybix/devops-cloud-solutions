# DevOps & Cloud Solutions

Real reliability and operations problems (retry storms, alerting, deployments, cost, observability), each with root cause, a working Python fix and tests.

Every problem folder has the same structure:

| File | Contents |
|---|---|
| `README.md` | The incident or requirement, root cause, solution and trade-offs |
| `solution.py` | Working implementation (Python standard library only) |
| `test_solution.py` | Tests that reproduce the problem and prove the fix |

## Problems

<!-- INDEX:START -->
| # | Problem |
|---|---|
| 001 | [Database recovers from a 2-minute blip, then goes down again under a retry storm](problems/001-retry-storm-backoff-jitter) |
| 002 | [On-call is paged 30 times a week for noise, yet missed a real 40-minute outage](problems/002-slo-burn-rate-alerting) |
| 003 | [Canary passed its health gate, then the full rollout quadrupled checkout errors](problems/003-canary-analysis-gate) |
| 004 | [Database volume filled up with no warning, while another server's disk alert paged every night for months](problems/004-disk-full-forecast-alert) |
| 005 | [Every mobile client failed to connect to the payments API at 09:34 because its TLS certificate had expired](problems/005-tls-certificate-expiry-monitor) |
| 006 | [A routine deploy silently undid an emergency memory fix, and the service started OOM-crashing again](problems/006-config-drift-detection) |
| 007 | [The nightly database backup had not run for three weeks, and nobody was alerted](problems/007-cron-dead-mans-switch) |
| 008 | [Search API replica count sawtoothed all day and p99 latency spiked every few minutes](problems/008-autoscaler-flapping-stabilization) |
| 009 | [The database failed over in under a minute, but the order service kept erroring for two hours until it was restarted](problems/009-stale-dns-cache-after-failover) |
| 010 | [The storage bill climbed every month after CI moved to per-PR environments, and the first cleanup script deleted a production volume](problems/010-orphaned-cloud-resources-cost) |
| 011 | [Every deploy caused a burst of 502s, so the team stopped deploying during business hours](problems/011-graceful-shutdown-connection-draining) |
| 012 | [The latency dashboard said p99 was 275 ms while customers on one host waited over two seconds](problems/012-averaged-percentiles-hide-latency) |
| 013 | [A five-minute database slowdown took the whole API down, and it stayed down after the database recovered](problems/013-liveness-probe-dependency-restart-storm) |
| 014 | [A deploy broke coupon checkout for two days, while the error-rate dashboard looked normal](problems/014-new-error-fingerprint-after-deploy) |
| 015 | [One database outage paged the on-call engineer 310 times in ten minutes](problems/015-alert-dedup-grouping) |
| 016 | [Users on one region were logged out with "token issued in the future" while logs showed replies arriving before requests](problems/016-clock-skew-drift-detection) |
| 017 | [Checkout returned 503s for 20 minutes every time a release went out during the evening peak](problems/017-rolling-deploy-capacity-batches) |
| 018 | [Payment errors vanished from the logs during an incident while the logging bill tripled](problems/018-log-flood-per-callsite-limit) |
| 019 | [Shoppers saw the checkout page flip between the old and new design on every reload during a "20%" rollout](problems/019-sticky-feature-flag-rollout) |
| 020 | [Every page on the site returned 503 for 40 seconds while all web servers were actually up](problems/020-health-check-panic-threshold) |
<!-- INDEX:END -->

## Run the tests

```bash
python run_tests.py                       # all problems
cd problems/<folder> && python -m unittest -v
```

Requires Python 3.10+. No third-party packages.

## Author

Hrishikesh Jadhav

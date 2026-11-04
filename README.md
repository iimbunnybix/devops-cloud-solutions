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
<!-- INDEX:END -->

## Run the tests

```bash
python run_tests.py                       # all problems
cd problems/<folder> && python -m unittest -v
```

Requires Python 3.10+. No third-party packages.

## Author

Hrishikesh Jadhav

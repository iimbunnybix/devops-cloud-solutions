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
| 013 | [A ten-minute database slowdown took the whole API down, and it stayed down after the database recovered](problems/013-liveness-probe-dependency-restart-storm) |
| 014 | [A deploy broke coupon checkout for two days, while the error-rate dashboard looked normal](problems/014-new-error-fingerprint-after-deploy) |
| 015 | [One database outage paged the on-call engineer 310 times in ten minutes](problems/015-alert-dedup-grouping) |
| 016 | [Users on one region were logged out with "token issued in the future" while logs showed replies arriving before requests](problems/016-clock-skew-drift-detection) |
| 017 | [Checkout returned 503s for 20 minutes every time a release went out during the evening peak](problems/017-rolling-deploy-capacity-batches) |
| 018 | [Payment errors vanished from the logs during an incident while the logging bill tripled](problems/018-log-flood-per-callsite-limit) |
| 019 | [Shoppers saw the checkout page flip between the old and new design on every reload during a "20%" rollout](problems/019-sticky-feature-flag-rollout) |
| 020 | [Every page on the site returned 503 for 40 seconds while all web servers were actually up](problems/020-health-check-panic-threshold) |
| 021 | [`df` said /var was 100% full, but `du` could only find 37 GiB of files](problems/021-deleted-open-log-files-disk-full) |
| 022 | [Backups had shown "succeeded" every night for weeks, but the first restore found empty files](problems/022-unverified-backup-restore-check) |
| 023 | [Error-rate alert stayed quiet while a restarted pod failed half its requests, and the traffic panel showed a negative request rate](problems/023-counter-reset-rate-spike) |
| 024 | [Login was down for every user during a single-zone power outage, although the service ran six replicas](problems/024-zone-spread-single-az-outage) |
| 025 | [Rolling back a bad release made the outage worse: the old version crashed on every request](problems/025-rollback-unsafe-schema-migration) |
| 026 | [On-call lowered the database connection limit during an incident, but the next deploy overloaded the database again](problems/026-config-typo-silent-default) |
| 027 | [Accounting received the same invoices twice whenever the database was slow, because the 5-minute export started on top of itself](problems/027-overlapping-scheduled-job-runs) |
| 028 | [Checkout failed with "password authentication failed" for 20 minutes during a routine database password rotation](problems/028-db-password-rotation-outage) |
| 029 | [Web servers failed every login with "No space left on device" while the disk dashboard showed 39% used](problems/029-inode-exhaustion-disk-alert) |
| 030 | [Checkout was down for 40 minutes during a database maintenance window, and on-call never got the page](problems/030-overbroad-maintenance-silence) |
| 031 | [Checkout failed for an hour after a deploy, and the error-rate alert stayed green because its metrics had disappeared](problems/031-alert-no-data-treated-as-healthy) |
| 032 | [Customers were charged twice on the night the clocks went back, and in spring the daily finance report never arrived](problems/032-dst-local-time-cron-double-run) |
| 033 | [Every scale-out and every deploy caused a p99 latency spike and a burst of timeouts on the newest instances](problems/033-load-balancer-slow-start-cold-instances) |
| 034 | [Half the fleet ran a different build of the same release, and rolling back did not bring the old code back](problems/034-mutable-image-tag-mixed-fleet) |
| 035 | [A "rename only" infrastructure pull request deleted the production orders database](problems/035-iac-rename-destroys-database) |
| 036 | [The API slowed to a crawl every afternoon while its CPU graph showed only 30%](problems/036-burstable-cpu-credit-exhaustion) |
| 037 | [Marketing emails arrived three hours late while the autoscaler said the workers were idle](problems/037-cpu-autoscaling-io-bound-queue-backlog) |
| 038 | [Login went down twice during a routine Kubernetes node upgrade](problems/038-node-drain-without-disruption-budget) |
| 039 | [On-call was woken at 3 a.m. for "50% errors" when only two requests had arrived](problems/039-error-ratio-alert-low-traffic-noise) |
| 040 | [Image workers were OOMKilled over and over under load, although each one planned to use only "half the memory"](problems/040-cache-sized-from-host-ram-oomkilled) |
| 041 | [Every dashboard and alert went blank an hour after a release added user_id to a request metric](problems/041-metric-label-cardinality-explosion) |
| 042 | [After a data-center migration, a quarter of traffic kept reaching the old servers for almost a day](problems/042-dns-ttl-lowered-too-late-cutover) |
| 043 | ["Checkout traffic dropped 30%" paged every other minute while traffic was perfectly flat](problems/043-traffic-drop-alert-on-unfinished-minute) |
| 044 | [After a certificate renewal the mobile app and partner integrations failed with "unable to get local issuer certificate", while the website worked in every browser](problems/044-tls-renewal-missing-intermediate-chain) |
| 045 | [Edge proxies crashed with "Expecting value: line 1 column 1" a few seconds into every routing deploy](problems/045-non-atomic-config-write-partial-read) |
<!-- INDEX:END -->

## Run the tests

```bash
python run_tests.py                       # all problems
cd problems/<folder> && python -m unittest -v
```

Requires Python 3.10+. No third-party packages.

## Author

Hrishikesh Jadhav

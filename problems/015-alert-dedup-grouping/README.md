# 015 · One database outage paged the on-call engineer 310 times in ten minutes

## Problem
The primary Postgres in the `prod-eu` cluster crashed. Its own
`PostgresDown` alert fired, and so did a `DBConnectionErrors` alert on each of
the 30 application hosts that talk to it. The alert rules are evaluated every
60 seconds and every firing went straight to the pager.

In the 10 minutes before failover finished, the on-call engineer's phone
received **310 pages** (31 alerts × 10 evaluations). They spent the first
minutes acknowledging pages instead of fixing the database, a second
engineer muted the pager entirely, and a real, unrelated disk alert in another
cluster was missed in the noise.

## Root cause
- **No deduplication.** The same alert (same labels) firing again on the next
  evaluation was treated as a new problem.
- **No grouping.** Thirty hosts reporting the same symptom of the same cause
  became thirty separate pages, instead of one page listing thirty hosts.
- **No rate limit on follow-ups.** A still-firing alert re-paged every minute.

## Solution
`AlertGrouper` sits between the rule evaluator and the pager, using the same
model as Prometheus Alertmanager:

1. **Fingerprint** each alert by all its labels (sorted), so repeat firings
   merge into one alert.
2. **Group** alerts by a few labels (`alertname`, `cluster` by default). All
   hosts with `DBConnectionErrors` in `prod-eu` share one group.
3. **`group_wait` (30 s):** a new group waits before its first page so the
   rest of the burst lands in the same message.
4. **`group_interval` (5 min):** when a group that already paged gains or
   loses members, send an update, at most once per interval.
5. **`repeat_interval` (4 h):** an unchanged, still-firing group is re-sent
   only after this long.

Replaying the same outage through the grouper (tests, fake clock) sends
**2 notifications** instead of 310: one for `PostgresDown` and one for
`DBConnectionErrors` listing all 30 hosts.

## Trade-offs
- `group_wait` delays the very first page by up to 30 seconds. That is
  usually fine for pages; tighten it for alerts where seconds matter.
- Choosing `group_by` labels is a judgement call: too coarse (just
  `alertname`) hides that two clusters are failing, too fine (`instance`)
  brings the flood back.
- Grouping does not know that `DBConnectionErrors` is *caused by*
  `PostgresDown`. **Inhibition rules** (suppress symptom alerts while the
  cause alert fires) or alerting on symptoms only (SLO burn rate, problem
  002) cut the noise further.
- State is in memory; a real deployment runs a clustered, replicated
  Alertmanager so a restart does not re-page everything.

## Run
```bash
python -m unittest -v test_solution.py
```

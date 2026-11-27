# 075 · The incident timeline showed the database failing over nine hours after the app errors it caused

## Problem
An e-commerce company runs its order API in New York, its primary
PostgreSQL cluster in Mumbai and its edge load balancers in London. Every
host logs in its own local time, with no offset:
`2026-03-14 09:30:04 ERROR orders-api: connection refused to db-primary`.
During an incident, the on-call engineer runs the incident tool, which pulls
the logs from all hosts and merges them into one timeline.

On 14 March the Mumbai primary lost its heartbeat and failed over to a
replica. Checkout errors lasted 45 seconds in reality. The merged timeline
the responders saw was:

```
2026-03-14 09:30:04  app-nyc-1    connection refused to db-primary
2026-03-14 09:30:45  app-nyc-1    db connection restored
2026-03-14 13:30:20  lb-london-1  5xx rate 38% on /orders
2026-03-14 19:00:00  db-mumbai-1  primary lost heartbeat, starting failover
2026-03-14 19:00:41  db-mumbai-1  replica promoted, accepting writes
```

The failover appeared **9 h 29 min 56 s after** the first app error, and
the London 5xx spike appeared four hours after the app had "recovered". The
responders concluded the failover was a *consequence* of the app outage,
spent the first hour of the post-incident review investigating the order
API's connection pool, and the action item for the real cause (a flaky
heartbeat network path in Mumbai) was written a week late.

## Root cause
**Timestamps without an offset were compared as if they shared one clock.**
`09:30:04` in New York (UTC-4 in March), `13:30:20` in London (UTC+0) and
`19:00:00` in Mumbai (UTC+5:30) are 20 seconds apart, but sorted as
strings they are nine and a half hours apart. The tool sorted the strings.

Daylight saving makes it worse even inside one zone. When New York set its
clocks back on 2026-11-01, 01:00-01:59 happened twice. A line logged at
`01:50` before the change and one logged at `01:10` after it sort in the
wrong order, although they are 20 minutes apart in real time. In spring,
`2026-03-08 02:30` never happened in New York at all, so a log line with
that time means a clock or parsing problem.

This is not clock skew (problem 016: the clocks were wrong) nor a missing
year (problem 051). Every clock here was correct; the logs just did not say
which zone they were in. Problem 032 is the same DST trap hitting a cron
scheduler, not log analysis.

## Solution
`merge_timeline` takes the logs plus each host's configured IANA zone
(`Asia/Kolkata`, `America/New_York`, `Europe/London`) and uses `zoneinfo`
from the standard library:

1. Parse each host's lines **in file order** with that host's zone and
   convert to an aware UTC `datetime`.
2. **Flag** times that are **nonexistent** (they do not survive a round
   trip through UTC) or **ambiguous** (`fold=0` and `fold=1` give different
   offsets). In the repeated hour, a host's log going *backwards* means the
   clock was set back, so later lines are read as the second occurrence
   (`fold=1`).
3. Sort by UTC. A host with no configured zone raises an error instead of
   being guessed.

The fixed timeline puts the failover first (13:30:00Z), then the app errors
(13:30:04Z), the 5xx spike (13:30:20Z), the promotion (13:30:41Z) and the
recovery (13:30:45Z): a 45-second incident in the right causal order. The
fall-back pair becomes 05:50Z and 06:10Z, both flagged as ambiguous.

The real fix is at the source: log **RFC 3339 timestamps in UTC or with an
offset** (`2026-03-14T13:30:04Z`). `to_rfc3339` shows the format. The parser
is for the years of old logs that already exist.

## Trade-offs
- **Depends on correct zone configuration.** If a host's zone is wrong in
  the inventory, its lines are shifted by whole hours. Keep the zone next to
  the host in the same source of truth, and check it against the host's own
  `timedatectl` output.
- **The fold heuristic needs a backwards step.** If a host logs once in the
  first 01:xx and next in the second 01:xx at a later wall time, nothing
  reveals the change, and the line may be off by an hour. That is why
  ambiguous lines are flagged, not silently trusted.
- **Nonexistent times keep the pre-jump offset** (`02:30` becomes 07:30Z).
  Any choice is a guess; the flag is what matters.
- **Second-level precision.** Lines in the same second from different hosts
  are ordered by input order, not by what really happened. Sub-second
  timestamps or a trace ID are needed to order those.
- Many log pipelines assume UTC when no offset is given (Elasticsearch date
  parsing does by default), which reproduces this bug at ingest time. Set the zone per source in the
  shipper, or better, fix the log format.

## Run
```bash
python -m unittest -v test_solution.py
```

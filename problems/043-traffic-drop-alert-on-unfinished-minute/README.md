# 043 · "Checkout traffic dropped 30%" paged every other minute while traffic was perfectly flat

## Problem
The checkout service runs on 10 web hosts, each serving a steady 10 requests
per second (6,000 requests a minute in total). A log shipper on each host
sends per-second request counts to the metrics store in batches every 15
seconds, and each batch spends another 1-20 seconds in transit.

A "traffic dropped" rule was added after an outage where a bad DNS change sent
customers elsewhere: every minute, on the minute, count the requests in the
minute that just ended and page if it is under 70% of normal.

In a one-hour simulation of steady traffic (tests, seeded transit times), the
rule paged on **24 of 56** checks. The minute it looked at held only 58-78% of
its requests at check time (69% on average), because the last 13-25 seconds
of it were still in the shippers' buffers or on the wire. On-call muted the
alert within a week. Later, when one shipper hung, the same rule paged **47
times** in the hour for a traffic drop that never happened.

## Root cause
The rule assumed that when a minute is over, its data is in the store. Metric
data arrives late: batching and transit add a delay, so the newest minute is
always incomplete. Counting it measures ingestion progress, not traffic. A
hung shipper looks exactly like lost traffic for the same reason.

## Solution
Track how far the data is complete and only judge complete minutes:

1. **Each batch says what it covers.** A shipper promises that every second
   before `covers_until` is in this batch or an earlier one.
2. **Per-host watermark.** The time up to which all of a host's batches have
   arrived. Batches can overtake each other in transit, so a batch only moves
   the watermark once every batch before it has arrived too.
3. **Fleet watermark** = the slowest host's watermark.
4. **Check the newest whole minute that ends before the watermark.** With
   the delays above that is always the previous minute (60 s behind).
5. **Separate "data delayed" from "traffic dropped".** If the newest complete
   minute is more than 180 s old, report `DATA_DELAYED`: the pipeline needs
   attention, but nobody should start a traffic incident.

Results in the tests: steady traffic gives **0** alerts in the hour. A real
50% drop at t=1800 s is reported at t=1920 s (2 minutes later). A shipper that
hangs at t=1200 s produces `DATA_DELAYED` from t=1380 s and no traffic-drop
pages.

## Trade-offs
- Alerts are about one minute later than the naive rule would be if data
  arrived instantly. That is the price of looking only at complete data; a
  shorter flush interval shrinks it.
- One slow host holds back the watermark for everyone. Real systems drop a
  host from the expected set when it is decommissioned, or use a quorum
  (e.g. 95% of hosts) and scale the expected count to the hosts that reported.
- Prometheus-style setups get a similar effect by evaluating with an offset
  (`... offset 1m`) or a longer range. That is simpler but uses a fixed delay
  guess instead of a measured watermark, and cannot tell "late" from "stuck".
- A dead host stops sending traffic and data together. This rule reports that
  as `DATA_DELAYED`; host-down alerts and an SLO burn-rate alert (problem
  002) should cover the user impact.

## Run
```bash
python -m unittest -v test_solution.py
```

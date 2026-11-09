# 042 · After a data-center migration, a quarter of traffic kept reaching the old servers for almost a day

## Problem
A company moved its storefront `shop.example.com` from an old hosting
provider to a new one. The A record had a TTL of 24 hours. The runbook said:

1. lower the TTL to 60 seconds,
2. wait one hour "to be safe",
3. switch the A record to the new IP,
4. shut down the old servers after another hour.

Step 4 was postponed only because support tickets started arriving: orders
were landing on the old servers, whose database was now read-only. For the
rest of the day a large share of traffic kept going to the old IP, and the
team had to keep the old site running and replay orders by hand. If the old
servers had been shut down on schedule, those customers would have seen the
site as down for most of a day.

## Root cause
**A resolver caches an answer for the TTL it had when it fetched it.**
Lowering the TTL at the DNS provider does not shorten entries that recursive
resolvers (ISPs, offices, public resolvers) already hold. A resolver that
fetched the record shortly before step 1 keeps the old IP, with a 24-hour TTL,
until that entry expires; the 60-second TTL only applies to answers fetched
after the change. Waiting the *new* TTL (plus a margin) says nothing about
answers given under the *old* TTL.

In the simulation (200 caching resolvers, each looking the name up on average
every 10 minutes, seeded), cutting over one hour after lowering the TTL:

| | Naive cutover (+1 h) | Safe cutover (+24 h) |
|---|---|---|
| Resolvers that returned the old IP after the cutover | **192 of 200** | **0** |
| Lookups after the cutover that returned the old IP | **13,582 of 56,157 (24.2%)** | **0** |
| Longest time a resolver still returned the old IP | **over 22.5 hours** | none |

## Solution
`solution.py`:

- **`CachingResolver`** models a resolver: it fixes the expiry time when it
  fetches the answer, exactly the behaviour that breaks the naive plan.
- **`safe_cutover_time(history, ttl_lowered_at)`** returns the earliest time
  at which every answer handed out *before* the TTL change has expired: for
  each earlier version of the record, the moment it stopped being served plus
  its TTL. With a 24-hour TTL lowered at time 0 this is 24 hours; if an even
  older version had a 7-day TTL and was served until 1 day before the change,
  it is 6 days.
- **`safe_retire_time(cutover_at, ttl_at_cutover)`** is when the old servers
  may be turned off: answers fetched just before the cutover still point at
  them for one (low) TTL.

The fixed runbook: lower the TTL at least one *old* TTL before the migration
(for a 24-hour TTL, the day before), cut over at or after
`safe_cutover_time`, keep the old servers up until `safe_retire_time`, and
watch their traffic drop to zero before shutting them down.

## Trade-offs
- The model assumes resolvers honour TTLs. Some clamp very low TTLs to a
  minimum (often 30 s to 5 minutes), and some clients cache longer than the TTL
  (see 009: a process that never re-resolves). Keep the old servers running
  well past `safe_retire_time`, ideally serving a redirect or proxying to the
  new site, and remove them only when their access logs go quiet.
- Lowering the TTL early means more queries to the DNS provider for a day.
  That is normally cheap; it is the price of a fast switch.
- When waiting a day is impossible, avoid depending on DNS: put both sites
  behind the same load balancer or anycast address and switch backends there,
  or make the old servers proxy to the new ones from the moment of cutover.
- Negative answers (NXDOMAIN) are cached too, using the SOA record's minimum
  TTL; creating a brand-new name has the same delay if someone looked it up
  before it existed.

## Run
```bash
python -m unittest -v test_solution.py
```

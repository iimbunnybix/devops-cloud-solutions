# 009 · The database failed over in under a minute, but the order service kept erroring for two hours until it was restarted

## Problem
The orders database runs as a primary with a standby. When the primary's host
failed, the managed service promoted the standby and updated the DNS name
`db.prod.internal` to the new primary's IP (record TTL 30 s) within seconds.
Every service recovered except the order service: its pods kept trying to
connect to the old IP and every checkout returned 500 until the on-call
engineer, two hours later, restarted the deployment "to see if it helps".

## Root cause
**The service cached the DNS answer forever.** It resolved the hostname once
at startup and reused the IP for the lifetime of the process (the same effect
as a JVM with `networkaddress.cache.ttl=-1`, or a connection pool that stores
the resolved address). The DNS TTL was never consulted, so a failover that
works by changing DNS was invisible to it, and a connection failure did not
cause a new lookup either.

In the reproduction (one connection attempt per second, DNS updated 10 s after
the old primary dies), the forever-cache fails **all 600 attempts** in the
10 minutes after failover and makes **0** DNS queries.

## Solution
`TtlResolver` and `DbClient` in `solution.py`:

1. **Honour the TTL**, clamped to `[min_ttl, max_ttl]` (1 s to 60 s). The
   upper clamp limits how long a wrong answer can survive even if someone sets
   a one-day TTL; the lower clamp stops a TTL of 0 from causing a lookup per
   request. With TTL alone, failures stop when the cached entry expires:
   **25** failed attempts in the reproduction (the failover landed 25 s
   before expiry).
2. **Re-resolve on connection failure.** A failed connect invalidates the
   entry; the client looks the name up again and retries once if the IP
   changed. Failures then last only as long as DNS itself still points at the
   dead host: **10** failed attempts (the 10 s DNS lag), then recovery on the
   next attempt.
3. **Rate-limit refreshes** (`min_refresh_interval` = 1 s per name) so a dead
   backend cannot become a DNS query storm: 30 queries over the 10 minutes
   (one per second during the lag, then one per TTL).
4. **Serve stale on DNS failure** for up to 5 minutes (`stale_grace`), so a
   resolver outage does not take down a service whose database is fine.
   Failed lookups count toward the rate limit too, so during a DNS outage the
   client retries at most once per second per name (1,000 requests in a
   second -> 1 query) instead of once per request.

## Trade-offs
- Shorter TTLs mean faster failover but more DNS load and more exposure to
  resolver hiccups; serve-stale reduces the second risk.
- Existing pooled connections to the old primary are not affected by DNS at
  all. Pools also need a max connection lifetime and validation on borrow, or
  they hold broken connections until a TCP timeout.
- If the old primary stays *reachable* but becomes read-only, connect succeeds
  and re-resolution never happens; treat "read-only" errors from the database
  as a signal to invalidate and reconnect too.
- Platform-level fixes: set the runtime's DNS cache TTL (for the JVM,
  `networkaddress.cache.ttl`), use a database proxy that tracks the primary
  for you, or use the provider's failover-aware driver.

## Run
```bash
python -m unittest -v test_solution.py
```

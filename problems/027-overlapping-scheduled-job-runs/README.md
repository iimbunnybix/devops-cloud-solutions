# 027 · Accounting received the same invoices twice whenever the database was slow, because the 5-minute export started on top of itself

## Problem
An invoice export job runs every 5 minutes from a scheduler. It collects
every invoice not yet marked "exported", sends them to the external
accounting system, and marks them exported when it finishes. A normal run
takes about 2 minutes.

One afternoon a heavy report query slowed the database and runs started
taking 15-17 minutes. The scheduler kept firing on time, so new copies
started while older ones were still working. Each copy picked up the same
unexported invoices. Finance found duplicate invoices in the accounting
system, customers received duplicate payment reminders, and cleanup took
two days. The extra copies also added more load to the already slow
database, which made every run slower.

## Root cause
The scheduler starts a run per tick, and nothing stops a run from starting
while the previous one is still going. The job is not safe to run twice at
once: it reads "unexported" at the start and marks "exported" only at the
end, so overlapping runs see the same work.

In the test schedule (one 2-minute run, three 1000-second runs, then four
2-minute runs, 10 new invoices per tick), `naive_export` reaches **4
concurrent runs** and exports **60 invoices twice** out of 80.

## Solution
Each run must hold a **lease** (`LeaseLock`) for the job before it starts:

- **Skip, don't queue.** If a live lease exists, the new start is skipped
  and counted in `skipped_starts`. The next free tick picks up all pending
  work, so nothing is lost. On the same schedule `guarded_export` runs one
  copy at a time, exports all 80 invoices exactly once, and skips 3 starts.
  Alert on a rising skip count: it means runs are slower than the interval.
- **Expiry.** The lease has a TTL (30 minutes here), so a run that crashes
  without releasing it blocks the job only until the lease expires. In the
  crash test, 5 ticks are skipped and then the job recovers on its own.
- **Owner token.** `acquire` returns a unique token. `release` and `renew`
  only work with the current owner's token, so a slow run whose lease has
  already expired cannot delete or extend the lease a newer run now holds.
- **Renew for long runs.** A run that may take longer than the TTL should
  call `renew` periodically (a heartbeat) while it works.

## Trade-offs
- A lease limits overlap; it does not make it impossible. A run that pauses
  longer than the TTL (GC pause, stuck I/O) and then resumes can still write
  after a new run started. If writes must be strictly single-writer, pass a
  fencing token to the downstream system, and make the export idempotent
  (for example an idempotency key per invoice) so a repeat is harmless.
- The TTL is a tuning knob: too short and healthy slow runs lose the lease;
  too long and a crash stops exports for longer. Keep it short and renew.
- In production the lease must live in shared storage with an atomic
  compare-and-set (a database row with `UPDATE ... WHERE expires_at < now`,
  Redis `SET NX PX`, etcd/Consul leases), not in process memory.
- Kubernetes CronJobs offer `concurrencyPolicy: Forbid`, which covers the
  scheduler side but not manual runs or jobs started from other places.

## Run
```bash
python -m unittest -v test_solution.py
```

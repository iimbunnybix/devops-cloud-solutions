# 052 · The database got corrupted and the newest restorable backup had been deleted three days earlier

## Problem
A team runs a nightly backup of their main 40 GB order database at 02:00
and keeps "the last 7 backups"; a cleanup step deletes everything older.
On night 4 the storage credential used by the backup job expired. Every run
after that failed after a few seconds and left a failed, empty backup
record. Nobody noticed: the job's failure went to a channel nobody read. A
week later a bad migration corrupted the orders table. The team went to
restore and found only 7 failed, empty backups. Retention had deleted the
three good ones as soon as the seventh failure arrived. Days of orders had
to be rebuilt from payment-provider exports and emails.

## Root cause
The retention rule counted **records**, not **restore points**: "keep the
newest 7 backups, whatever their status". A failed or empty backup filled a
slot exactly like a good one. Once the job had failed 7 nights in a row, the
newest 7 records were all failures, and every backup that could actually be
restored was outside the window and deleted. The rule designed to limit
storage cost removed the last line of defence, and it did so silently,
because nothing alerted on "no good backup recently".

## Solution
- `is_usable` defines a restore point: status `succeeded` **and** more than
  0 bytes written.
- `backups_to_delete` walks backups from newest to oldest and keeps the
  newest 7 **usable** ones, however old they are. Failed and empty backups
  are deleted (they cannot be restored) and do not take up the quota.
  Running backups are skipped, because the job is still writing them. A quota
  below 1 is refused.
- `backups_are_stale` returns `True` when the newest usable backup is more
  than 26 hours old (one nightly run plus slack), or when there is none, so
  the failing job pages someone instead of failing quietly for a week.

In the tests, 3 good backups followed by 7 failures: the original rule
deletes all 3 good ones and keeps nothing restorable; the new rule keeps all
3 and deletes the 7 failures.

## Trade-offs
- Keeping old backups when new ones fail means storage stops shrinking during
  an outage, and the "newest" restore point may be days old. The staleness
  alert is what limits that window; retention alone does not.
- "More than 0 bytes" is a weak check. A truncated dump still passes. A real
  restore test (problem 022) or a size check against the previous backup is a
  stronger definition of "usable".
- Deleting failed backups immediately removes their logs; some teams keep the
  last few failures for debugging.
- Object-store lifecycle rules ("delete objects older than 7 days") cannot
  see backup status at all. If you rely on them, add object lock or a
  separate long-retention copy, such as a weekly backup kept for months.

## Run
```bash
python -m unittest -v test_solution.py
```

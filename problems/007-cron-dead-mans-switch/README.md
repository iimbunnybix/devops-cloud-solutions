# 007 · The nightly database backup had not run for three weeks, and nobody was alerted

## Problem
A nightly `pg_dump` backup of the billing database runs from cron on a utility
VM and uploads to object storage. It pages on-call if the script exits
non-zero. When an engineer needed to restore a table after a bad migration,
the newest backup was 21 days old. The VM had been rebuilt from a new base
image that did not include the cron daemon, so the backup had silently
stopped. The restore had to use the 21-day-old dump plus a manual replay of
application logs.

## Root cause
**Alerting watched failures, not absence.** The only signal was "the script
failed", and a script that never starts never fails. Cron removed, a wrong
crontab user, a disabled Kubernetes CronJob, a scheduler outage or a job stuck
forever on a lock all produce *no* events, so a failure-only alert stays green.
`ReproduceSilentCron` shows zero alerts for the whole outage.

## Solution
**A dead man's switch (heartbeat monitor) that knows each job's schedule.**

1. The job wrapper sends `start`, `success` and `fail` pings with a run id.
2. The monitor knows `period`, `grace` and `max_runtime` per job and, every
   minute, computes a status:
   - **MISSED**: no success within `period + grace` of the last success.
   - **NEVER_RAN**: a newly registered job has not succeeded within
     `period + grace` (catches a job that was never scheduled correctly).
   - **STUCK**: a run started but did not finish within `max_runtime`.
     Overlapping runs are tracked by id, so one healthy run does not hide a
     hung one. A run that crashed without any final ping (OOM kill, lost
     host) is forgotten once a newer run finishes and the old one is already
     past `max_runtime`, so the job can return to OK and still page MISSED
     later instead of staying STUCK forever.
   - **FAILED**: the last run reported failure, with its error message.
3. Only **state transitions** are emitted: one `FIRING` per incident and a
   `RESOLVED` when the job succeeds again, so a 21-day gap is one page, not
   one per check.

For the daily backup (period 24 h, grace 1 h), the test shows the missed run
detected between 25 h and 25 h + 1 min after the last success (the 1-minute
evaluation interval), instead of never.

## Trade-offs
- The monitor itself must be outside the failure domain it watches (another
  host or a hosted check-in service), and needs its own alert if *it* stops.
- Interval-based schedules are simpler than full cron expressions. Jobs that
  skip weekends or holidays need calendar-aware expectations or they cause
  false MISSED alerts.
- Grace is a tuning knob: too small and slow nights page; too large and
  detection is late. Start from the job's observed p99 start delay.
- A success ping only proves the script exited 0. For backups, also verify
  the output (size, checksum, a periodic test restore), because "successful"
  empty dumps are a known failure mode.
- Unknown job names raise instead of being ignored, so a typo in a wrapper
  fails loudly at deploy time rather than creating an unmonitored job.

## Run
```bash
python -m unittest -v test_solution.py
```

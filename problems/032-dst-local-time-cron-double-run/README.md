# 032 · Customers were charged twice on the night the clocks went back, and in spring the daily finance report never arrived

## Problem
A subscription business runs its daily jobs from an in-house scheduler
that the team configured in New York local time, because finance thinks
in local business days: the billing run at **01:30** and the revenue
report at **02:30**.

On 1 November 2026 (US daylight saving time ends) the billing run charged
every customer due that day twice. The payment provider's duplicate
detection caught some charges; the rest had to be refunded by hand, and
support handled a wave of "double charge" complaints. Looking back, the
team also found that on 8 March 2026 (daylight saving time starts) the
02:30 revenue report had not run at all; nobody had noticed because no
job failed.

## Root cause
The scheduler woke up every minute, converted the current time to New
York local time and ran each job whose `HH:MM` matched. That assumes every
local time happens exactly once a day, which is false twice a year:

- **Fall back:** at 02:00 EDT the clocks go back to 01:00 EST, so
  01:00-01:59 happens twice. 01:30 matched at **05:30 UTC and again at
  06:30 UTC**, so billing ran twice.
- **Spring forward:** at 02:00 EST the clocks jump to 03:00 EDT, so
  02:00-02:59 does not exist. 02:30 never matched, so the report was
  silently skipped.

Over all of 2026 (365 local days), the naive scheduler runs the 01:30 job
**366** times and the 02:30 job **364** times.

## Solution
`DailyJobScheduler` in `solution.py` schedules by **local date**, not by
matching wall-clock digits:

1. For each local date, `run_instant_utc` resolves the job's wall time to
   **one UTC instant**. An ambiguous time uses its first occurrence (01:30
   EDT = 05:30 UTC on 1 November). A nonexistent time moves past the gap
   (02:30 on 8 March becomes 03:30 EDT = 07:30 UTC), so the job runs late
   rather than never.
2. On each tick it runs the job once the clock has passed that instant
   **and** the date has not run yet. The repeated hour cannot trigger a
   second run, and a coarse or late tick (every 7 minutes in a test) still
   runs it once.
3. `classify_local_time` reports whether a time is normal, ambiguous or
   nonexistent on a given date, so a config check can warn when someone
   schedules a job inside the 01:00-03:00 window.

Over 2026, both jobs run on **every one of the 365 local dates exactly
once**. The same code handles other zones with different transition
times: Berlin repeats 02:00-02:59 on 25 October 2026, and the 02:30 job
runs once at 00:30 UTC (02:30 CEST).

## Trade-offs
- The simplest alternative is to schedule in **UTC**. That never repeats
  or skips, but the job moves by an hour in local time twice a year, which
  matters for jobs tied to local business days or office hours.
- Moving jobs out of the 01:00-03:00 local window avoids the problem in
  most zones that observe DST, but not all transition rules, and relies on
  everyone remembering why.
- `last_run_date` is in memory here; a real scheduler must persist it (or
  record runs in a table keyed by job and local date), otherwise a
  restart in the repeated hour runs the job again. The billing job itself
  should also be idempotent per customer and billing date, so any duplicate
  run is harmless.
- Classic Vixie cron and cronie special-case clock changes of less than
  three hours, but many hand-written schedulers and job libraries do not;
  check before relying on local-time schedules.

## Run
```bash
python -m unittest -v test_solution.py
```

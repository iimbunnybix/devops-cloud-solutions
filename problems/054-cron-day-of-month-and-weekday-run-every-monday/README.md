# 054 · Customers got a "monthly" account statement every Monday

## Problem
A lending platform emails each of its 400,000 customers a monthly account
statement. An engineer moved the job to a new scheduler host and, to avoid a
clash with a weekend batch, wrote the crontab line `0 2 1 * 1`, intending
"02:00 on the 1st of the month, but only if it is a Monday". The job ran on
Monday 5 January 2026, then again on the 12th, 19th and 26th. Customers got
five statements in January (one on the 1st and four Mondays), support tickets
spiked, and the email provider rate-limited the sender domain for bulk sending.

## Root cause
Cron does not combine the two day fields with AND. The crontab(5) rule in
Vixie cron (the cron on most Linux systems, and POSIX) is: if **both**
day-of-month and day-of-week are restricted (neither starts with `*`), the
entry runs when **either** matches. So `0 2 1 * 1` means "on the 1st, or on
any Monday". In 2026 that is 63 runs (12 firsts plus 52 Mondays, minus 1 June
which is both) instead of the 1 the author expected. The popular "first Monday
of the month" idiom `0 2 1-7 * 1` has the same bug: 124 runs in 2026 instead
of 12. Nothing complains about the entry: it is valid syntax.

## Solution
`solution.py` contains a small cron matcher and two fixes:

- `cron_day_matches` implements the real rule, including Vixie cron's detail
  that a field counts as unrestricted when its text starts with `*`, so
  `*/2` in day-of-month keeps AND semantics (tested).
  `naive_assumed_day_matches` is the author's AND mental model, kept for
  comparison; `run_times` lists the runs under either rule.
- `lint_cron` flags every entry that restricts both day fields. Run it in CI
  over the crontabs in the repository so the mistake is caught at review.
- `rewrite_with_day_guard` turns such an entry into a weekday-only schedule
  plus a day-of-month guard the job checks itself, for example
  `0 2 * * 1` with `[ "$(date +%-d)" -eq 1 ] || exit 0`.
  `guarded_run_times` proves the guarded job does work on exactly the
  intended days for both examples above.

## Trade-offs
- The guard lives in the job or a wrapper script, so it is invisible in the
  crontab line itself; leave a comment next to the entry. Schedulers with a
  richer syntax avoid this: some cron variants (Quartz, and others) support
  `1#1` for "first Monday" and refuse a restricted day-of-month and
  day-of-week together.
- The matcher handles numbers, `*`, lists, ranges and steps only; it does not
  parse month or weekday names, `@daily` macros or `L`/`W`/`#` extensions.
- A guard means cron still starts the job on the extra days and it exits at
  once; that is harmless for most jobs, but the wrapper must exit before
  taking locks or sending heartbeats to a dead man's switch (problem 007).

## Run
```bash
python -m unittest -v test_solution.py
```

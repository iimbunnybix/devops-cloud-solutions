# 051 · Just after midnight on 1 January the "checkout errors" alert went silent while payments were failing

## Problem
A shop's web servers send classic syslog lines (`Dec 31 23:59:15 web-3
checkout: ERROR ...`) to a central collector. An alert fires when more than
a handful of checkout `ERROR` lines arrive within 10 minutes. On New Year's
Eve the payment gateway started timing out at 23:57. At 00:05 the alert
looked at the last 10 minutes and counted **1** error instead of **3**: the
two from before midnight were missing. The on-call engineer was not paged
until the next morning's report, and the incident timeline in the log search
showed those lines as written on **31 December of the following year**. A
year earlier the same pipeline crashed on the first `Feb 29` line it saw
after the leap day, while backfilling logs on 1 March.

## Root cause
RFC 3164 syslog timestamps have **no year**. The shipper filled it in with
the collector's *current* year. That is right for most of the year, but in
the first minutes of January every line from 31 December gets the new year
and lands about 12 months in the future, outside every "last N minutes"
query. Using the wrong year also turns `Feb 29` into an invalid date
(`ValueError: day is out of range for month`) in a non-leap year.

## Solution
`parse_timestamp` tries the previous, current and next year and keeps the
**latest candidate that is not after now + 1 hour**:
- `Dec 31 23:59:15` seen at `2027-01-01 00:05` becomes 2026-12-31, because
  2027-12-31 is in the future.
- The next year is a candidate so that a host whose clock runs a few seconds
  ahead can log `Jan  1 00:00:05` while the collector still reads
  31 December; the one-hour tolerance allows that skew without accepting
  lines a year ahead.
- Dates that do not exist in a candidate year (Feb 29) are skipped, so a
  leap-day line seen in March gets the leap year. If no neighbouring year is
  a leap year, it raises a clear `ValueError` instead of guessing.

`count_recent_errors` runs the same alert with either parser, so the tests
show 1 error counted with the old parser and 3 with the new one.

## Trade-offs
- The inference assumes lines arrive within about a year of being written.
  Replaying a two-year-old archive needs the year from the file name or
  file date instead.
- Timestamps are treated as local time on the collector, like the original
  pipeline. Hosts in other time zones still need a time zone per host.
- The real fix is to stop guessing: switch senders to RFC 5424 or ISO 8601
  timestamps with year and UTC offset (rsyslog and syslog-ng both support
  this), and keep this inference only for legacy devices.

## Run
```bash
python -m unittest -v test_solution.py
```

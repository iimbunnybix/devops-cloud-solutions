# 004 · Database volume filled up with no warning, while another server's disk alert paged every night for months

## Problem
Two disk incidents in the same month on a fleet alerting on `disk used > 90%`:

1. A Postgres replica's volume filled up after a replication slot stopped
   being consumed and WAL piled up. Usage went from 50% to 100% in about
   ten hours. The 90% page fired roughly two hours before writes failed, at
   night, and the on-call engineer could not grow the volume in time.
2. Meanwhile a log-archive host sat at ~91% for months. Its alert paged
   nightly, was acknowledged without action, and was eventually muted, along
   with the rest of the disk alerts in that group.

## Root cause
A static threshold measures **how full** a disk is, not **how soon it will
be full**. It pages too late when growth is fast and pages forever when a
full-but-stable disk is fine. Alert fatigue from the second case then hid
the first.

## Solution
Alert on **predicted time-to-full**:

- Fit an ordinary least-squares line to the last 6 hours of usage samples
  (`linear_fit`). Least squares averages out sample noise; a two-point
  "now vs 1 h ago" rate is easily skewed by one burst.
- **Reset the trend after big drops** (> 5% of capacity, e.g. log rotation or
  a cleanup job), so growth before and after the drop isn't averaged together.
  The reset only applies once the post-drop segment covers at least 2 hours;
  a job that writes temp files and deletes them every hour would otherwise
  restart the fit each cycle and only ever see the steep "write" half.
- Project from the fitted current value to capacity → `seconds_to_full`.
- **Page** if full within 4 hours (or already ≥ 98%), **ticket** if full
  within 4 days, otherwise stay quiet. Flat or shrinking usage never alerts.
- Require at least 6 samples before forecasting.

Results from the tests (100 GB volume, 5-minute samples):

| Scenario | Static 90% rule | Forecast |
|---|---|---|
| Stable at ~91% | fires (false page) | no alert |
| 50% → 80% over 6 h at 5 GB/h | silent | page: full in 4.0 h |
| 60% → 63% over 6 h at 0.5 GB/h | silent | ticket: full in 3.1 d |
| 5 GB/h with ±0.5 GB noise | silent | page, estimate within ±0.5 h |
| Rotation drop, then 6 GB/h | — | growth estimated at 6.0 GB/h |
| Hourly job writes then deletes 10 GB at 70% | silent | never pages after the first hour; silent once 4 h of history exist |

This is the same idea as Prometheus's
`predict_linear(node_filesystem_avail_bytes[6h], 4*3600) < 0`, with the
drop-reset and two severities added.

## Trade-offs
- Linear fits miss accelerating growth (e.g. a retry loop that doubles
  logging); a shorter window reacts faster but is noisier. Running a short
  and a long window together is a common compromise.
- Bursty workloads (nightly batch writes) can look like fast growth for an
  hour; the 6-hour window, the 2-hour minimum before a drop resets the
  trend, and the ticket tier keep that from paging. With less than a few
  cycles of history a sawtooth can still open a ticket.
- Keep a hard-limit page (98%) as a backstop; forecasting needs history,
  and a new volume or a sudden huge write won't have any.
- Inode exhaustion fills a disk without using bytes; forecast inodes the
  same way.

## Run
```bash
python -m unittest -v test_solution.py
```

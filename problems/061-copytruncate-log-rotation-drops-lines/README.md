# 061 · An auditor asked for the log line of a captured payment and it was in neither app.log nor app.log.1

## Problem
A payments API on a fleet of VMs writes its application log to
`/var/log/payments/app.log`. Logrotate rotates it every night at 00:00 with
the `copytruncate` option, keeping a few old generations.

During a dispute, an auditor asked for the log lines of a payment captured at
00:00:01. The lines were in neither `app.log` nor `app.log.1`, and the same
gap existed on every host every night. With heavy logging at midnight
(batch settlement starts then) the gap reached several seconds, and the log
pipeline could not show what had happened in it. The team could not prove
from its own logs which requests had been processed.

## Root cause
`copytruncate` is two separate steps on a file the service keeps writing to:

1. copy `app.log` to `app.log.1` (takes time on a large file),
2. truncate `app.log` to zero length.

Anything the service writes after step 1 has read the end of the file and
before step 2 runs is not in the copy and is then deleted by the truncate.
The logrotate manual itself warns that with `copytruncate` "some logging
data might be lost". `ReproduceProblem` writes one line between the two
steps and shows it ends up in no file.

## Solution
Rotate by **renaming** (`rename_rotate`) and then tell the service to
reopen its log path:

1. `app.log.2 -> app.log.3`, `app.log.1 -> app.log.2` (the oldest beyond
   `keep` is deleted), then `app.log -> app.log.1`. A rename is atomic and
   does not touch the data.
2. The service's open file handle points at the file's inode, not its name,
   so lines written right after the rename still go into what is now
   `app.log.1`.
3. On a signal (logrotate's `postrotate` sending `SIGHUP`/`SIGUSR1`, or the
   framework's "reopen logs" hook), `AppLogger.reopen` opens `app.log`
   again, which creates a fresh file.

The tests check that a line written during rotation ends up in `app.log.1`,
that every line is stored exactly once, and that generations shift and the
oldest is deleted.

## Trade-offs
- The service must support reopening its log file on a signal. Many do
  (nginx with `USR1`, Python's `WatchedFileHandler` reopens when the file is
  moved). Without it, the service keeps writing to `app.log.1` forever.
- `copytruncate` exists for programs that cannot reopen; for those, prefer
  logging to stdout/stderr and letting systemd-journald or the container
  runtime handle storage and rotation, or ship logs over the network.
- Compressing the rotated file must wait until the service has reopened
  (logrotate's `delaycompress`), or the last lines are written into a file
  that has already been compressed and deleted.

## Run
```bash
python -m unittest -v test_solution.py
```

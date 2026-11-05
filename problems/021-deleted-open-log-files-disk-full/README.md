# 021 · `df` said /var was 100% full, but `du` could only find 37 GiB of files

## Problem
The API hosts have a 200 GiB `/var` volume. One night writes to `/var` started
failing with `No space left on device`; the app could not write its local
upload buffer and returned 500s on file uploads. `df -h /var` showed 100% used,
but `du -sh /var` added up to only 37 GiB. The on-call engineer deleted old
logs, which freed nothing, and finally rebooted a host, which "fixed" it until
the next week.

## Root cause
Deleting a file on Linux removes only its **name** (the directory entry). The
inode and its blocks are freed when the link count is 0 **and** the last open
file descriptor on it is closed. `du` walks directory entries, so it cannot see
a file without a name; `df` asks the filesystem how many blocks are in use, so
it still counts them.

logrotate was configured with `create` (rename the log, start a new one) and a
`postrotate` script that signals nginx to reopen its logs. After an upgrade the
script's pid-file path was wrong, so nginx was never told. It kept writing to
the renamed `access.log.1`; when that file aged out of `rotate N` it was deleted
while nginx still held it open, and it kept growing to 160 GiB with no name.
Smaller leaks came from a Java GC log and a forked worker sharing its parent's
log file.

## Solution
`solution.py` reads a `/proc`-style table of open descriptors (the same data as
`ls -l /proc/<pid>/fd` or `lsof +L1`):

- `deleted_inode_holders` finds descriptors on the full mount whose inode has
  **link count 0**. It does not rely on the `" (deleted)"` text in readlink
  output, because a real file may be named `report (deleted).txt` (tested).
- `hidden_bytes` sums those inodes **once each**, even when one process has
  `dup()`ed the fd or a forked child shares it. In the test incident this is
  exactly the 163 GiB gap between `df` (200 GiB) and `du` (37 GiB).
- `recommend` lists one action per process, largest reclaim first: send the
  process's log-reopen signal when it has one (`SIGUSR1` for nginx, `SIGHUP`
  for rsyslogd), otherwise restart it. A process only gets credit for inodes it
  holds alone; for a file shared by a parent and child, closing it in one of
  them frees 0 bytes, so both must reopen or restart (tested).

### logrotate: `copytruncate` versus reopening
- **`create` + `postrotate` signal** (the default style): rename the file, the
  app reopens on a signal. No lines are lost, but if the signal never arrives
  the app keeps writing to the old file, which is the incident above.
- **`copytruncate`**: copy the file, then truncate the original in place, so the
  app never needs to reopen. Lines written between the copy and the truncate
  are lost, and a writer that did not open the file with `O_APPEND` keeps
  writing at its old offset, leaving a sparse file that looks as big as before.

## Trade-offs
- This finds the space; freeing it still needs the process to cooperate. In an
  emergency you can truncate the deleted file through its descriptor
  (`: > /proc/<pid>/fd/<fd>`) without a restart, at the cost of losing that log.
- Reading other users' `/proc/<pid>/fd` needs root (or `CAP_SYS_PTRACE`), so a
  real agent runs privileged; here the table is passed in to keep it testable.
- Better prevention: alert when `df` used minus `du` used grows past a few
  percent, make the `postrotate` step fail loudly, or have apps log to stdout
  and let the runtime (journald, the container runtime) handle rotation.

## Run
```bash
python -m unittest -v test_solution.py
```

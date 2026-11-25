# 071 · After the nightly log rotation, the first hour of access logs was missing from search on random hosts

## Problem
Every web host runs a log shipper that tails `/var/log/app/access.log`
and sends each line to the central log search. The shipper saves a
checkpoint, "inode -> byte offset", so a restart does not re-send old
lines. A nightly job rotates the log by archiving and deleting the old file
and letting the app create a fresh one.

Some mornings the security team could not find the first hour after
rotation in log search on a handful of hosts, and the first line that did
arrive was half a line (`/api/orders/997 200` with no timestamp). An
investigation into a suspicious login had no access logs for exactly that
hour. Nothing in the shipper logged an error.

## Root cause
**The shipper identified a file by its inode number alone.** When the old
log is deleted, its inode number is freed, and ext4 commonly hands that
same number to the next file created in the filesystem: the new
`access.log`. The shipper saw a known inode, looked up the old offset and
resumed there, in the middle of a file it had never read.

In the simulation the old file is 500 lines (22,390 bytes). After rotation
the new file reuses the inode and the app writes 800 lines. The naive
shipper ships **303** of them, starting with half a line, and **497 are
lost**. With only 300 new lines (less than 22,390 bytes) it ships
**nothing** until the new file grows past the old offset.

## Solution
`poll` keeps a richer checkpoint per path: inode, offset, and a SHA-256
**fingerprint of the first bytes** of the file (up to 256 bytes, and only
bytes already read, so later appends never change it). It resumes from the
saved offset only if all three still agree:

- same inode number,
- the file is at least as long as the saved offset (a log only grows while
  it is tailed; shorter means truncated or replaced),
- the first bytes hash to the same fingerprint.

Otherwise the file is new and is read from byte 0. In the same scenarios
every line of the new file is shipped (800 of 800, and 300 of 300), plain
appends are not shipped twice, truncation and rename-style rotation (new
inode) both restart at 0, and a line without its newline yet waits for it.

## Trade-offs
- Two different files that begin with the same 256 bytes look like one
  file. Timestamped log lines make that very unlikely, but a log that
  starts with a fixed banner longer than the fingerprint would defeat it;
  a longer fingerprint or hashing the first N lines fixes that.
- Re-hashing the prefix on every poll costs a small read per file. It is
  a few hundred bytes, negligible next to shipping the new data.
- A file shorter than the fingerprint is fingerprinted on what exists, so
  a new file whose first few bytes match the old one could still slip
  through until it grows. Some shippers instead wait until a file has
  enough bytes before they fingerprint it.
- Lines written to the old file after the last poll but before it was
  deleted are still lost. Rotating by rename (keep `access.log.1` around
  for a while) and having the shipper finish reading the renamed file
  closes that gap; delete-and-recreate rotation cannot.
- Several shippers offer this: Vector fingerprints files by a checksum of
  their first bytes by default, and Filebeat can use a fingerprint as the
  file identity instead of inode and device.

## Run
```bash
python -m unittest -v test_solution.py
```

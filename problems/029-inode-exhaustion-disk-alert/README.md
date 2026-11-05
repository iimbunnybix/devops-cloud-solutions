# 029 · Web servers failed every login with "No space left on device" while the disk dashboard showed 39% used

## Problem
A fleet of PHP web servers stores user sessions as one small file per
visitor under `/var/lib/php/sessions` on a 100 GiB ext4 `/var` volume. A
config-management cleanup removed the cron job that deleted expired
sessions. About 60,000 new session files (around 1.2 KB each) piled up
every hour.

Four and a half days later every login and checkout started failing:
the app could not create a session file and logged `No space left on
device`. On-call opened the disk dashboard and saw `/var` at 39% used, so
the first hour of the incident went into checking the application and the
load balancer. Nobody was paged before customers were, because the disk
alert had never fired.

## Root cause
**The disk alert watched bytes only.** ext4 creates a fixed number of
inodes at format time (one per 16 KiB by default, so 6,553,600 on a
100 GiB volume), and every file needs one, however small. Millions of tiny
files use up the inodes long before they use up the blocks. `df -h` shows
plenty of space; `df -i` shows 100%.

In the simulation (15 GiB and 200,000 inodes already in use, 60,000
session files per hour, no cleanup), new files fail from **hour 106**, when
inodes are at **100%** and bytes are at **39.2%**. The original rule
(`bytes used >= 90%`) does not fire once in the whole simulated week.

## Solution
`disk_alert` checks **both** resources with the same thresholds (warning
at 85%, critical at 95%) and returns the worse one, naming whether it is
`bytes` or `inodes` so on-call knows which `df` to run:

- On the same week, an **inode WARNING fires at hour 90** (16 hours before
  writes fail) and **CRITICAL at hour 101** (5 hours before).
- If both resources are at the same severity, the fuller one is reported,
  because it fails first.
- Filesystems that allocate inodes dynamically (btrfs, many network
  filesystems) report 0 total inodes; they are checked on bytes only,
  instead of dividing by zero or alerting falsely.
- `directories_by_file_count` ranks directories by **number of files**,
  not size. `du` ranks by bytes and points at the large log and database
  files, which are not the problem here.

## Trade-offs
- Another alert per filesystem means more rules to tune. Using the same
  thresholds for bytes and inodes keeps it to one mental model.
- Thresholds alone give 16 hours of warning at this growth rate; a faster
  leak gives less. Combining this with a fill-rate forecast (problem 004)
  on inode usage gives earlier pages for fast leaks.
- The real fix is restoring the session cleanup (or moving sessions to
  Redis or the database). The alert only makes sure the next leak is
  noticed before users notice it.
- The inode count cannot be raised on a live ext4 filesystem. Volumes that
  are expected to hold many small files should be formatted with a smaller
  `-i` bytes-per-inode ratio, or use XFS, which allocates inodes dynamically.

## Run
```bash
python -m unittest -v test_solution.py
```

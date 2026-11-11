# 045 · Edge proxies crashed with "Expecting value: line 1 column 1" a few seconds into every routing deploy

## Problem
A fleet of 300 edge proxies loads its routing table from `/etc/edge/routes.json`
and polls the file every second, reloading when its modification time changes.
A deploy agent on each host pushes new routes by opening the file with `"w"`
and writing the new JSON (about 3 KB for 50 routes).

On most deploys nothing happened. But on every deploy a handful of proxies
logged `json.decoder.JSONDecodeError: Expecting value: line 1 column 1` or
`Unterminated string`, the reload thread died, and the proxy kept running with
no routing table at all: every request got a 404 until the process was
restarted by hand. On a busy day this was 1-3% of the fleet per deploy, and
each one dropped all traffic for its region slice for several minutes.

## Root cause
Opening a file with `"w"` truncates it to zero bytes first, then the bytes are
written. Between the truncate and the last write, the file on disk is empty or
partial, and nothing stops another process from reading it at that moment. A
reader polling once a second will, across hundreds of hosts, eventually land in
that window. The reader then trusted whatever it read: no validation and no
fallback to the config it was already running.

## Solution
`solution.py` fixes both sides:

1. **`atomic_write_config`** writes the new version to a temp file *in the same
   directory*, flushes and `fsync`s it, then calls `os.replace` to rename it over
   `routes.json`. On POSIX a rename within one filesystem is atomic: readers see
   either the whole old file or the whole new file. If the write is interrupted,
   only the temp file is incomplete; it is removed and the live file is unchanged.
2. **`ConfigReloader`** parses and validates (`version` and a non-empty
   `routes` table) before switching. On a bad, partial or missing file it keeps
   the last good config, counts the rejection and records the error for an
   alert. It refuses to start only if there was never a good config.

The tests reproduce the race deterministically with `InterruptedWriter`, which
stops after N bytes, instead of relying on real timing: the naive writer leaves
an empty or truncated file that the naive reader crashes on, while the atomic
writer leaves the old file in place and the reloader keeps serving it.

## Trade-offs
- `os.replace` is atomic only within one filesystem; a temp file in `/tmp`
  would make it a copy. On Windows, replacing a file another process holds open
  can fail, so retry there.
- Readers that keep the file open (instead of reopening) still see the old
  inode after the rename; reopen on every reload.
- Fsyncing the file protects the contents; for full crash safety also fsync
  the directory after the rename, which costs a little more latency per deploy.
- Kubernetes ConfigMap volumes already use an atomic symlink swap; this matters
  for files written by agents, `scp`, configuration management or hand edits.
- Keeping the last good config means a broken deploy is silently ignored unless
  `rejected_reloads` / `last_error` is exported and alerted on.

## Run
```bash
python -m unittest -v test_solution.py
```

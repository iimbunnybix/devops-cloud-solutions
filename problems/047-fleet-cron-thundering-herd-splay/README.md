# 047 · The metrics server timed out at the top of every hour, and was idle for the other 59 minutes

## Problem
A company runs 4,000 Linux hosts. Every host has the same crontab line from
the base image: `0 * * * * /usr/local/bin/metrics-sync`, which uploads
inventory and package data to a central server. That server handles about
25 sync requests per second comfortably.

Every hour at :00 the server's load balancer showed thousands of connections
in one second, request latency went past the 30-second client timeout, and
most hosts logged `sync failed: timeout`. The hosts that failed retried at the
next :00 together with everyone else, so the same hosts kept missing data;
the patch-compliance dashboard showed 30-40% of the fleet as "unknown" for
days. Doubling the server size did not help, and between 00:01 and 00:59 the
server sat almost idle.

## Root cause
All hosts are NTP-synced to within milliseconds and run the same schedule, so
their start times are not just similar, they are identical. In the replay
(`test_solution.py`), all **4,000** requests arrive in the same second: 160
times the server's capacity, for one second, every hour. The total work per
hour is small; the problem is that none of it is spread out.

This is different from a job overlapping its own previous run (027) or a
local-time cron firing twice on a DST change (032): here each host is
correct on its own, and the failure only appears when the whole fleet is
summed.

## Solution
`solution.py` gives each host a fixed offset ("splay") inside a window:

1. **`splay_offset_seconds`** hashes `"<job>:<hostname>"` with SHA-256 and
   takes it modulo the window (600 s by default). Python's `hash()` is salted
   per process, so it would change the offset after every restart; SHA-256
   gives the same answer on every host and in every process. The job name is
   part of the key so two different jobs on one host do not always collide.
2. **`splayed_cron_line`** turns the offset into a crontab line. Cron only has
   minute resolution, so seconds become a short `sleep`:
   `1 * * * * sleep 50 && /usr/local/bin/metrics-sync` for `node-00001`.
3. **`peak_requests_per_second`** and **`seconds_over_capacity`** measure the
   load the server sees, so a window can be checked before it is rolled out.

Replaying one hour for 4,000 hosts against a 25 requests/second server:

| Schedule | Peak requests in one second | Seconds over capacity |
|---|---|---|
| Original (`0 * * * *` everywhere) | 4,000 | 1 |
| Splay over 60 s | 80 | 60 |
| Splay over 600 s | 18 | 0 |

The 60-second row shows that the window has to fit the fleet: 4,000 hosts in
60 seconds is about 67 per second on average, still well over capacity.

Because the offset depends only on the hostname, adding 500 hosts does not
move any existing host, and a host's sync always happens at the same minute,
which keeps logs and debugging predictable.

## Trade-offs
- A hash spreads hosts randomly, not perfectly: with 600 s and 4,000 hosts the
  average is 6.7 per second but the peak is 18, and per-minute counts range
  from 358 to 435. Assigning slots by host index would be perfectly even, but
  every host would move whenever the fleet grows or shrinks.
- Data from late-splayed hosts is up to 10 minutes older. Pick the window from
  capacity: roughly `fleet size / (capacity per second)` with headroom.
- Random jitter at each run (`sleep $((RANDOM % 600))`, or systemd timers'
  `RandomizedDelaySec`) also spreads load, but a host's run time changes every
  hour. systemd's `FixedRandomDelay=yes` gives a stable per-host delay like
  this code.
- Splay only spreads the scheduled start. Retries after failure also need
  backoff with jitter (001), and the server should still rate-limit and shed
  load instead of trusting every client to behave.

## Run
```bash
python -m unittest -v test_solution.py
```

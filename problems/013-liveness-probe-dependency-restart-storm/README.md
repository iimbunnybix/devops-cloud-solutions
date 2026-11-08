# 013 · A ten-minute database slowdown took the whole API down, and it stayed down after the database recovered

## Problem
An orders API runs as 20 pods on Kubernetes. Its `/healthz` endpoint, used
for the liveness probe, runs `SELECT 1` against PostgreSQL with a 1-second
timeout ("if we can't reach the database we're not healthy"). The probe runs
every 10 s and the kubelet restarts a container after 3 consecutive failures.
Each pod needs about 45 s to start and warm its caches.

During a vacuum-heavy maintenance window the primary database slowed to
~2.5 s per query for ten minutes. Requests got slow, which was expected. Then
every pod was restarted at roughly the same time, then restarted again, and
the API returned 503s for about eight minutes in total because there were no
pods running at all. Even after the database had recovered, there were no pods
running for almost another minute, and the API took more than a minute to get
back to full capacity while pods sat in crash-loop back-off.

## Root cause
**The liveness probe checked a shared dependency.** Liveness should detect
local failures that a restart fixes (deadlock, wedged event loop, corrupted
in-process state). A slow database is not fixed by restarting the API; it is
the same database for every pod, so every probe failed together and the
kubelet restarted the whole fleet. Restarts made it worse: starting pods
served nothing, opened new connections to the struggling database, and the
kubelet's crash-loop back-off (10 s, doubling to a 300 s cap) kept pods down
after the cause was gone.

The second-by-second simulation in `solution.py` reproduces the incident with
Kubernetes defaults (back-off 10, 20, 40, ... s): **100 restarts, 490 seconds
with zero running pods (55 of them after the database had already recovered),
and full capacity only 64 seconds after the database recovered.**

## Solution
- **`local_liveness`**: the liveness endpoint (`/livez`) checks only the
  process itself (for example, a heartbeat from the request loop and worker
  threads). With it the same scenario has **0 restarts and all 20 pods
  running throughout**, while a truly hung pod is still detected and
  restarted.
- **Dependencies are handled per request**: timeouts, circuit breakers (see
  backend-api-solutions 009) and degraded responses. The API keeps answering
  (slowly, or with errors for the affected endpoints) and recovers the moment
  the database does.
- **Readiness for local readiness**: use the readiness probe for things that
  are specific to one pod (cache not warmed yet, pod overloaded, draining). If
  readiness fails on every pod because of a shared dependency, the load
  balancer has nowhere to send traffic either.
- **`check_probe_config`**: a lint for deployment manifests that flags any
  liveness check on a shared dependency and liveness that is identical to
  readiness.

## Trade-offs
- With local-only liveness, a pod whose database connection pool is
  permanently broken (a bug only a restart fixes) will not be restarted
  automatically. Detect that specific case locally (pool reports no healthy
  connections for N minutes while other pods are fine) instead of probing the
  database.
- Failing readiness on a dependency outage is sometimes right (traffic can
  go to another region or cluster that is healthy). It is only harmful when
  every replica fails together and there is nowhere else to go.
- Larger `failureThreshold` or `timeoutSeconds` only delays the storm; a
  long enough outage still triggers it.
- Use a `startupProbe` for slow-starting apps so liveness does not kill pods
  that are still booting.
- The simulation ignores the extra database load from reconnecting pods,
  which in real incidents makes the slowdown itself last longer.

## Run
```bash
python -m unittest -v test_solution.py
```

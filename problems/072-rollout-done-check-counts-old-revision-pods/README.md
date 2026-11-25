# 072 · The pipeline marked checkout v42 "deployed" in 0 seconds while every v42 pod was crash-looping

## Problem
The checkout service runs as a Kubernetes Deployment with 4 replicas and a
rolling update of `maxSurge: 1, maxUnavailable: 0`. After `kubectl apply`,
the deploy script waited for the rollout with its own check: "number of
ready pods with label `app=checkout` is at least 4", then ran smoke tests,
posted "v42 deployed" in the release channel and closed the change ticket.

v42 had a missing environment variable and crashed on start. Because of
`maxUnavailable: 0` the old v41 pods kept serving, so customers saw nothing
at first. But the pipeline reported success, the smoke tests passed
(against v41), and the team shipped a database migration that only v42
understood an hour later. That broke checkout for real.

## Root cause
**The completion check counted pods by app label, not by revision.**
During a rolling update old and new pods carry the same `app` label, and
the old pods stay ready until new ones replace them. "4 ready pods" was
true before a single v42 pod existed.

In the simulation (4 replicas, checks every 10 s), the naive check reports
**complete at 0 s** for the crash-looping release, while all ready pods are
v41. It also reports a healthy release complete at 0 s, before any v42 pod
is ready, so post-deploy smoke tests always ran against the old version.

## Solution
`rollout_status` checks the pods of the **target revision** only (in
Kubernetes, the `pod-template-hash` of the new ReplicaSet, or simply use
`kubectl rollout status`, which compares the Deployment's updated and
available replica counts):

- **Complete** only when all 4 replicas are new *and* ready *and* no
  old-revision pod is left.
- **Failed** as soon as a new pod has restarted more than 3 times: a crash
  loop will not fix itself, so the pipeline should roll back now.
- **Timed out** when nothing is decided by the 600 s progress deadline
  (the Kubernetes default `progressDeadlineSeconds`), for example a pod
  that runs but never passes its readiness probe.

In the same simulation the healthy release completes at **120 s**, with 4
pods all on v42; the crash-looping release **fails at 80 s**; the
never-ready release **times out** after 600 s instead of hanging the
pipeline. A couple of restarts during startup are tolerated.

## Trade-offs
- `kubectl rollout status` (or the Deployment's `Progressing` condition)
  already does the revision-aware part. Hand-written checks drift from it;
  prefer the built-in and add only what it lacks, such as the fast
  crash-loop failure.
- The restart limit is a guess: an app that legitimately restarts a few
  times while a dependency warms up needs a higher limit, and a lower one
  rolls back sooner. Pair it with a startup probe so slow starts do not
  count as crashes.
- "Ready" only means the readiness probe passed. A release that is ready
  but returns errors still completes; a canary analysis gate (problem 003)
  catches that.
- A timeout tells you the rollout is stuck, not why; the pipeline should
  print the new pods' events and last logs when it fails.

## Run
```bash
python -m unittest -v test_solution.py
```

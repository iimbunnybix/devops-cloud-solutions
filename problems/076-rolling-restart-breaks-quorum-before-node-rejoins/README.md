# 076 · Every write to the config store failed for 35 seconds during a "zero-downtime" rolling restart

## Problem
A platform team runs a 3-node etcd-style cluster that stores service
discovery and feature configuration for about 200 services. Writes need a
majority of voters (2 of 3), so the cluster is designed to survive one node
being down at a time. To apply a kernel patch, an engineer ran the team's
rolling restart script: restart a node, wait until its client port answers,
move on to the next.

The script finished in 15 seconds and printed "all nodes up". During that
time every write to the cluster failed for **35 seconds**: deploys could not
register new instances, two services' leader elections timed out and both
restarted, and the feature-flag service returned stale values. The same
script had been "fine" for months on a test cluster with almost no data,
where nodes caught up in under a second.

## Root cause
**"Port open" was used as "member is healthy".** After a restart, a quorum
node opens its port within seconds (5 s in the model) but must then fetch
and replay the log entries it missed before it is an in-sync voter (30 s in
the model). The script restarted node 2 while node 1 was still catching up,
which left one in-sync voter out of three. With no majority, the cluster
refuses writes until two nodes are back in sync.

In the model, restarts happen at 0 s, 5 s and 10 s. Quorum is lost from 5 s
to 40 s: **35 seconds of failed writes**. A 5-node cluster restarted the same
way also loses quorum for **35 seconds**, so a bigger cluster does not save
you if the script outruns catch-up.

This differs from problem 017 (batch size vs. serving capacity of a
stateless web tier). Here one node at a time is already the right batch
size; the bug is not waiting until that one node really rejoined.

## Solution
`health_gated_rolling_restart` checks the cluster, not the process, at
every step:

1. **Before restarting a node**, wait until the *other* nodes alone have
   enough in-sync voters for a majority. Then taking this node down cannot
   lose quorum, even if the cluster was already degraded.
2. **After restarting it**, wait until the node reports as an in-sync
   voting member, not just until its port opens.
3. Each wait has a **120 s timeout**. On timeout the rollout **aborts** and
   leaves the remaining nodes alone, with a reason such as
   `node-1 not in sync after 120s` or
   `not restarting node-1: only 1 other in-sync voters, need 2`.

Results from the model:

| Scenario | Rollout | Duration | Seconds without quorum |
|---|---|---|---|
| 3 nodes, healthy | port-open script | 15 s | 35 |
| 3 nodes, healthy | health-gated | 105 s | 0 |
| 5 nodes, healthy | port-open script | 25 s | 35 |
| 5 nodes, healthy | health-gated | 175 s | 0 |
| 3 nodes, node-1 never catches up | health-gated | aborts at 120 s, 1 node restarted | 0 |
| 3 nodes, one already crashed | health-gated | aborts at 120 s, 0 nodes restarted | 0 |
| 5 nodes, one already crashed | health-gated | completes all 5 | 0 |

In a real cluster, "in sync" comes from the cluster itself: etcd's
`endpoint status` raft index matching the leader, ZooKeeper's `mntr`
showing the node as a follower with the leader's zxid, Kafka KRaft's
`describe --replication` lag of 0.

## Trade-offs
- **Slower.** The rollout takes 35 s per node instead of 5 s. On large data
  sets catch-up can take minutes; that time is the real cost of keeping the
  cluster available, not overhead.
- **An abort can leave the cluster with no spare node.** When node-1 never
  catches up, writes still work (2 of 3), but one more failure would lose
  quorum. The abort must page someone; it is not a quiet "rollout failed".
- **The timeout is a guess.** Too short and big catch-ups abort healthy
  rollouts; too long and a broken node sits out of the cluster for that
  long. Size it from measured catch-up times.
- **Health is read from the cluster's own view.** If the health endpoint
  lies (for example reports "member" before catch-up), the gate is
  worthless. Test it against a node with a large backlog.
- Restart the **leader last** in real clusters: each leader restart forces
  an election and a short write pause. Kubernetes operators for etcd,
  ZooKeeper and Kafka apply the same gate through readiness checks plus a
  PodDisruptionBudget of `maxUnavailable: 1`.

## Run
```bash
python -m unittest -v test_solution.py
```

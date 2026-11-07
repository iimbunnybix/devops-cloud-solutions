# 038 · Login went down twice during a routine Kubernetes node upgrade

## Problem
A platform team upgraded a production cluster's nodes to a new Kubernetes
version with an automated job that drained two nodes at a time
(`kubectl drain --delete-emptydir-data --ignore-daemonsets`), rebooted
them and moved on. The login service runs 3 replicas; checkout and search
run 2 each. Pods take about a minute to pull, start and pass readiness.

Halfway through the first batch, every login attempt failed for about a
minute. It happened again on the second batch. Checkout and search had
shorter outages of their own. The upgrade was "successful" according to
the job, and nothing in the deployment changed, so the incident looked
random until someone lined up pod events with the drain log.

## Root cause
**No PodDisruptionBudget, so the drain was allowed to evict every
replica at once.** A drain evicts all pods on the cordoned nodes. The
scheduler had placed all three login pods on the first two nodes, so the
first batch deleted all of them before any replacement was ready. The
replacements were scheduled onto the nodes of the *second* batch (the least
loaded schedulable nodes at that time), so the second batch evicted them
all again.

The simulation in `solution.py` (5-second ticks, 60 s pod start-up, 120 s
node reboot) reproduces it: login reaches **0 ready pods for 110 seconds
in total**, and checkout and search each have **55 seconds** at zero.

## Solution
- **`budget_eviction_allowed`** models the Eviction API with a PDB
  (`minAvailable`): an eviction is refused when it would leave the service
  with fewer *ready* pods than its budget. `kubectl drain` retries refused
  evictions, so pods leave one at a time, each only after a replacement is
  ready. Pods that are not ready yet can always be evicted, because removing
  them costs no capacity.
- With budgets `login: 2, checkout: 1, search: 1`, the same upgrade keeps
  login at **2 ready pods or more** and no service ever reaches zero. The
  drain takes **490 s instead of 250 s**, which is the price of waiting for
  replacements.
- **`check_budgets`** lints budgets before an upgrade: a service with no
  budget, or with `min_available >= replicas` (which can never allow an
  eviction).
- **Drain timeout**: if evictions are refused for `DRAIN_TIMEOUT_SECONDS`
  (30 minutes), the drain stops and reports the stuck pods
  (`login@n1, login@n1, login@n2`) instead of hanging forever or
  force-deleting them.

## Trade-offs
- A PDB only protects against *voluntary* disruptions (drains, the cluster
  autoscaler, evictions). A node that crashes still takes its pods with it;
  spread replicas across nodes and zones too (see problem 024).
- Budgets slow down upgrades and can block them: a single-replica service
  with `minAvailable: 1` can never be drained without downtime. Fix the
  replica count, or accept a short outage and document it.
- The simulation assumes unlimited node capacity. In a full cluster the
  replacement pod may stay Pending, the budget keeps refusing evictions,
  and the drain times out; keep spare capacity (or surge nodes) for upgrades.
- Readiness is modelled as a fixed 60 s start-up; real start-up times vary,
  and a pod that never becomes ready blocks the drain until the timeout.

## Run
```bash
python -m unittest -v test_solution.py
```

# 024 · Login was down for every user during a single-zone power outage, although the service ran six replicas

## Problem
The login service runs 6 replicas of 500 millicores each on a cluster with
nodes in three availability zones, and needs 4 replicas to carry peak
traffic. The team believed six replicas made it highly available. When one
zone lost power, all six replicas went down together and nobody could sign
in until the zone came back. The other two zones were healthy and had plenty
of free CPU the whole time.

## Root cause
To save money the scheduler scored nodes "most allocated first" (bin
packing): each new pod went onto the fullest node that still had room. Nothing
in the scoring knew about zones. In the test cluster (`a-1` with 3,000m free,
`b-1` and `c-1` with 4,000m free) `naive_bin_pack` places all 6 replicas on
`a-1` in `zone-a`, so losing `zone-a` leaves 0 replicas. Replica count alone
says nothing about failure domains.

## Solution
`spread_place` adds a zone spread rule in front of bin packing, the same idea
as Kubernetes `topologySpreadConstraints` with
`topologyKey: topology.kubernetes.io/zone`, `maxSkew: 1` and
`whenUnsatisfiable: DoNotSchedule`:

1. Look at zones with the fewest replicas of this service first.
2. Skip a zone if placing the replica there would make the difference
   between the busiest and the emptiest zone larger than `max_skew`.
3. Inside the chosen zone, still pick the tightest-fitting node, so packing
   savings are kept where they are safe.
4. If no zone qualifies, raise `UnschedulableError` instead of quietly packing
   the healthy zones. A full zone stays in the count, so it blocks the others
   from running ahead; ignoring it would bring back the original risk.

`survives_any_zone_loss` checks a placement: for every zone, the replicas
outside it must still be at least `min_available`. The same cluster now gets
2 replicas per zone and keeps 4 running whichever zone is lost.

## Trade-offs
- `DoNotSchedule` can leave pods pending when one zone is full or degraded.
  That is the point (it is loud), but it can block a scale-up during an
  incident. `ScheduleAnyway` (prefer spread, never block) is the softer
  alternative; pair it with an alert on `survives_any_zone_loss`.
- Spreading costs more: replicas no longer fill the cheapest nodes first, and
  cross-zone traffic is billed by most cloud providers.
- Placement only covers new pods. Later node drains, autoscaler scale-downs
  or zone recovery can unbalance a running service; re-check placements
  periodically or use a descheduler.
- A PodDisruptionBudget and enough spare capacity in each zone are still
  needed: surviving zones must be able to absorb the lost zone's traffic.

## Run
```bash
python -m unittest -v test_solution.py
```

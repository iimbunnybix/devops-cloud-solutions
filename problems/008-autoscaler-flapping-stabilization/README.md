# 008 · Search API replica count sawtoothed all day and p99 latency spiked every few minutes

## Problem
The search API runs on Kubernetes behind a home-grown autoscaler (written
before the team moved to the built-in HPA) that targets 60% CPU per pod.
Traffic was steady, yet the replica graph looked like a saw blade: pods were
added and removed almost every evaluation. Each removed pod took a warm
in-memory index cache with it, each new pod served its first requests cold,
and p99 latency jumped every few minutes. The cluster also spent a lot of time
scheduling and pulling images for pods that lived under a minute.

## Root cause
Every 15 s the autoscaler set `replicas = ceil(cpu_demand / 0.6)` and applied
it **immediately in both directions**, with no tolerance and no memory. Normal
noise in CPU demand (±25% around 6 cores in the simulation) moves that number
up and down by a pod or two each tick. Over one simulated hour (240
evaluations) the naive scaler changed the replica count **200 times**,
bounced between 8 and 13 pods, and started **210 pods** in total, although
the average demand never changed.

## Solution
Apply the rules the Kubernetes HorizontalPodAutoscaler uses by default:

1. **Tolerance band ±10%**: if `utilization / target` is within 0.9–1.1, do
   nothing.
2. **Scale up quickly, but bounded**: at most +100% or +4 pods per step,
   whichever is larger. A real spike (6 → 18 cores) goes 10 → 20 on the next
   tick and 20 → 30 on the one after.
3. **Scale down slowly**: use the **highest** recommendation of the last
   **300 s** (the downscale stabilization window). A dip shorter than 5 minutes
   never removes pods; sustained low demand shrinks once the window has
   passed. The replica count the scaler starts with counts as a
   recommendation too, so a dip right after the autoscaler restarts is held
   for the full window as well.
4. Clamp to `[min_replicas, max_replicas]`.

On the same one-hour load, the stabilized scaler changes the replica count
**once** (10 → 12), starts **2** pods, and stays between 10 and 12 pods.

## Trade-offs
- A long scale-down window costs money: up to 5 minutes of extra pods after
  every peak. Shorten it for spiky batch traffic, lengthen it for services
  with expensive warm-up.
- CPU is a lagging, indirect signal. Request rate or queue depth per pod
  often scales more accurately, and the same stabilization rules apply.
- Scale-up limits slow the response to a sudden 10× spike; pair the
  autoscaler with headroom (min replicas) or scheduled scaling for known
  peaks.
- Prefer the built-in HPA (`behavior.scaleDown.stabilizationWindowSeconds`,
  `policies`) over a custom scaler. The code here shows why those defaults
  exist and how to unit-test scaling behaviour against a recorded load.

## Run
```bash
python -m unittest -v test_solution.py
```

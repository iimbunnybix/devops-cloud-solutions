# 003 · Canary passed its health gate, then the full rollout quadrupled checkout errors

## Problem
Deployments of the checkout service go to a 10% canary first. An automated
gate checks the canary for 15 minutes and promotes it if its 5xx rate is
below 1%. A release with a bug in a payment-provider client passed the gate
and was promoted to 100% of traffic. The site-wide 5xx rate went from
~0.2% to ~0.8% and stayed there until someone noticed the error budget
burning and rolled back by hand.

## Root cause
The gate compared the canary to a **fixed absolute threshold** instead of
to the **current baseline**. Quadrupling a 0.2% error rate still lands under
1%, so the gate was blind to any regression smaller than "5× worse". It also
had no minimum sample size and no latency check. (The test
`test_static_gate_promotes_build_that_quadruples_errors` reproduces this:
a canary at 0.87% errors is promoted.)

## Solution
`CanaryAnalyzer` compares canary and baseline pods **serving traffic at the
same time** (same load, same dependencies, same time of day) and returns
`PROMOTE`, `ROLLBACK` or `CONTINUE`:

- **Error rate**: one-sided test, *"is the canary worse?"*, at
  `alpha = 0.01`. With up to 1,000 total errors it uses an **exact binomial
  test** (given k errors in total, how likely is it that the canary got this
  many of them by chance, given its traffic share?). The z-test's normal
  approximation is badly wrong on tiny counts: one canary error against a
  zero-error baseline gave p ≈ 0.0008 and a rollback, while the exact
  p-value is 5,000/55,000 ≈ 0.091 (tested). Above 1,000 errors the z-test is
  used. A rollback also requires at least a **+25% relative**
  increase, because with millions of requests even a +10% change is
  "significant" (tested: p < 0.01 but still promoted).
- **Latency**: canary p99 must be within **1.2×** baseline p99 (needs at least
  500 samples per side). A slow tail that leaves errors untouched still
  triggers rollback (tested: 352 ms vs 264 ms).
- **Minimum traffic**: below 2,000 requests per cohort the verdict is
  `CONTINUE`, so a few early errors can't decide the rollout either way.

Results on the seeded simulations in the tests:

| Scenario | Verdict |
|---|---|
| Canary 0.87% errors vs baseline 0.19% | ROLLBACK (p ≈ 0) |
| Canary and baseline both at 0.2% | PROMOTE |
| 200 A/A runs of identical builds | 1 false rollback (≈ alpha = 1% as expected) |
| Same error rate, 4× slower tail | ROLLBACK on p99 |

## Trade-offs
- Repeated checks during one canary inflate the false-rollback rate
  (multiple testing). Either keep alpha small, as here, or use a sequential
  test. A false rollback costs a redeploy; a false promotion costs an outage.
- A 10% canary needs enough traffic to detect small regressions. Low-traffic
  services need longer canaries or accept only detecting large effects.
- Baseline must be a fresh deployment of the *current* version (not the
  long-running fleet) to avoid differences from warm caches or pod age.
  Tools like Kayenta/Argo Rollouts do this comparison at scale; the logic is
  the same.
- Business metrics (orders/min, payment success) catch bugs that return
  HTTP 200 with wrong behaviour; add them as extra checks.

## Run
```bash
python -m unittest -v test_solution.py
```

# 036 · The API slowed to a crawl every afternoon while its CPU graph showed only 30%

## Problem
An internal pricing API runs on a few AWS `t3.large` instances. During
office hours it needs about 60% CPU; overnight it idles at a few percent.
The only CPU alert was "CPU above 80% for 5 minutes".

On busy days, from late afternoon the API's p99 latency went from about
100 ms to several seconds and requests started timing out, but the CPU
graph *dropped* from 60% to a flat 30%. No alert fired. Engineers looked
for a downstream problem and found nothing, until the next morning, when
the instances were fast again.

## Root cause
`t3` instances are **burstable**. A `t3.large` (2 vCPUs) has a baseline
of 30% per vCPU and earns 36 CPU credits an hour (0.6 per minute), up to a
cap of 864 credits. One credit is one vCPU at 100% for one minute. Above the
baseline the instance spends credits; at zero it is held at the baseline.

At 60% the instance spends 1.2 credits a minute and earns 0.6, a net burn
of 0.6 a minute. In the reproduction, a balance of 288 credits at 09:00 lasts
exactly **480 minutes**, so from 17:00 the service gets 30% CPU while it
needs 60%, and is throttled for the last **120 minutes** of the day.
Measured CPU falls to the baseline, so `naive_cpu_alert` (above 80%) never
fires. The "fault" disappears overnight because the instance earns credits
back while idle.

## Solution
`solution.py` models the credit balance and alerts on **time to
exhaustion** instead of on CPU percent:

- `simulate` replays a per-minute CPU demand series, spending and
  earning credits, capping the balance and throttling when it is empty;
- `minutes_until_exhaustion` divides the current balance by the net burn
  rate over a recent window (no prediction when the instance earns at
  least as fast as it spends);
- `should_warn` fires when credits will run out within a warning window
  (120 minutes by default). It also keeps firing when the balance is
  already near zero, because a throttled instance's CPU sits at the
  baseline and looks like it has no net burn.

In the reproduction the warning fires at minute **360** (15:00), two hours
before throttling starts, leaving time to move the service to a larger or
non-burstable instance type.

## Trade-offs
- The prediction assumes recent use continues. A sudden spike burns
  credits faster than forecast; a shorter window reacts sooner but is
  noisier.
- In production, read the real `CPUCreditBalance` CloudWatch metric
  instead of simulating it; the simulation here makes the behaviour
  testable and shows how the balance moves.
- T3 "unlimited" mode avoids throttling, but sustained use above the
  baseline is billed as surplus credits. If the daily average is above the
  baseline, a non-burstable type (`m6i`, `c6i`) is usually cheaper and
  more predictable.
- Burstable volumes (gp2 EBS I/O credits) and some managed databases
  have the same failure mode and need the same kind of alert.

## Run
```bash
python -m unittest -v test_solution.py
```

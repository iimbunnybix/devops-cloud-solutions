# 078 · Two big outages three days apart, and the error-budget policy never froze a single deploy

## Problem
A SaaS API has a 99.9% availability SLO and a written error-budget policy:
when the budget is spent, feature deploys stop and the team works on
reliability until it recovers. A weekly report showed "error budget used
this month" from a spreadsheet: errors since the 1st divided by the month's
allowance (planned traffic of 1,000,000 requests/day times 0.1%).

On 31 January a bad config push caused an outage, and on 2 February a
feature deploy caused another. Users saw two outages in three days and a
large customer opened an escalation. Yet the report showed January at 84.5%
and February at 73%: both green, so the freeze never triggered and the team
kept shipping features through the following weeks.

## Root cause
The budget was measured over **calendar months**, so it started again
from zero errors on the 1st. An incident at the end of a month stopped
counting the next morning; the window the policy looked at did not match
the experience users had, which is "how reliable was the service over the
last 30 days?".

In the reproduction (normal days: 200 errors, each incident day 20,000 extra
errors): January ends at **0.845** of its 31,000-error allowance, 1 February
shows **0.007**, and February never goes above **0.914**, so the original
policy allows deploys on every day of both months. Over the 30 days ending 2
February there were 46,000 errors against a 30,000 budget: **153%**.

## Solution
Compute the budget over a **rolling 30-day window** every day
(`rolling_budget_used`), against the requests actually served in that
window:
- 1 February: 26,000 errors / 30,000 allowed = **0.867**; the first
  incident still counts after the month changes.
- 2 February: **1.533**, so `deploys_allowed` freezes deploys that day.
- The freeze lifts on **2 March**, when the 31 January incident leaves the
  window; nothing about the calendar decides it.

## Trade-offs
- A rolling window recovers gradually, one day at a time, and the date the
  freeze lifts is set by when an old incident ages out, which can feel
  arbitrary. Teams often add a rule like "and at least N days since the last
  incident".
- Calendar windows are still useful for **contractual SLAs** that are
  written per month and pay credits per month. Report both, but drive the
  engineering policy from the rolling one.
- Using actual requests (instead of planned traffic) keeps the budget honest
  on low-traffic days, but a quiet day with a few errors counts more. A
  time-based SLO ("bad minutes") is the alternative for very spiky traffic.
- Burn-rate alerts (problem 002) catch fast budget burn during an incident;
  the rolling budget decides what the team does afterwards. Both are needed.

## Run
```bash
python -m unittest -v test_solution.py
```

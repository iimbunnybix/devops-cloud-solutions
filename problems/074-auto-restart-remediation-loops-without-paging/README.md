# 074 · Search returned errors for almost two hours while the auto-remediation bot restarted it 110 times and paged no one

## Problem
An operations team runs a remediation bot: when a service's health check
fails, it restarts the service. For a year this quietly fixed the search
service's occasional stuck process with one restart, and the team was proud
that search "never paged anyone anymore".

One evening the search node's data disk filled up. Every health check
failed, so the bot restarted the service, which came up, could not write its
index, and failed again a minute later. The bot restarted it every minute
for the rest of the evening. Each restart dropped in-flight queries and
threw away the warm caches, so the few queries that succeeded were slow.
Because the bot handled every failure, no alert reached a human. Customers
reported broken search on social media before anyone on the team knew.

## Root cause
**Remediation without a limit or an exit.** A restart is the right answer to
a transient fault and the wrong answer to a persistent one, and the bot
could not tell them apart. It treated "I restarted it" as "it is handled".
In the model (fault at minute 10, two hours simulated), a stuck process is
fixed by **1** restart. A full disk gets **110** restarts, one per failed
check, and **0** pages.

This is not a liveness-probe restart storm caused by a dependency
(problem 013). There the probe was checking the wrong thing. Here the check
was correct, and the response to it never stopped or escalated.

## Solution
`RemediationPolicy` gives automation a budget:

- At most **3 restarts in a sliding 30-minute window**. A restart older than
  the window no longer counts, so a fault that recurs once an hour is still
  fixed automatically every time.
- When the budget is spent and the service is still unhealthy, the bot
  **pages once** with what it already tried ("minute 13: still unhealthy
  after 3 restarts in 30 min; auto-restart paused").
- After paging, it **stops touching the service** until a human calls
  `acknowledge()`. A restart in the middle of someone's debugging destroys
  evidence and undoes their work.

Same scenarios with the policy: the stuck process is still fixed by 1
restart with no page. The full disk gets 3 restarts and one page at
minute 13, instead of 110 restarts and silence.

## Trade-offs
- **The service stays down until a human arrives.** Paging does not fix the
  disk. It only makes sure someone is working on it three minutes in, not
  two hours in. Pair the bot with an alert on the underlying cause (disk
  usage forecast, problem 004) so the page says *why*.
- **Budget size is a guess.** Too small and a flaky service pages for
  something one more restart would fix. Too large and the outage runs
  longer before escalation. Track how often the budget is exhausted and
  tune it.
- **Count remediations as incidents.** Even successful restarts should be
  recorded and reviewed. Frequent "successful" restarts are a bug report.
- Kubernetes' CrashLoopBackOff spaces out restarts but never pages anyone.
  You still need an alert on restart count or crash-looping state.

## Run
```bash
python -m unittest -v test_solution.py
```

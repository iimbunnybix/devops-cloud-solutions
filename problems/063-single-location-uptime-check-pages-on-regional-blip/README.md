# 063 · On-call was woken by 42 "site down" pages in a month, and customers never saw an outage

## Problem
A SaaS company's external uptime monitor requested the login page once a
minute from a single probe location (`us-east`) and paged on-call as soon as
one request failed. Most nights a page arrived, the engineer opened a laptop,
and the site loaded fine; traffic, error-rate and customer-ticket graphs
showed nothing. In the test's simulated 30-day month (seeded, five probe
locations, each with short independent network blips of 1-5 minutes), the
`us-east` probe failed in **136** of 43,200 minutes and the naive monitor sent
**42** pages, none for a real outage. After a few weeks the team started
acknowledging "site down" pages without looking, which is exactly how a real
outage gets missed.

## Root cause
A single probe measures the whole network path from that probe to the site,
not the site. A transit provider rerouting, packet loss on a regional link or
a problem in the monitoring vendor's own data center fails the probe just as
an outage does. With one location and a "one failure pages" rule, every
blip on that path became a page. There is no second opinion to tell
"the site is down" from "this path is having a bad minute".

## Solution
`QuorumChecker` probes from several locations each round:

- **Quorum.** A round counts as failing only when at least `quorum`
  locations fail (default 3 of 5). Independent blips rarely line up: in the
  simulated month at most **2** locations ever failed in the same minute.
  A regional problem hitting two European probes also stays below the quorum.
- **Consecutive rounds.** The state flips to DOWN only after
  `failing_rounds_to_alert` failing rounds in a row (default 2), and back to
  UP only after `healthy_rounds_to_recover` healthy rounds in a row (default
  2), so one bad round neither pages nor ends an incident early.
- **Which locations failed.** Every `Verdict` lists the failed locations, so
  the page says "5 of 5 failing" (global outage) or the dashboard shows
  "eu-west, eu-central failing" (regional problem worth a ticket, not a page).
- **No data is not a failure.** A location whose probe agent sent no result
  is not counted as failing; settings that make the quorum meaningless
  (0, or more than the number of locations) are rejected.

The same simulated month produces **0** pages. With a real 10-minute outage
added at minute 1000, the quorum monitor pages exactly once, at minute 1001,
and resolves at minute 1011.

## Trade-offs
- Detection is one round slower: with a 1-minute interval and 2 failing
  rounds, a real outage pages about a minute later. Probing more often or
  from more locations shortens that without losing the noise filter.
- A quorum hides outages that affect only some users, such as one region or
  one ISP. Alert on those separately at lower urgency (a ticket, or a page
  only if they persist), and use real-user error rates for user-visible
  impact.
- Ignoring missing results means a monitor whose probe agents all go silent
  reports UP. Alert separately on probe agents that stop reporting, or the
  monitor can fail without anyone noticing.
- Probe locations should sit in different networks and providers; five
  locations in the same cloud region share one failure path and vote as one.

## Run
```bash
python -m unittest -v test_solution.py
```

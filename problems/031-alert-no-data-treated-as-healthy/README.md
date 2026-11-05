# 031 · Checkout failed for an hour after a deploy, and the error-rate alert stayed green because its metrics had disappeared

## Problem
The checkout service handles about 1,200 requests a minute. Its only
paging alert is an error-rate rule: page when more than 2% of requests
over the last 5 minutes fail. A release changed the service's HTTP router
and, by accident, stopped serving the `/metrics` endpoint on the port the
monitoring system scraped. The same release also broke a payment code
path, so about 30% of checkouts started failing.

Nobody was paged. The checkout dashboard showed "No data" panels, which
looked like a cosmetic dashboard problem to the one engineer who opened
it. The outage was found through customer support tickets, and the
revenue for that hour was mostly lost.

## Root cause
**The alert treated "no data" as "healthy".** The rule computed
`errors / requests` over whatever scrapes had arrived in the window and
returned OK when the window was empty. That is the default in many
monitoring setups: a Prometheus comparison over an empty series returns
nothing, so the alert never becomes active, and some alerting tools offer
"OK" as the no-data handling.

The two failures were correlated, which is the common case: a bad deploy,
a crashing pod or a dead node takes out the service *and* its metrics at
the same moment. Exactly when the alert was needed, it had nothing to
evaluate.

In the simulation (`broken_deploy_incident`: 60 healthy minutes at 0.3%
errors, then a deploy at minute 60 that raises errors to about 30% and
stops every scrape), the naive rule **never pages** in the two hours. With
the metrics still flowing, the same rule would have paged at minute 60.

## Solution
`evaluate` in `solution.py` has three states instead of two:

1. **NO_DATA** when the last 5 scrapes in a row are missing
   (`ABSENT_AFTER_MINUTES`). It is checked first, because an empty window
   can never show that the service is healthy, and it pages just like
   FIRING. In the simulation it pages at **minute 64**, the fifth missed
   scrape after the deploy, and stays in NO_DATA until metrics return.
2. **FIRING** when the error ratio over the scrapes that did arrive is
   above 2%. If the exporter only flaps (every other scrape lost), the
   remaining scrapes still show the errors and the alert fires.
3. **OK** otherwise, including a window with no traffic at all (0
   requests) and a single missed scrape or up to four in a row, so a
   restart or one slow scrape does not page.

In Prometheus the same idea is a companion rule such as
`absent(checkout_requests_total)` or an `up == 0` rule for the target,
and in tools with a no-data setting, "Alerting" instead of "OK".

## Trade-offs
- No-data alerts page for monitoring failures, not only service
  failures: a broken scrape config or a monitoring outage wakes someone
  up. That is usually right for a top-level paging alert; it can be routed
  to a lower-urgency channel when the service also has a black-box probe.
- Decommissioning a service now needs a step to remove its rules,
  otherwise it pages NO_DATA forever.
- Five minutes of absence before paging is a choice between speed and
  noise from short scrape gaps. Shorter windows need more reliable
  scraping.
- A synthetic check from outside (a probe that places a test checkout)
  catches the same incident without relying on the service's own
  metrics, and is the strongest complement to this rule.

## Run
```bash
python -m unittest -v test_solution.py
```

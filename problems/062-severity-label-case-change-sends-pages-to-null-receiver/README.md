# 062 · A three-hour checkout outage paged nobody because the alert said severity "Critical" instead of "critical"

## Problem
An online retailer routes Prometheus alerts with a routing tree similar to
Alertmanager's: `severity="critical"` pages the on-call engineer,
`severity="warning"` posts to Slack, and `severity="info"` goes to a
`null` receiver that throws alerts away. The **default** receiver (for
anything that matched no route) was also `null`, set years earlier to cut
noise from experimental rules.

A shared logging and alerting library upgrade changed the checkout team's
rule template to emit `severity="Critical"`. Checkout error rate then hit
40% for three hours on a Saturday. The alert fired the whole time, the
dashboard showed it red, and nobody was paged; a customer tweet finally
reached the on-call engineer.

## Root cause
Two decisions combined:

- **Exact, case-sensitive matching.** `"Critical" != "critical"`, so the
  alert matched none of the routes. An alert without a `severity` label at
  all matches none either.
- **A discarding default.** Unmatched alerts went to `null`, so a labelling
  mistake silently became "drop this alert" instead of "someone should look
  at this".

## Solution
`route_alert(labels, routes)`:

- **Normalise routing labels** (`severity`, `team`): trim and lower-case
  their values before matching, so `Critical`, ` CRITICAL ` and `critical`
  all page.
- **Validate severity** against `{critical, warning, info}`. An unknown or
  missing severity is not guessed; it goes to `oncall-unrouted`, a real
  receiver watched by people, with the reason attached.
- **No silent default.** An alert that matches no route also goes to
  `oncall-unrouted`. Dropping an alert now needs an explicit route (here
  `severity="info"` -> `null`).
- **CI check.** `find_unrouted` takes one example alert per alerting rule and
  lists those that would not be routed normally, so the broken template
  fails review instead of an outage.

## Trade-offs
- Normalising in the router hides the inconsistency rather than fixing it.
  Also fix the labels at the source (lint alert rules for allowed label
  values) so dashboards and silences, which also match labels, agree.
- Real Alertmanager has no "normalise" step; the equivalent is a
  case-insensitive regex matcher such as `severity=~"(?i)critical"`, plus a
  default receiver that reaches people, and `amtool config routes test` with
  sample labels in CI.
- An `oncall-unrouted` receiver gets noisy if nobody fixes what lands in
  it. Treat every alert there as a bug in the rule, and keep it out of the
  paging path unless the alert's other labels suggest it is urgent.

## Run
```bash
python -m unittest -v test_solution.py
```

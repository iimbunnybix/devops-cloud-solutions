# 030 · Checkout was down for 40 minutes during a database maintenance window, and on-call never got the page

## Problem
The payments team scheduled a Postgres upgrade on the `pg-eu-1` cluster
for Saturday 02:00-04:00. Before starting, the engineer created an alert
silence so on-call would not be woken by the expected "database down" and
"replication lag" alerts. The quickest matcher that stopped the noise was
`env=prod, region=eu-west-1`, and the silence tool did not require an end
time.

At 02:40 an unrelated bad config push broke the checkout API in the same
region. Its 5xx alert fired and was silenced along with the database
alerts. Customers could not pay for 40 minutes until someone noticed on
social media. Because the silence never expired, a "disk will fill in 4
hours" alert on the search cluster on Monday morning was also hidden, and
that cluster filled up too.

## Root cause
**The silence matched far more than the work being done, and lasted
forever.** A silence is a filter on labels: every alert that has all the
matcher labels is hidden. `env` and `region` are on every production alert
in the region, so the silence covered every service, not only the
database. With no end time it kept doing that after the maintenance was
over.

In the test night (two expected database alerts during the window, the
checkout outage at 02:40, a real database alert at 05:00 and the Monday
disk alert), the naive silence hides **all 5 alerts** and on-call
receives **none**.

## Solution
`create_silence` validates a silence before it exists:

1. **Scoped matchers**: it must match a specific `service` *and* a
   `cluster` or `instance`. Region-only or env-only matchers are rejected.
2. **Mandatory, short expiry**: an end time is required, must be after
   the start, and the window may be at most **4 hours**. Longer work needs
   a new silence, which forces someone to look again.
3. **Owner and reason**: `created_by` and `comment` are required, so anyone
   who later finds a hidden alert knows whom to ask.
4. **Exempt alerts**: alerts labelled `silence_exempt="true"` (for example
   "WAL archiving stopped", which means possible data loss) are always
   delivered.
5. **Audit trail**: `route_alerts` records each silenced alert together
   with the silence that hid it.

With `service=payments-db, cluster=pg-eu-1` from 02:00 to 04:00, only the
**2 expected** database alerts are silenced. The checkout outage, the
05:00 database alert and the Monday disk alert (**3 alerts**) reach
on-call. An alert at exactly 04:00 is delivered (the end is exclusive).

## Trade-offs
- Engineers must know the exact labels of the target; a maintenance UI
  that pre-fills them from the service catalog removes most of that cost.
- Some maintenance really does affect many services (a network change in
  one zone). Those need a reviewed, broader silence, or better, alert
  inhibition rules ("if the zone is down, hide per-service alerts in that
  zone") instead of a blanket silence.
- A 4-hour cap means long migrations need the silence extended, which can
  be annoying. That is the point: each extension is a moment to check that
  the silence still matches only what it should.
- Silenced alerts still exist. Showing "N alerts hidden by your silence" to
  its owner in chat during the window catches a mistake within minutes.

## Run
```bash
python -m unittest -v test_solution.py
```

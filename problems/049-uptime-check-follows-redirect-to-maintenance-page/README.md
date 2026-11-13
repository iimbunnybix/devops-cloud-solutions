# 049 · Checkout was down for 40 minutes while the uptime dashboard stayed at 100%

## Problem
An online shop runs a synthetic monitor that requests
`/checkout/healthz` every minute and pages on-call when it fails. During a
40-minute checkout database outage, customers could not pay and support
tickets piled up, but the monitor stayed green: in the test, all **40 of 40**
checks during the outage are reported healthy and availability shows
**100%**. On-call was paged by the support team, not by monitoring, and the
outage's start time had to be reconstructed from logs.

## Root cause
The application's error middleware answered every request it could not
serve with `302 Found` to a static `/maintenance` page, which returned
`200 OK`. The monitor used an HTTP client that **follows redirects** and then
checked only the **final status code**. "Something answered 200 at the end of
a redirect chain" was treated as "checkout is up", even though the page that
answered was the one that exists precisely to say it is not.

## Solution
`check` evaluates an explicit `CheckSpec`:
1. **No redirect following.** A health endpoint that redirects is itself a
   symptom (maintenance mode, expired auth, misrouting). The failure reason
   includes the Location, e.g. `status 302 to https://.../maintenance`, so
   on-call sees the cause right away.
2. **Exact expected status** (200 by default). A route that is supposed to
   redirect can declare `expected_status=302`.
3. **Content marker.** The body must contain a string only the real page
   produces, such as `"status":"ok"` from the health endpoint, so a generic
   200 page (maintenance, CDN error page, captive portal) fails the check.

With the fix the same outage shows 0 of 40 checks healthy (0% availability).
`availability([])` returns 0.0, so a monitor that recorded nothing does not
look perfectly available.

## Trade-offs
- Checks that follow redirects are still useful for "can a user get from the
  homepage to the login page", but they need content assertions at the end.
- Content markers are coupled to the page; changing the health payload must
  update the monitor. Keep the marker on a dedicated health endpoint rather
  than on marketing pages that change often.
- A health endpoint that checks the database can itself cause load or fail
  for reasons users do not see; pair it with checks on real user journeys
  and with error-rate alerts from production traffic.

## Run
```bash
python -m unittest -v test_solution.py
```

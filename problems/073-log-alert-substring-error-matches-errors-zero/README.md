# 073 · On-call was paged every minute all night by an error alert while nothing was failing

## Problem
A platform team alerts on application logs: if more than 10 lines per
minute match `error` (case-insensitive), page on-call. It had been quiet
for months. Then a routine deploy added two counters, `errors=0 retries=0`,
to the heartbeat line each of 40 workers logs every minute. The same week
the docs site published an `/docs/error-codes` page that gets about two
requests a second.

From that night the alert fired every minute. The on-call engineer found
nothing wrong, silenced it for 8 hours, and during that silence a real
payment-provider outage went unpaged for 40 minutes.

## Root cause
**The alert matched a substring, not a log level.** "error" appears in
field names (`errors=0`), URLs (`/docs/error-codes`) and message text. In
the model, a perfectly healthy minute has **160** matching lines (40
heartbeats + 120 docs requests), sixteen times the threshold. A real
incident with 25 failing payments only moves the count from 160 to **185**,
so even a tuned threshold could not separate the two.

This is not alert deduplication (problem 015) or a flapping threshold: the
signal itself was wrong.

## Solution
- `parse_logfmt` reads each line's `key=value` and `key="quoted value"`
  fields, so `level=ERROR` inside a quoted message is not mistaken for the
  level.
- `summarize` counts only lines whose `level` is ERROR, FATAL or CRITICAL
  (any case). The healthy minute now has **0** error lines and does not page.
  The incident minute has **25** and pages. Nine errors stay below the
  threshold.
- Lines with no `level` field are counted as **unparsed**, and
  `alert_is_blind` raises a warning when more than 5% of a minute is
  unparsed, or there are no logs at all. A logger that switches to JSON
  would otherwise drop the error count to zero and the alert would look
  healthy forever.

## Trade-offs
- **Depends on a log format.** Structured logs with a level field are the
  real fix; this parser only handles logfmt. JSON or syslog sources need
  their own parser feeding the same counts. Multi-line stack traces count as
  unparsed lines, which is why the blind check uses a share, not "any".
- **Better: alert on metrics, not log text.** An `errors_total` counter or the
  request error ratio (problem 039) is cheaper and more stable than parsing
  logs. Log-based alerts are a stop-gap for services that do not export
  metrics yet.
- **Not every ERROR line is an outage.** A noisy service that logs
  client mistakes at ERROR will still page. Fix the log levels at the
  source, or alert on a ratio of errors to requests.
- A tighter regex like `\bERROR\b` would have fixed this incident but still
  match "ERROR" in user-supplied text such as search terms.

## Run
```bash
python -m unittest -v test_solution.py
```

# 055 · On-call set RECOMMENDATIONS_ENABLED=false to stop a failing feature, but it stayed on for another 40 minutes

## Problem
The product page service calls a recommendations backend on every request.
When that backend started timing out, page latency climbed and conversion
dropped. The runbook says to turn the feature off with its kill switch, so the
on-call engineer set `RECOMMENDATIONS_ENABLED=false` on the deployment and
watched the rollout finish. Nothing changed: pods kept calling the failing
backend. Thinking the rollout had not picked up the change, the team restarted
pods twice and tried `0` instead of `false`, before someone read the code and
removed the variable entirely. The outage lasted about 40 minutes longer than
it needed to, because the switch that was supposed to end it did nothing.

## Root cause
The service read the switch with `bool(os.environ["RECOMMENDATIONS_ENABLED"])`
(`naive_flag`). Environment variables are always strings, and in Python every
non-empty string is truthy: `bool("false")`, `bool("0")` and `bool("off")`
are all `True`; only the empty string is `False`. The kill switch could only be
turned "off" by setting it to an empty value, which nobody would guess, and no
error or log line showed that `false` had been read as `True`.

## Solution
`parse_bool` accepts an explicit list of spellings (`1/true/yes/on` and
`0/false/no/off`, case-insensitive, surrounding spaces ignored) and raises
`InvalidFlagError` for anything else, including an empty value. `strict_flag`
uses the default only when the variable is absent. `load_flags` reads every
switch at startup and collects all bad values into one error, so the pod fails
to start (and the rollout stops) instead of running with a guessed value.

Failing at startup is the point: a kill switch with an unreadable value should
make the deploy fail loudly, not silently keep the feature in its current state.

## Trade-offs
- Strict parsing turns a value that used to "work" by accident (for example
  `FLAG=enabled`) into a failed deploy. Run the parser in CI against every
  manifest before switching it on in production.
- An env-var switch still needs a rollout to change. A runtime feature-flag
  service flips in seconds, but adds a dependency that must have a safe
  default when it is unreachable.
- Rejecting the empty string is a choice; some teams treat `FLAG=` as "unset".
  Either rule is fine as long as it is explicit and tested.
- Libraries such as pydantic-settings already parse booleans strictly; this
  standard-library version shows the rule they implement.

## Run
```bash
python -m unittest -v test_solution.py
```

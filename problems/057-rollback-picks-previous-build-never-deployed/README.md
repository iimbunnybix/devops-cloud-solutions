# 057 · The emergency rollback deployed a release that had never run in production, and the second rollback brought the broken release back

## Problem
A payments API is deployed several times a day. The on-call runbook says:
"if a deploy is bad, run `rollback.sh prod`". The script asked the CI system
for the list of built artifacts and redeployed the one built just before the
version currently running.

One evening release `1.4.2` started returning errors. The previous build,
`1.4.1`, had been built but **failed QA in staging and was never deployed**;
it carried a half-finished migration. `rollback.sh` shipped `1.4.1`, and the
error rate got worse. The engineer then rolled forward manually to `1.4.0`
and, when a second issue appeared, ran `rollback.sh` again: this time the
script picked `1.4.2`, the release that had started the incident. The outage
lasted much longer than the original bad deploy would have.

## Root cause
The script answered "what was built before this?" when the question is
"what was **running and healthy** before this?". The build list includes
artifacts that failed QA, were built for other environments, or were never
promoted. It also has no memory of rollbacks, so it cannot know which
releases are known to be bad. And `versions[position - 1]` with
`position == 0` is `versions[-1]` in Python: rolling back from the oldest
build silently wraps around to the **newest** one.

## Solution
`rollback_target` uses the **deploy history of the target environment**:

- Only **successful** deployments **to this environment** count; staging
  deploys and failed rollouts are ignored.
- A version that was **replaced by a rollback** is marked bad and never
  chosen again.
- A version must have stayed live for at least **30 minutes** to be
  trusted; one replaced after five minutes may have been replaced for a
  reason.
- The version running now (including a redeploy of it) is skipped.
- If nothing qualifies, it raises `NoRollbackTarget` so a human chooses,
  instead of guessing or wrapping around.

In the tests the first rollback picks `1.4.0`, and a second rollback after
that picks `1.3.9` instead of `1.4.2`.

## Trade-offs
- It relies on the deploy log being complete; deploys done by hand outside
  the pipeline are invisible. Make the pipeline the only way to deploy.
- Code is not the only thing that changes: a rollback past a database
  migration still needs a backward-compatible schema (see problem 025).
- The 30-minute health window is a heuristic; teams with canary analysis can
  record an explicit "verified healthy" flag per deployment instead.
- Tools such as Argo Rollouts and Spinnaker keep this history for you; the
  bug usually lives in hand-written scripts around plain CI artifacts.

## Run
```bash
python -m unittest -v test_solution.py
```

# 006 · A routine deploy silently undid an emergency memory fix, and the service started OOM-crashing again

## Problem
During an incident, an on-call engineer raised the `checkout` service's memory
limit from 1Gi to 2Gi and opened a firewall rule directly in the console. The
outage ended, but the changes never went back into Git. Six weeks later a
routine deploy re-applied the Git config: the memory limit went back to 1Gi,
pods started OOM-crashing under peak load, and the same incident happened
again. The manually opened firewall rule (port 22 to the internet) was
found only during a later security review.

A drift detector already existed. It compared the raw JSON from Git and from
the API, reported dozens of differences on every resource on every run, and
had been muted for months.

## Root cause
- **Manual changes outside Git** with no reconciliation step.
- **A detector so noisy it was useless.** For the untouched resource in the
  test, the naive diff reports 15 differences and none of them are real:
  - server-managed fields (`status`, `uid`, `resourceVersion`, timestamps);
  - defaults the server fills in (`imagePullPolicy`, `protocol`, `strategy`);
  - set-like lists returned in a different order (env vars);
  - equivalent spellings of the same value (`1Gi` vs `1024Mi`, `500m` vs `0.5`).

## Solution
`detect_drift(desired, live)` normalizes both sides before diffing:

1. **Ignore** platform-owned paths (`IGNORED_PATHS`).
2. **Fill server defaults** into the desired side only (`SERVER_DEFAULTS`).
   An omitted `replicas` equals live `1`, but a live `3` is still reported.
3. **Compare set-like lists by natural key** (`SET_KEYS`): env vars by
   `name`, ports by `containerPort` + `protocol` (so 53/TCP and 53/UDP stay
   two items), firewall rules by whole-item identity. Two items with the same
   key raise `ValueError` instead of one silently replacing the other.
   Paths stay readable, e.g. `spec.container.env[LOG_LEVEL].value` or
   `spec.container.ports[53/TCP]`.
4. **Canonicalize quantities** with `parse_quantity` (`Ki/Mi/Gi`, `k/M/G`,
   millicores).

Each result is a `Drift(path, kind, desired, live)` where `kind` is
`changed`, `added_live` (exists only live, usually a manual change) or
`missing_live`. On the untouched resource the result is empty. Each manual
change in the tests (memory hotfix, extra firewall rule, removed or edited env
var, scaled replicas) produces exactly one finding at the right path.

Run it on a schedule and on every deploy. Treat `added_live`/`changed` as
"put this into Git or revert it" before the deploy is allowed to proceed.

## Trade-offs
- The ignore/default/set-key tables are per resource type and must be
  maintained. A missing default means noise; an over-broad ignore hides real
  drift. Keep them small and reviewed, and test them against real API output.
- Detection is not prevention. Restrict console write access in production,
  or use a GitOps controller (Argo CD, Flux) that continuously reconciles and
  shows drift. Terraform users get similar detection from `terraform plan`
  with `-refresh-only`.
- Auto-reverting drift is tempting but can undo an emergency fix mid-incident.
  Alert and block deploys on drift rather than silently overwriting it.
- Secrets in live config must not be printed in drift reports. Redact values
  for sensitive paths.

## Run
```bash
python -m unittest -v test_solution.py
```

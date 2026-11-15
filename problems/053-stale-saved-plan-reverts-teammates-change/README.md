# 053 · An approved infrastructure change quietly undid a teammate's emergency scale-up

## Problem
A platform team changes production with a two-step pipeline: `plan` writes a
plan file that a reviewer approves, and a later `apply` job runs that exact
file. At 10:00 Alice planned a change to the `web` group that opened port 443;
the plan sat in the review queue. At 10:30 checkout latency spiked, and Bob
planned and applied an emergency change moving `web` from `m5.large` to
`m5.2xlarge`. At 11:00 Alice's plan was approved and applied. It finished
green, and `web` was back on `m5.large`: the saved plan held the full "after"
value for the resource, computed at 10:00. Latency climbed again during the
lunch peak, and it took an hour to work out that nobody had scaled it down on
purpose.

## Root cause
A saved plan is a diff against **one version of the state**. Its "after"
values include attributes the author never meant to touch, copied from the
state at plan time. Applying it later assumes nothing has changed since. The
pipeline applied any plan file it was given, so when the state had moved on,
it wrote the old values back without any warning. Locking the state during
`apply` does not help: Bob's and Alice's applies never overlapped.

## Solution
`solution.py` stamps every plan with the state it was computed from and
checks that stamp before writing:

- `make_plan` records the state's `lineage` (which state history this is),
  its `serial` (bumped on every write) and a SHA-256 hash of the resources.
- `safe_apply` calls `check_plan_is_current`, which raises `StalePlanError`
  if the lineage, serial or resource hash differ from the current state. The
  state is left untouched, and the author must plan again. The new plan then
  shows the conflict openly: `instance_type: m5.2xlarge -> m5.large` (tested).
- The serial catches normal applies, including applying the same plan twice.
  The hash also catches a state edited by hand or restored without a serial
  bump. The lineage stops a staging plan from being applied to production.

`naive_apply` keeps the original behaviour so the tests can show the revert.
Terraform works the same way: it refuses a saved plan whose state has changed
since it was created ("Saved plan is stale").

## Trade-offs
- Any write to the state makes every pending plan stale, even one touching
  unrelated resources. On a busy shared state that means a lot of re-planning;
  splitting large states into smaller ones per service reduces it. Checking
  only the hashes of the resources a plan touches is finer-grained, but it can
  miss changes to resources the plan read as inputs.
- The check covers changes recorded in the state. Changes made by hand in the
  cloud console are only seen if `plan` refreshes from the real
  infrastructure first; drift detection (problem 006) covers that gap.
- The check and the write must happen under the state lock, or two applies
  that both pass the check can still race. This model is single-threaded.

## Run
```bash
python -m unittest -v test_solution.py
```

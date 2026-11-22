# 065 · Removing one unused queue from the config made the plan destroy and recreate four live queues, and their unread messages were lost

## Problem
A platform team manages its message queues with infrastructure as code. The
queues are declared as one list and created with `count`, so each queue is
addressed by its position: `queue[0]` is `orders`, `queue[1]` is
`payments`, and so on for six queues.

The `payments` queue was retired, so an engineer deleted it from the list.
The reviewer saw a one-line diff. The plan said "4 to add, 5 to destroy",
which was skimmed as noise and applied. `billing`, `emails`, `refunds` and
`exports` were deleted and recreated empty: every message waiting in them
(invoices to send, refunds to issue) was gone, and producers got errors for
the few seconds each queue did not exist.

## Root cause
The planner matches the old and new resources **by address**. With `count`
the address is the list index, so removing item 1 shifts every later item
down one place: `queue[1]` changes from `payments` to `billing`,
`queue[2]` from `billing` to `emails`, and so on, and the last address
disappears. A queue's name cannot change in place, so each shifted address
becomes destroy + create. Unlike problem 035 (a resource renamed in code),
nothing here was renamed; only its position in a list moved.

In `solution.py`, `naive_plan` reproduces this: removing `payments` from
the six queues gives **4 creates and 5 destroys**, destroying `payments`,
`billing`, `emails`, `refunds` and `exports`.

## Solution
- **Address by a stable key.** `keyed_plan` addresses each queue by its name
  (`queue["billing"]`), like Terraform's `for_each` over a set or map. Now
  removing `payments` is exactly **1 destroy and 0 creates**, inserting a
  queue in the middle is one create, and reordering the list is no change.
  Duplicate names are rejected, because they cannot be keys.
- **Guard destroys of protected resources.** `check_plan` fails a plan that
  destroys any resource on a protected list unless that destroy was approved
  explicitly. The original index plan is blocked because it destroys
  `billing`; the keyed plan that only removes `payments` passes. Run it in
  CI on the machine-readable plan so a skimmed "5 to destroy" cannot be
  applied by accident.

## Trade-offs
- Switching existing resources from `count` to `for_each` changes their
  addresses, so the first plan after the switch would recreate everything.
  Move state first (`moved` blocks or `terraform state mv` per resource),
  and check that the plan is empty before applying.
- Keys must be stable for the resource's whole life. Keying by something
  that changes (a display name, an environment-specific suffix) brings the
  same problem back.
- Provider-level protection (`prevent_destroy`, deletion protection on the
  cloud resource) is a stronger last line of defence than a CI check, but
  it is per resource and easy to forget on new ones. Use both.
- `count` is still fine for identical, stateless copies (N workers) where
  which one is removed does not matter.

## Run
```bash
python -m unittest -v test_solution.py
```

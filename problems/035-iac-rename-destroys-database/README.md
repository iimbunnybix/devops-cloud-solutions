# 035 · A "rename only" infrastructure pull request deleted the production orders database

## Problem
A team manages its AWS estate with Terraform. CI runs `terraform plan` on
every merge to `main` and, if the plan finishes without errors, runs
`terraform apply` automatically. A tidy-up pull request renamed the
resource `aws_db_instance.main` to `aws_db_instance.primary` to match a
naming convention. Reviewers saw a one-line diff with no functional change.

The plan contained a **delete** of `aws_db_instance.main` and a **create**
of `aws_db_instance.primary`. The pipeline applied it: the orders database
was destroyed and an empty one created in its place. Checkout was down
while the database was restored from the last snapshot, and orders written
since that snapshot had to be rebuilt from payment-provider records.

## Root cause
Terraform identifies a resource by its **address** in code, not by its
real-world identity. When the address changes, the state still holds the
old address, so the plan is "destroy the old one, create the new one". The
same delete+create happens when an attribute the provider cannot change in
place (for example a database `engine`) is edited.

The pipeline gate only asked "did the plan error?"
(`naive_can_auto_apply`). A plan that deletes a stateful resource is a
valid plan, so it passed.

## Solution
`review_plan` inspects the plan's resource changes before apply and blocks
auto-apply when a **stateful** resource type (databases, buckets, volumes,
tables, caches) would be:

- **renamed**: a delete-only and a create-only change of the same type
  whose configuration matches, ignoring computed fields such as `id`,
  `arn` and `endpoint`. The finding suggests a `moved { from = ... to = ... }`
  block, which tells Terraform to update the state address with no
  infrastructure change. Each create can match only one delete;
- **replaced**: the change contains both delete and create (in either
  order, so `create_before_destroy` is covered). The finding lists the
  changed attributes so the engineer sees which edit forced replacement;
- **destroyed**: a plain delete.

Stateless resources (instances, load-balancer rules) can still be replaced
freely, and in-place updates of databases pass. The tests reproduce the
rename incident and cover each case above.

## Trade-offs
- The rename detector is a heuristic. If the rename also changes an
  attribute, it is reported as a plain destroy. It is still blocked, just
  without the `moved` hint.
- The list of stateful types must be maintained. Pair this with
  provider-side protection (`lifecycle { prevent_destroy = true }`, RDS
  `deletion_protection`, S3 versioning) so a missed type is not a disaster.
- A blocked plan needs a human with approval rights to apply deliberate
  deletions; that is the point, but it adds a step to decommissioning.
- Policy engines (OPA/Conftest, Sentinel) can enforce the same rules on
  `terraform show -json` output; this module shows the logic they need.

## Run
```bash
python -m unittest -v test_solution.py
```

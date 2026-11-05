# 025 · Rolling back a bad release made the outage worse: the old version crashed on every request

## Problem
An e-commerce order service deploys several times a day. The pipeline runs
the release's database migration first, then rolls out the new pods.
Release 42 renamed `orders.coupon_code` to `orders.promo_code` (the
migration and the code change shipped together) and also contained an
unrelated bug in tax calculation. On-call saw wrong totals within minutes and
rolled the app back to release 41, the standard two-minute fix. Instead every
request started failing with `column "coupon_code" does not exist`. The
team had to write and run a reverse migration by hand under pressure; the
incident lasted about 50 minutes instead of 2. During the rolling deploy
itself, the release-41 pods that were still running had also been failing.

## Root cause
App rollbacks are fast because they only change code, but **the schema is not
rolled back with them**. The deploy gate (`naive_deploy_gate`) only checked
that the *new* release works with the *migrated* schema. It never checked the
*previous* release against it, even though the previous release keeps
serving traffic during the rollout and is exactly what you roll back to.
A rename (or drop) of a column the old release uses, or a new `NOT NULL`
column without a default that the old release does not insert, makes the
migration a one-way door.

## Solution
`rollback_safe_deploy_gate(old_app, new_app, schema, operations)`:

1. Applies the migration to a copy of the schema.
2. Checks the new release against it (as before).
3. Also checks the **old** release: every column it reads must still exist,
   and every column that is `NOT NULL` without a default must be one it
   inserts. Any failure is reported as `rollback blocked: ...` and the
   deploy stops before the migration runs.

This enforces the **expand/contract** pattern: ship the change in steps that
are each compatible with the release before them: add `promo_code`
(nullable), deploy code that writes both columns and reads the new one,
backfill, and only drop `coupon_code` once no release you might roll back to
reads it. The tests show the rename and the drop blocked, and the expand and
contract steps allowed.

## Trade-offs
- The gate is only as good as the declared column usage. Deriving it
  automatically (from ORM models, or by parsing the queries the old release
  runs, e.g. `pg_stat_statements`) is more reliable than hand-written lists.
- Expand/contract turns one deploy into three or four and needs a backfill
  and a cleanup ticket that someone must actually finish.
- It does not cover data changes (e.g. a migration that rewrites values into
  a format the old release cannot parse) or long-running locks from
  migrations; those need their own checks.
- Migration linters such as Rails' `strong_migrations`,
  `django-migration-linter` or Squawk (PostgreSQL SQL) implement similar
  rules if you can adopt them.

## Run
```bash
python -m unittest -v test_solution.py
```

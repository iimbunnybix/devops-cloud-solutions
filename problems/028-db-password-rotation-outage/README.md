# 028 · Checkout failed with "password authentication failed" for 20 minutes during a routine database password rotation

## Problem
The security team rotates the checkout service's PostgreSQL password every
90 days. The runbook said: run `ALTER ROLE checkout PASSWORD '...'`, update
the secret in the secret manager, then do a rolling restart of the 40
checkout pods so they pick it up.

The moment the password changed, every pod still running had the old
password cached. Existing pooled connections kept working (PostgreSQL does
not drop sessions on a password change), but every *new* connection failed
with `password authentication failed for user "checkout"`. Pools churn
connections constantly, so errors climbed as old connections aged out.
The rolling restart took about 20 minutes, and checkout errors stayed high
until the last pod restarted. A batch job that read the secret only at
startup kept failing until someone restarted it by hand that evening.

## Root cause
**The old credential stopped working before every consumer had the new
one.** With a single database user, "change the password" and "consumers
switch" cannot happen at the same moment, so there is always a window where
some consumers hold a credential the database no longer accepts. Nothing
checked who was still using the old password before it was invalidated.

In the test (4 pods, 5 new connections per pod per rollout step),
`naive_rotate` + `rolling_restart` produces 30 failed logins: the 3, 2, 1
and 0 pods that have not restarted yet after each step, times 5.

## Solution
**Dual-credential rotation** with two database users (`checkout_a`,
`checkout_b`) that have the same privileges, used in turn, driven by a small
state machine (`DualCredentialRotation`):

1. **`start()`** (`STABLE` -> `ROLLING_OUT`): set a new random password on
   the *inactive* slot user, first in the database, then publish a secret
   with `current` = new and `previous` = old. The old user still works.
2. **Roll out.** Pods load the new secret on restart. `Pod.open_connection`
   tries `current` and falls back to `previous`, which also covers a new
   user that has not reached every database replica yet. The same rollout
   now has 0 failed logins.
3. **`finish()`** (`ROLLING_OUT` -> `STABLE`): a **safety check** refuses to
   revoke the old user while any known pod still runs an older secret
   version, or while the database shows open connections for the old user
   (this catches consumers nobody listed, like the forgotten batch job). If
   blocked, it raises `RotationBlocked` with every reason and changes
   nothing. Otherwise it revokes the old user and publishes the secret
   without `previous`.

The next rotation goes back to the first slot, so each rotation always has
one known-good credential to fall back to.

## Trade-offs
- Two users must have identical grants and own nothing important (use a
  shared group role that owns the objects); otherwise one slot can do
  things the other cannot.
- The "no open connections" check needs a quiet period: a consumer that
  connects only once a day is invisible most of the time. In production,
  check `pg_stat_activity` *and* login logs for the old user over at least
  one full job cycle before revoking.
- Revoking login (`NOLOGIN`) does not end existing sessions. That is what
  keeps the rollout safe, but if the rotation is because of a leak, also
  terminate the old user's sessions after the check passes.
- Managed options do this for you: AWS Secrets Manager's "alternating
  users" rotation, Vault database secrets engine (short-lived dynamic users),
  or IAM database authentication, which removes static passwords entirely.

## Run
```bash
python -m unittest -v test_solution.py
```

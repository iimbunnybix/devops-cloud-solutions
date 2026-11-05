# 026 · On-call lowered the database connection limit during an incident, but the next deploy overloaded the database again

## Problem
The checkout service (60 pods) shares one PostgreSQL primary. During a
database overload, the on-call engineer cut each pod's pool from the default
200 connections to 20 by adding `CHECKOUT_MAX_DB_CONNECTION=20` to the
deployment's environment. The incident calmed down by itself as traffic
dropped, so the change looked like it had worked. At the next morning's
peak, after a routine deploy, the database hit its connection limit again
and checkout failed for 25 minutes. The pods had been running with 200
connections each the whole time.

## Root cause
The variable was misspelled: the service reads `CHECKOUT_MAX_DB_CONNECTIONS`
(with an S). `naive_load` uses `environ.get(name, default)`, so:

- a variable it does not know about is silently ignored, and
- a setting it does know about but cannot find silently becomes the default.

Nothing in the logs, the deploy, or the health checks showed that the
override had no effect. The same pattern hides wrong types (`"3s"` for a
millisecond value) until the code path that parses them runs.

## Solution
`load_config` validates the whole environment against a declared schema at
startup:

- **Unknown settings fail.** Any variable starting with the service's
  prefix (`CHECKOUT_`) that is not a declared setting is an error, with a
  `difflib` "did you mean" hint: `unknown setting CHECKOUT_MAX_DB_CONNECTION
  (did you mean CHECKOUT_MAX_DB_CONNECTIONS?)`. Variables without the prefix
  (`PATH`, `HOME`, other teams' settings) are ignored.
- **Values are parsed and range-checked** (`CHECKOUT_MAX_DB_CONNECTIONS=0
  is outside 1..500`), and required settings without a default must be set.
- **All errors are reported at once** in one `ConfigError`, so the process
  exits before it serves traffic and the rollout stops at the first pod.

## Trade-offs
- Strict validation means an old variable left in a deployment after a
  setting is removed now breaks startup. Keep a short "deprecated names"
  list that only warns, and delete entries after a release or two.
- Failing startup turns a config mistake into a failed deploy rather than a
  silent misconfiguration; that is the point, but it needs good rollout
  health checks so the bad pod does not take traffic.
- The prefix rule only catches typos in the variable *name* that keep the
  prefix. A typo in the prefix itself (`CHEKOUT_...`) is still ignored;
  emitting the effective config at startup (secrets redacted) helps there.
- Libraries such as `pydantic-settings` give typed, validated settings with
  less code if third-party dependencies are allowed.

## Run
```bash
python -m unittest -v test_solution.py
```

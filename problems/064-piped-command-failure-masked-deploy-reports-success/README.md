# 064 · The database migration failed, CI showed the deploy step green, and the new release crashed on a missing column

## Problem
A team's CI pipeline deploys a web API in two steps: run the schema
migrations, then roll out the new release. To keep a copy of the migration
output as a build artifact, someone changed the migration step to:

```bash
set -e
./run_migrations.sh | tee migrate.log
./deploy.sh --release "$RELEASE"
```

One release added a `NOT NULL` column. The migration hit a lock timeout and
exited with status 1, but the step stayed green and the job went on to
deploy. The new release queried a column that did not exist, and every
request touching orders failed with 500s until the release was rolled back
and the migration was re-run by hand. The CI log did contain the migration
error, a few hundred lines above a green check mark nobody questioned.

## Root cause
In bash, "the return status of a pipeline is the exit status of the last
command, unless the pipefail option is enabled" (`man bash`, "Pipelines").
The last command was `tee`, which wrote the log and exited 0, so the
pipeline's status was 0. `set -e` did not help: it reacts to the pipeline's
status, which was already 0. The same pattern hides failures in
`build | gzip > artifact.gz`, `curl ... | sh` and `tests | grep -v noise`.

## Solution
`solution.py` has two parts:

- **A model of bash's pipeline status.** `naive_pipeline_status` returns the
  last command's status (bash's default). `pipeline_status(codes,
  pipefail=True)` returns the rightmost non-zero status, or 0 if every command
  succeeded, which is what `set -o pipefail` does. For the incident,
  `[1, 0]` gives 0 by default and 1 with pipefail, so the step fails and the
  deploy never starts. The model was checked against real bash 5.2 on 60
  random pipelines in both modes, with no mismatches (the tests themselves do
  not need bash).
- **A linter for CI step scripts.** `lint_script` walks each line, splits it
  into pipelines on unquoted `;`, `&&` and `||`, splits each pipeline on
  unquoted `|` and `|&`, and reports every line with a pipeline that runs
  while pipefail is off. It recognises `set -o pipefail` and option clusters
  such as `set -euo pipefail`, and `set +o pipefail` turns it off again.
  Pipes inside quotes, after a backslash or in comments are ignored, and
  `||` is not a pipe. `lint_steps` lints each step separately, because each
  CI step usually runs in a fresh shell and does not inherit options from the
  previous one.

The fix for the deploy script itself is one line: start it with
`set -euo pipefail`.

## Trade-offs
- `pipefail` also fails on harmless non-zero statuses, for example
  `grep` finding no matches (status 1) or a producer killed by `SIGPIPE`
  when `head` stops reading early (status 141). Handle those explicitly,
  such as `{ grep pattern file || true; } | sort`, rather than turning
  pipefail off.
- The linter reads one line at a time. It does not follow pipelines split
  across lines with a trailing `\`, heredocs, functions defined elsewhere or
  scripts that call other scripts. It is a guard against the common mistake,
  not a shell parser; `shellcheck` and running steps with `bash -eo pipefail` cover more.
  GitHub Actions does this when a step sets `shell: bash` explicitly, but
  its default shell on Linux runners is `bash -e`, without pipefail.
- Where per-command handling is needed, bash's `PIPESTATUS` array gives
  every command's status, at the cost of more code in each step.

## Run
```bash
python -m unittest -v test_solution.py
```

# 050 · After shipping 1.10.0, "deploy latest" kept rolling production back to 1.9.1

## Problem
A team's deploy pipeline has a "deploy latest release" job that runs after
each tag is pushed, and a one-click "roll back to previous release" job. The
day they tagged `v1.10.0`, production went **back** to `v1.9.1`: a payment
fix that shipped in 1.10.0 disappeared, and the bug it fixed returned. Every
re-run of the job did the same. The rollback job used the same ordering, so
it considered 1.10.0 the *oldest* release and could not roll back from it.

## Root cause
The job chose the newest tag with `max(tags)`, which compares **strings**
character by character. `"v1.10.0"` and `"v1.9.0"` first differ at the
fourth character: `"1"` versus `"9"`, so `"v1.10.0" < "v1.9.0"`. Releases
1.0-1.9 happened to sort correctly, which hid the bug until the first
two-digit minor version. Over the tags `v1.8.2 … v1.10.1`, `max` returns
`v1.9.1`.

## Solution
- `parse_tag` turns `vMAJOR.MINOR.PATCH[-prerelease]` into integers plus
  pre-release identifiers, using `fullmatch` so tags like `latest`,
  `v1.99`, `v2.0.0.1` or a tag with a trailing newline are **ignored, not
  guessed at**.
- `sort_key` follows SemVer 2.0 precedence: numbers compare numerically, a
  release sorts after its pre-releases (`2.0.0-rc.10 < 2.0.0`), numeric
  pre-release parts compare as numbers (`rc.2 < rc.10`) and sort before text
  parts.
- `latest_release` excludes pre-releases, so `v1.11.0-rc.1` is never deployed
  as "latest", and raises if there are no valid tags instead of deploying
  whatever sorts last.
- `previous_release` uses the same ordering for rollbacks and refuses to roll
  back past the oldest release.

## Trade-offs
- Choosing "latest" by version number still trusts that tags were created in
  the right order. Recording the exact artifact digest of each successful
  deploy, and rolling back to *that*, is more robust than recomputing it.
- Pinning the version explicitly (a deploy request that names `v1.10.1`)
  removes the "latest" lookup altogether; many teams use the tag only to
  trigger the build.
- Hotfix tags on an older line (`v1.9.2` after `v1.10.0`) are older by
  SemVer, so `latest_release` correctly ignores them, but a rollback from
  1.10.0 goes to 1.9.2, not to 1.9.1.
- `packaging.version` (PEP 440) or a SemVer library would do this in
  production; this code shows the rules with the standard library only.

## Run
```bash
python -m unittest -v test_solution.py
```

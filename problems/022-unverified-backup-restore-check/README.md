# 022 · Backups had shown "succeeded" every night for weeks, but the first restore found empty files

## Problem
A Postgres database behind the order service is backed up nightly by a cron
job, and the job dashboard showed green every night. After an engineer ran a
bad `DELETE` without a `WHERE` clause, the team went to restore the previous
night's backup and found every table dump empty. So were the dumps from the
nights before, back to the day a database password had been rotated, six
weeks earlier. Orders from those weeks had to be rebuilt by hand from payment
provider exports and emails.

## Root cause
The job ran `pg_dump ... | gzip | aws s3 cp - s3://...` in a shell without
`set -o pipefail`. A pipeline's exit status is the status of its **last**
command, so when `pg_dump` failed to authenticate, `gzip` happily compressed
empty input, the upload succeeded and the job exited 0. The scheduler only
checked the exit code (`naive_backup_ok`), and nobody ever tested a restore.
A backup is only known to work once it has been restored.

## Solution
`verify_backup` is a separate job that runs after each backup. It restores the
latest backup into a scratch database (simulated here by counting rows in each
dump) and collects **every** failure into one report:

- **Freshness**: the newest backup must be at most 26 h old (the 24 h schedule
  plus runtime), so a backup job that silently stopped running also alerts.
- **Manifest checksums**: the dump step writes a SHA-256 per file; any file
  that is missing, extra or has a different checksum means it was truncated
  or corrupted on the way to storage.
- **Every source table is present**: catches a new table that the dump job's
  table list never included.
- **Row counts versus the live source within 5%**: the source keeps changing
  after the dump, so an exact match is not expected. This is the check that
  catches the incident, because an empty dump has a perfectly valid checksum
  (tested: the empty backup passes the exit-code check but fails with
  "restored 0 rows, source has 1000").

`alert_message` turns a failed report into one page with all failures listed.
The tests cover a good backup, an empty dump, a 10% short dump (fails), 4%
drift (passes), a truncated upload, a missing manifest, a missing file, a new
table, a 30 h old backup, no backup at all, and an empty source table.

Also fix the job itself: `set -euo pipefail` in the script, so a failed
`pg_dump` fails the pipeline.

## Trade-offs
- A real restore of a large database takes time and money (a scratch
  instance, hours of `pg_restore`). Many teams verify a full restore weekly
  and only checksums plus a sampled table restore nightly.
- Row counts within a tolerance cannot detect wrong *values*. Add a few
  invariant queries on the restored copy (sum of yesterday's order totals,
  max order id) for stronger checks.
- The 5% tolerance must fit the table: an append-only log grows faster than a
  customers table. Comparing against row counts recorded at dump time (inside
  the same snapshot) removes the need for a tolerance.
- Keep the verifier separate from the backup job and alert when the verifier
  itself does not report (a dead man's switch, see problem 007).

## Run
```bash
python -m unittest -v test_solution.py
```

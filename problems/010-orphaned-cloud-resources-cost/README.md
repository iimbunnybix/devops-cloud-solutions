# 010 · The storage bill climbed every month after CI moved to per-PR environments, and the first cleanup script deleted a production volume

## Problem
Platform engineering moved CI to a fresh environment per pull request. Finance
flagged that block storage and IP charges kept climbing every month even though
the number of running instances was flat. An engineer wrote a quick script,
"delete every unattached volume and IP", and ran it on a Friday. It removed
hundreds of leaked CI volumes, and also the payments database's data volume,
which the DBA had detached five minutes earlier for a planned filesystem
check. Restoring it from the last snapshot cost hours of downtime and a
replay of transactions.

## Root cause
- **The leak:** the CI teardown deleted instances only. Data volumes were
  created with *delete on termination* off, snapshots were taken for
  debugging, and each environment reserved a public IP. None of them were
  removed, and nothing reported spend on resources not attached to anything.
- **The dangerous fix:** "unattached" was used as if it meant "unused". A
  volume detached minutes ago, a snapshot of a still-existing volume, or a
  resource someone deliberately parked are all unattached but not garbage.
  The script had no age threshold, no exemptions, no dry run and no grace period.

In the simulation (`ci_leak_fleet`: 1,800 CI environments over 180 days,
plus 40 attached production volumes), the policy finds **1,858 orphans
(1,679 volumes and 179 IPs) costing 16,504.40 per month** at the illustrative
rates in `solution.py`, all owned by the `ci` team. The naive script, given a
volume detached 5 minutes earlier, deletes it.

## Solution
`find_orphans` and `CleanupJob` in `solution.py`:

1. **Age-based rules per kind:** volumes unattached ≥ 14 days, public IPs
   unassociated ≥ 7 days, snapshots whose source volume no longer exists and
   that are ≥ 90 days old.
2. **Exemptions:** `keep=true`, or an `expires` tag in the future. A malformed
   `expires` value is *not* an exemption, so typos cannot hide waste forever.
   Dates with a timezone offset (`2027-01-01T00:00:00+02:00`, `...Z`) are
   converted to UTC before comparing, so one such tag cannot crash the run.
3. **Report before acting:** findings sorted by monthly cost and grouped by
   `team` tag, so each owner sees their own bill.
4. **Two-phase deletion:** `mark` tags each candidate with the time it was marked;
   `sweep` deletes only resources marked ≥ 7 days ago **and still orphaned
   now**. Re-attaching a volume during the grace period spares it and removes
   the mark. `dry_run` is the default.
5. **Cascades are handled over cycles:** once leaked volumes are deleted,
   their old snapshots become orphans on the next run (344 of the 600 CI
   snapshots in the simulation, the ones older than 90 days). The 121 volumes
   that were under 14 days old when marked are left for later runs.

## Trade-offs
- Thresholds trade savings against safety; 14 days of unattached volume cost
  is cheap insurance compared with deleting real data. For high-value
  volumes, take a final snapshot before deleting.
- Tag-based ownership only works if tagging is enforced at creation
  (policy-as-code or an admission check); untagged findings need a default owner.
- The real fix for the leak is upstream: set *delete on termination*, or tear
  down the whole environment as one unit (a CloudFormation/Terraform stack or
  a dedicated account/project per environment). This job is the safety net.
- Rates here are illustrative; use the provider's billing export (e.g. cost
  and usage reports) for real numbers.

## Run
```bash
python -m unittest -v test_solution.py
```

import random
import unittest
from datetime import datetime, timedelta

from solution import (MARK_TAG, CleanupJob, Inventory, Policy, Resource, cost_by_owner,
                      find_orphans, naive_cleanup)

NOW = datetime(2026, 10, 1, 9, 0)
SNAPSHOTS_NEXT_CYCLE = 344      # of 600 CI snapshots: those older than 90 days


def ci_leak_fleet(seed=42):
    """180 days of per-PR CI environments whose volumes, snapshots and IPs were left behind,
    plus a small healthy production estate."""
    rng = random.Random(seed)
    res = []
    for i in range(1_800):
        created = NOW - timedelta(days=rng.randint(1, 180), hours=rng.randint(0, 23))
        torn_down = created + timedelta(hours=rng.randint(1, 48))
        vol = f"vol-ci-{i:04d}"
        res.append(Resource(vol, "volume", created, size_gb=rng.choice([50, 100, 200]),
                            detached_at=torn_down, tags={"team": "ci"}))
        if i % 3 == 0:
            res.append(Resource(f"snap-ci-{i:04d}", "snapshot", torn_down, size_gb=50,
                                source_volume=vol, tags={"team": "ci"}))
        if i % 10 == 0:
            res.append(Resource(f"ip-ci-{i:04d}", "ip", created, detached_at=torn_down,
                                tags={"team": "ci"}))
    for i in range(40):
        res.append(Resource(f"vol-prod-{i}", "volume", NOW - timedelta(days=400), size_gb=500,
                            attached_to=f"i-prod-{i}", tags={"team": "payments"}))
    return res


class ReproduceLeak(unittest.TestCase):
    def test_leaked_resources_keep_billing(self):
        inv = Inventory(ci_leak_fleet())
        findings = find_orphans(inv, NOW)
        monthly = round(sum(f.monthly_cost for f in findings), 2)
        self.assertEqual(len(findings), 1_858)                      # 1,679 volumes + 179 IPs
        self.assertEqual(monthly, 16_504.4)
        self.assertEqual(list(cost_by_owner(findings)), ["ci"])     # all from CI, none from prod

    def test_naive_cleanup_deletes_volume_detached_for_maintenance(self):
        maint = Resource("vol-prod-db", "volume", NOW - timedelta(days=700), size_gb=2_000,
                         detached_at=NOW - timedelta(minutes=5), tags={"team": "payments"})
        inv = Inventory([maint])
        self.assertIn("vol-prod-db", naive_cleanup(inv))            # data loss


class SafeCleanup(unittest.TestCase):
    def test_fresh_detach_and_young_snapshots_are_left_alone(self):
        inv = Inventory([
            Resource("vol-maint", "volume", NOW - timedelta(days=700), size_gb=2_000,
                     detached_at=NOW - timedelta(minutes=5)),
            Resource("snap-young", "snapshot", NOW - timedelta(days=30), size_gb=10,
                     source_volume="vol-gone"),
            Resource("snap-live-src", "snapshot", NOW - timedelta(days=365), size_gb=10,
                     source_volume="vol-maint"),
            Resource("ip-new", "ip", NOW - timedelta(days=2)),
        ])
        self.assertEqual(find_orphans(inv, NOW), [])

    def test_exemptions(self):
        old = NOW - timedelta(days=100)
        inv = Inventory([
            Resource("vol-keep", "volume", old, size_gb=10, tags={"keep": "true"}),
            Resource("vol-exp", "volume", old, size_gb=10, tags={"expires": "2026-12-31"}),
            Resource("vol-expired", "volume", old, size_gb=10, tags={"expires": "2026-09-01"}),
            Resource("vol-bad-tag", "volume", old, size_gb=10, tags={"expires": "soon"}),
        ])
        ids = sorted(f.resource.id for f in find_orphans(inv, NOW))
        self.assertEqual(ids, ["vol-bad-tag", "vol-expired"])

    def test_expires_tag_with_timezone_offset_is_compared_not_crashing(self):
        old = NOW - timedelta(days=100)
        inv = Inventory([
            Resource("vol-future-offset", "volume", old, size_gb=10,
                     tags={"expires": "2027-01-01T00:00:00+00:00"}),
            Resource("vol-future-z", "volume", old, size_gb=10,
                     tags={"expires": "2027-01-01T00:00:00Z"}),
            Resource("vol-past-offset", "volume", old, size_gb=10,
                     tags={"expires": "2026-09-01T00:00:00+02:00"}),
            Resource("vol-prod", "volume", NOW - timedelta(days=400), size_gb=500,
                     attached_to="i-1", tags={"expires": "2027-01-01T00:00:00+00:00"}),
        ])
        ids = sorted(f.resource.id for f in find_orphans(inv, NOW))
        self.assertEqual(ids, ["vol-past-offset"])

    def test_offset_is_converted_to_utc_before_comparing(self):
        # 10:30+02:00 is 08:30 UTC, i.e. already past NOW (09:00 UTC).
        old = NOW - timedelta(days=100)
        inv = Inventory([Resource("vol-a", "volume", old, size_gb=10,
                                  tags={"expires": "2026-10-01T10:30:00+02:00"})])
        self.assertEqual([f.resource.id for f in find_orphans(inv, NOW)], ["vol-a"])

    def test_two_phase_mark_then_sweep(self):
        inv = Inventory(ci_leak_fleet())
        job = CleanupJob(inv, Policy(grace_days=7), dry_run=False)
        marked = job.mark(NOW)
        self.assertEqual(job.sweep(NOW + timedelta(days=6)), [])     # still in grace period
        deleted = job.sweep(NOW + timedelta(days=7))
        self.assertEqual(len(deleted), len(marked))
        leftover = [r for r in inv.all() if r.kind == "volume" and r.attached_to is None]
        self.assertEqual(len(leftover), 121)                         # too young when marked
        self.assertTrue(all((NOW - r.detached_at).days < 14 for r in leftover))
        self.assertTrue(all(rid.startswith(("vol-ci", "snap-ci", "ip-ci")) for rid in deleted))

    def test_sweep_just_before_grace_period_ends_deletes_nothing(self):
        late_evening = datetime(2026, 10, 1, 23, 59)
        vol = Resource("vol-x", "volume", late_evening - timedelta(days=60), size_gb=100,
                       detached_at=late_evening - timedelta(days=30))
        job = CleanupJob(Inventory([vol]), Policy(grace_days=1), dry_run=False)
        job.mark(late_evening)
        self.assertEqual(job.sweep(late_evening + timedelta(minutes=1)), [])
        self.assertEqual(job.sweep(late_evening + timedelta(days=1, minutes=-1)), [])
        self.assertEqual(job.sweep(late_evening + timedelta(days=1)), ["vol-x"])

    def test_snapshots_follow_in_next_cycle(self):
        inv = Inventory(ci_leak_fleet())
        job = CleanupJob(inv, dry_run=False)
        job.mark(NOW)
        job.sweep(NOW + timedelta(days=7))
        later = NOW + timedelta(days=7)
        snaps = [f for f in find_orphans(inv, later) if f.resource.kind == "snapshot"]
        self.assertEqual(len(snaps), SNAPSHOTS_NEXT_CYCLE)

    def test_reattached_during_grace_is_spared(self):
        vol = Resource("vol-x", "volume", NOW - timedelta(days=60), size_gb=100,
                       detached_at=NOW - timedelta(days=30))
        inv = Inventory([vol])
        job = CleanupJob(inv, dry_run=False)
        job.mark(NOW)
        self.assertIn(MARK_TAG, vol.tags)
        vol.attached_to = "i-restored"                               # owner reclaimed it
        self.assertEqual(job.sweep(NOW + timedelta(days=10)), [])
        job.mark(NOW + timedelta(days=10))
        self.assertNotIn(MARK_TAG, vol.tags)                         # mark removed

    def test_dry_run_deletes_nothing(self):
        inv = Inventory(ci_leak_fleet())
        job = CleanupJob(inv)                                         # dry_run default
        job.mark(NOW)
        due = job.sweep(NOW + timedelta(days=8))
        self.assertGreater(len(due), 0)
        self.assertEqual(inv.deleted, [])

    def test_snapshot_becomes_orphan_after_volume_deleted(self):
        vol = Resource("vol-a", "volume", NOW - timedelta(days=200), size_gb=10,
                       attached_to="i-1")
        snap = Resource("snap-a", "snapshot", NOW - timedelta(days=150), size_gb=10,
                        source_volume="vol-a")
        inv = Inventory([vol, snap])
        self.assertEqual(find_orphans(inv, NOW), [])
        inv.delete("vol-a")
        self.assertEqual([f.resource.id for f in find_orphans(inv, NOW)], ["snap-a"])

    def test_sorted_by_cost(self):
        old = NOW - timedelta(days=100)
        inv = Inventory([Resource("v-small", "volume", old, size_gb=10),
                         Resource("v-big", "volume", old, size_gb=1_000),
                         Resource("ip", "ip", old)])
        self.assertEqual([f.resource.id for f in find_orphans(inv, NOW)], ["v-big", "ip", "v-small"])


if __name__ == "__main__":
    unittest.main()

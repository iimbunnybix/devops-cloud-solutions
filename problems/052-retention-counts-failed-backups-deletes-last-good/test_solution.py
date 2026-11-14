import unittest
from datetime import datetime, timedelta

from solution import (KEEP_USABLE_BACKUPS, STATUS_FAILED, STATUS_RUNNING, STATUS_SUCCEEDED,
                      Backup, backups_are_stale, backups_to_delete, naive_backups_to_delete,
                      newest_usable_backup)

FIRST_NIGHT = datetime(2026, 11, 1, 2, 0, 0)
ONE_DAY = timedelta(days=1)
GOOD_SIZE = 40_000_000_000


def nightly(night: int, status: str = STATUS_SUCCEEDED, size_bytes: int = GOOD_SIZE) -> Backup:
    """The backup started at 02:00 on night number `night` (0 = 1 November)."""
    return Backup(f"db-2026-11-{night + 1:02d}", FIRST_NIGHT + night * ONE_DAY, status, size_bytes)


def week_of_failures_after_good_backups() -> list[Backup]:
    """Nights 0-2 succeeded, then the credential expired and nights 3-9 failed."""
    good = [nightly(night) for night in range(3)]
    failed = [nightly(night, STATUS_FAILED, size_bytes=0) for night in range(3, 10)]
    return good + failed


class ReproduceProblem(unittest.TestCase):
    def test_week_of_failures_deletes_every_good_backup(self):
        to_delete = naive_backups_to_delete(week_of_failures_after_good_backups())
        self.assertEqual(sorted(to_delete), ["db-2026-11-01", "db-2026-11-02", "db-2026-11-03"])

    def test_nothing_restorable_survives_the_naive_rule(self):
        backups = week_of_failures_after_good_backups()
        to_delete = set(naive_backups_to_delete(backups))
        survivors = [backup for backup in backups if backup.name not in to_delete]
        self.assertIsNone(newest_usable_backup(survivors))


class FixRetention(unittest.TestCase):
    def test_good_backups_survive_a_week_of_failures(self):
        to_delete = backups_to_delete(week_of_failures_after_good_backups())
        for name in ["db-2026-11-01", "db-2026-11-02", "db-2026-11-03"]:
            self.assertNotIn(name, to_delete)

    def test_failed_backups_are_deleted(self):
        to_delete = backups_to_delete(week_of_failures_after_good_backups())
        self.assertEqual(len(to_delete), 7)
        self.assertIn("db-2026-11-10", to_delete)

    def test_oldest_usable_backups_beyond_quota_are_deleted(self):
        backups = [nightly(night) for night in range(10)]
        to_delete = backups_to_delete(backups)
        self.assertEqual(sorted(to_delete), ["db-2026-11-01", "db-2026-11-02", "db-2026-11-03"])

    def test_empty_succeeded_backup_does_not_use_up_the_quota(self):
        backups = [nightly(night) for night in range(KEEP_USABLE_BACKUPS)]
        backups.append(nightly(KEEP_USABLE_BACKUPS, STATUS_SUCCEEDED, size_bytes=0))
        to_delete = backups_to_delete(backups)
        self.assertEqual(to_delete, ["db-2026-11-08"])

    def test_running_backup_is_never_deleted(self):
        backups = [nightly(night) for night in range(10)]
        backups.append(nightly(10, STATUS_RUNNING, size_bytes=0))
        self.assertNotIn("db-2026-11-11", backups_to_delete(backups))

    def test_input_order_does_not_matter(self):
        backups = week_of_failures_after_good_backups()
        reversed_backups = list(reversed(backups))
        self.assertEqual(sorted(backups_to_delete(backups)),
                         sorted(backups_to_delete(reversed_backups)))

    def test_keeping_zero_backups_is_refused(self):
        with self.assertRaises(ValueError):
            backups_to_delete(week_of_failures_after_good_backups(), keep=0)


class FixStalenessAlert(unittest.TestCase):
    def test_week_of_failures_is_stale(self):
        now = FIRST_NIGHT + 10 * ONE_DAY
        self.assertTrue(backups_are_stale(week_of_failures_after_good_backups(), now))

    def test_last_nights_backup_is_fresh(self):
        backups = [nightly(night) for night in range(3)]
        now = FIRST_NIGHT + 2 * ONE_DAY + timedelta(hours=6)
        self.assertFalse(backups_are_stale(backups, now))

    def test_no_backups_at_all_is_stale(self):
        self.assertTrue(backups_are_stale([], FIRST_NIGHT))


if __name__ == "__main__":
    unittest.main()

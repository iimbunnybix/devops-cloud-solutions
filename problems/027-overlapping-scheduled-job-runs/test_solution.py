import unittest

from solution import (CRASHED, INTERVAL_SECONDS, INVOICES_PER_INTERVAL, LEASE_TTL_SECONDS,
                      FakeClock, LeaseLock, guarded_export, naive_export)

JOB = "invoice-export"
NORMAL = 120                      # a healthy run: 2 minutes
SLOW = 1000                       # database slowdown: over 3 intervals
SLOW_PERIOD = [NORMAL, SLOW, SLOW, SLOW, NORMAL, NORMAL, NORMAL, NORMAL]
TOTAL_INVOICES = len(SLOW_PERIOD) * INVOICES_PER_INTERVAL


def new_lock(now=0.0):
    clock = FakeClock(now)
    return clock, LeaseLock(clock)


class ReproduceProblem(unittest.TestCase):
    def test_slow_runs_overlap_and_export_invoices_twice(self):
        report = naive_export(SLOW_PERIOD)
        self.assertEqual(report.max_concurrent_runs, 4)
        self.assertEqual(report.duplicate_exports, 60)

    def test_healthy_schedule_has_no_overlap_even_without_lock(self):
        report = naive_export([NORMAL] * 8)
        self.assertEqual(report.max_concurrent_runs, 1)
        self.assertEqual(report.duplicate_exports, 0)


class FixSkipsOverlappingStarts(unittest.TestCase):
    def test_only_one_run_at_a_time(self):
        self.assertEqual(guarded_export(SLOW_PERIOD).max_concurrent_runs, 1)

    def test_no_invoice_is_exported_twice(self):
        self.assertEqual(guarded_export(SLOW_PERIOD).duplicate_exports, 0)

    def test_every_invoice_is_still_exported_exactly_once(self):
        counts = guarded_export(SLOW_PERIOD).export_counts
        self.assertEqual(len(counts), TOTAL_INVOICES)
        self.assertEqual(set(counts.values()), {1})

    def test_skipped_starts_are_counted(self):
        self.assertEqual(guarded_export(SLOW_PERIOD).skipped_starts, 3)

    def test_crashed_run_blocks_only_until_its_lease_expires(self):
        schedule = [NORMAL, CRASHED] + [NORMAL] * 10
        report = guarded_export(schedule)
        # Crash at t=300 holds the lease until 2100: ticks 600..1800 are skipped.
        self.assertEqual(report.skipped_starts, (LEASE_TTL_SECONDS // INTERVAL_SECONDS) - 1)
        self.assertEqual(len(report.export_counts), len(schedule) * INVOICES_PER_INTERVAL)
        self.assertEqual(report.duplicate_exports, 0)


class FixLeaseLock(unittest.TestCase):
    def test_second_acquire_while_held_fails(self):
        _, lock = new_lock()
        self.assertIsNotNone(lock.acquire(JOB, ttl=60))
        self.assertIsNone(lock.acquire(JOB, ttl=60))

    def test_expired_lease_can_be_taken_over(self):
        clock, lock = new_lock()
        lock.acquire(JOB, ttl=60)
        clock.now = 60
        self.assertIsNotNone(lock.acquire(JOB, ttl=60))

    def test_slow_run_cannot_release_lease_of_newer_run(self):
        clock, lock = new_lock()
        slow_token = lock.acquire(JOB, ttl=60)
        clock.now = 61
        new_token = lock.acquire(JOB, ttl=60)
        self.assertFalse(lock.release(JOB, slow_token))
        self.assertTrue(lock.holds(JOB, new_token))

    def test_slow_run_cannot_renew_after_losing_its_lease(self):
        clock, lock = new_lock()
        slow_token = lock.acquire(JOB, ttl=60)
        clock.now = 61
        lock.acquire(JOB, ttl=60)
        self.assertFalse(lock.renew(JOB, slow_token, ttl=60))

    def test_renew_keeps_a_long_run_holding_the_lease(self):
        clock, lock = new_lock()
        token = lock.acquire(JOB, ttl=60)
        clock.now = 50
        self.assertTrue(lock.renew(JOB, token, ttl=60))
        clock.now = 100
        self.assertIsNone(lock.acquire(JOB, ttl=60))
        self.assertTrue(lock.holds(JOB, token))

    def test_owner_release_frees_the_lock_immediately(self):
        _, lock = new_lock()
        token = lock.acquire(JOB, ttl=60)
        self.assertTrue(lock.release(JOB, token))
        self.assertIsNotNone(lock.acquire(JOB, ttl=60))

    def test_each_acquire_gets_a_distinct_token(self):
        clock, lock = new_lock()
        first = lock.acquire(JOB, ttl=10)
        clock.now = 10
        second = lock.acquire(JOB, ttl=10)
        self.assertNotEqual(first, second)

    def test_different_jobs_do_not_block_each_other(self):
        _, lock = new_lock()
        lock.acquire(JOB, ttl=60)
        self.assertIsNotNone(lock.acquire("data-sync", ttl=60))


if __name__ == "__main__":
    unittest.main()

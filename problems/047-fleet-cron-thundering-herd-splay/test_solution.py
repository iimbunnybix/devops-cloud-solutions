import unittest

from solution import (DEFAULT_SPLAY_WINDOW_SECONDS, fleet_hostnames, peak_requests_per_second,
                      seconds_over_capacity, splay_offset_seconds, splayed_cron_line,
                      start_offsets)

FLEET_SIZE = 4_000
SERVER_CAPACITY_PER_SECOND = 25
JOB = "metrics-sync"


def fleet() -> list[str]:
    return fleet_hostnames(FLEET_SIZE)


class ReproduceProblem(unittest.TestCase):
    def test_every_host_hits_the_server_in_the_same_second(self):
        offsets = start_offsets(fleet(), JOB, use_splay=False)
        self.assertEqual(peak_requests_per_second(offsets), FLEET_SIZE)

    def test_that_second_is_far_over_server_capacity(self):
        offsets = start_offsets(fleet(), JOB, use_splay=False)
        self.assertEqual(seconds_over_capacity(offsets, SERVER_CAPACITY_PER_SECOND), 1)
        self.assertGreater(peak_requests_per_second(offsets), 100 * SERVER_CAPACITY_PER_SECOND)


class FixDeterministicSplay(unittest.TestCase):
    def test_splay_keeps_peak_load_under_server_capacity(self):
        offsets = start_offsets(fleet(), JOB, use_splay=True)
        self.assertEqual(peak_requests_per_second(offsets), 18)
        self.assertEqual(seconds_over_capacity(offsets, SERVER_CAPACITY_PER_SECOND), 0)

    def test_window_too_short_for_the_fleet_still_overloads(self):
        # 4,000 hosts in 60 seconds is ~67 per second on average.
        offsets = start_offsets(fleet(), JOB, use_splay=True, window_seconds=60)
        self.assertEqual(peak_requests_per_second(offsets), 80)

    def test_same_host_always_gets_the_same_offset(self):
        first = splay_offset_seconds("node-00001", JOB)
        second = splay_offset_seconds("node-00001", JOB)
        self.assertEqual(first, second)

    def test_offset_is_the_same_in_every_process(self):
        # A fixed expected value proves no per-process salt (like hash()) is involved.
        self.assertEqual(splay_offset_seconds("node-00001", JOB), 110)

    def test_offsets_stay_inside_the_window(self):
        for offset in start_offsets(fleet(), JOB, use_splay=True):
            self.assertGreaterEqual(offset, 0)
            self.assertLess(offset, DEFAULT_SPLAY_WINDOW_SECONDS)

    def test_every_minute_of_the_window_gets_a_similar_share(self):
        minute_counts = [0] * (DEFAULT_SPLAY_WINDOW_SECONDS // 60)
        for offset in start_offsets(fleet(), JOB, use_splay=True):
            minute_counts[offset // 60] += 1
        # The ideal is 400 per minute; the actual range is 358..435.
        self.assertGreater(min(minute_counts), 350)
        self.assertLess(max(minute_counts), 450)

    def test_adding_hosts_does_not_move_existing_hosts(self):
        before = start_offsets(fleet(), JOB, use_splay=True)
        after = start_offsets(fleet_hostnames(FLEET_SIZE + 500), JOB, use_splay=True)
        self.assertEqual(after[:FLEET_SIZE], before)

    def test_different_jobs_on_one_host_get_different_offsets(self):
        self.assertNotEqual(splay_offset_seconds("node-00001", "metrics-sync"),
                            splay_offset_seconds("node-00001", "package-sync"))


class FixCronLine(unittest.TestCase):
    def test_cron_line_uses_minute_and_sleep_for_seconds(self):
        line = splayed_cron_line("node-00001", JOB, "/usr/local/bin/metrics-sync")
        self.assertEqual(line, "1 * * * * sleep 50 && /usr/local/bin/metrics-sync")

    def test_one_second_window_puts_everyone_at_minute_zero(self):
        self.assertEqual(splayed_cron_line("node-00001", JOB, "sync", window_seconds=1), "0 * * * * sync")


class FixValidation(unittest.TestCase):
    def test_window_longer_than_the_job_period_is_rejected(self):
        with self.assertRaises(ValueError):
            splay_offset_seconds("node-00001", JOB, window_seconds=5400)

    def test_zero_window_is_rejected(self):
        with self.assertRaises(ValueError):
            splay_offset_seconds("node-00001", JOB, window_seconds=0)

    def test_empty_hostname_is_rejected(self):
        with self.assertRaises(ValueError):
            splay_offset_seconds("", JOB)

    def test_empty_fleet_has_no_load(self):
        self.assertEqual(peak_requests_per_second([]), 0)


if __name__ == "__main__":
    unittest.main()

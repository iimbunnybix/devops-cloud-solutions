import random
import unittest

from solution import (AuthoritativeZone, CachingResolver, RecordVersion,
                      naive_cutover_time, safe_cutover_time, safe_retire_time)

OLD_IP, NEW_IP = "198.51.100.10", "203.0.113.20"   # documentation ranges
DAY = 86_400
OLD_TTL, LOW_TTL = DAY, 60
TTL_LOWERED_AT = 0.0
HOUR = 3_600
RESOLVERS = 200
MEAN_SECONDS_BETWEEN_QUERIES = 600


def history_with_cutover(cutover_at):
    """Record served for a week with a 24 h TTL, lowered at 0, moved at cutover."""
    return [RecordVersion(-7 * DAY, OLD_IP, OLD_TTL),
            RecordVersion(TTL_LOWERED_AT, OLD_IP, LOW_TTL),
            RecordVersion(cutover_at, NEW_IP, LOW_TTL)]


def stale_answers_after(cutover_at, seed=42):
    """Count lookups after the cutover that still return the old IP.

    Each resolver queries at random times from two days before the TTL change
    until two days after it, so some hold a 24 h answer when the TTL drops.
    """
    zone = AuthoritativeZone(history_with_cutover(cutover_at))
    rng = random.Random(seed)
    stale = total = stuck_resolvers = 0
    last_stale_time = cutover_at
    for _ in range(RESOLVERS):
        resolver = CachingResolver()
        stale_before = stale
        now = -2 * DAY + rng.uniform(0, DAY)
        while now < 2 * DAY:
            ip = resolver.lookup(now, zone)
            if now >= cutover_at:
                total += 1
                if ip == OLD_IP:
                    stale += 1
                    last_stale_time = max(last_stale_time, now)
            now += rng.expovariate(1 / MEAN_SECONDS_BETWEEN_QUERIES)
        if stale > stale_before:
            stuck_resolvers += 1
    return stale, total, last_stale_time - cutover_at, stuck_resolvers


class ReproduceEarlyCutover(unittest.TestCase):
    def test_naive_plan_cuts_over_after_one_hour(self):
        self.assertEqual(naive_cutover_time(TTL_LOWERED_AT, LOW_TTL, HOUR - LOW_TTL), HOUR)

    def test_resolvers_keep_old_ip_for_most_of_a_day(self):
        stale, total, longest, stuck = stale_answers_after(HOUR)
        self.assertEqual((stale, total), (13_582, 56_157))   # 24.2% of lookups
        self.assertEqual(stuck, 192)                          # of 200 resolvers
        self.assertGreater(longest, 22.5 * HOUR)


class FixWaitForOldTtl(unittest.TestCase):
    def test_safe_cutover_waits_for_the_old_ttl(self):
        self.assertEqual(safe_cutover_time(history_with_cutover(2 * DAY), TTL_LOWERED_AT), DAY)

    def test_no_resolver_returns_old_ip_after_safe_cutover(self):
        cutover = safe_cutover_time(history_with_cutover(2 * DAY), TTL_LOWERED_AT)
        stale, _total, _longest, stuck = stale_answers_after(cutover)
        self.assertEqual((stale, stuck), (0, 0))

    def test_old_server_retires_one_low_ttl_after_cutover(self):
        self.assertEqual(safe_retire_time(DAY, LOW_TTL), DAY + LOW_TTL)

    def test_earlier_long_ttl_version_still_counts(self):
        history = [RecordVersion(-10 * DAY, OLD_IP, 7 * DAY),
                   RecordVersion(-DAY, OLD_IP, 300),
                   RecordVersion(0, OLD_IP, 60)]
        self.assertEqual(safe_cutover_time(history, 0), 6 * DAY)

    def test_versions_after_the_ttl_change_are_ignored(self):
        history = [RecordVersion(-DAY, OLD_IP, 300), RecordVersion(0, OLD_IP, 60),
                   RecordVersion(10, NEW_IP, 9_999)]
        self.assertEqual(safe_cutover_time(history, 0), 300)


class ResolverCache(unittest.TestCase):
    def test_resolver_keeps_ttl_it_fetched_even_after_provider_changes(self):
        zone = AuthoritativeZone(history_with_cutover(HOUR))
        resolver = CachingResolver()
        resolver.lookup(-10, zone)
        self.assertEqual(resolver.lookup(HOUR + 1, zone), OLD_IP)
        self.assertEqual(resolver.lookup(DAY - 10, zone), NEW_IP)

    def test_empty_history_is_rejected(self):
        with self.assertRaises(ValueError):
            AuthoritativeZone([])


if __name__ == "__main__":
    unittest.main()

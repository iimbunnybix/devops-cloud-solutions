import unittest

from solution import (ORIGINAL_BUCKETS, SLO_ALIGNED_BUCKETS, SLO_TARGET, Histogram, SloBucketMissing,
                      error_budget_used, good_fraction, histogram_of, interpolated_good_fraction,
                      naive_good_fraction, require_slo_bucket, simulated_latencies, true_good_fraction)

REQUESTS = 100_000
SEED = 70
REGRESSION_SHARE = 0.08      # 8% of requests take a new synchronous call...
REGRESSION_SECONDS = 0.15    # ...that adds 150 ms


def healthy_traffic():
    return simulated_latencies(REQUESTS, 0.0, REGRESSION_SECONDS, SEED)


def regressed_traffic():
    return simulated_latencies(REQUESTS, REGRESSION_SHARE, REGRESSION_SECONDS, SEED)


class ReproduceProblem(unittest.TestCase):
    def test_regression_drops_real_good_share_below_target(self):
        self.assertAlmostEqual(true_good_fraction(regressed_traffic()), 0.9555, places=4)

    def test_nearest_bucket_query_still_reports_slo_met(self):
        reported = naive_good_fraction(histogram_of(regressed_traffic(), ORIGINAL_BUCKETS))
        self.assertAlmostEqual(reported, 0.9961, places=4)
        self.assertGreaterEqual(reported, SLO_TARGET)

    def test_nearest_bucket_query_cannot_see_the_regression(self):
        before = naive_good_fraction(histogram_of(healthy_traffic(), ORIGINAL_BUCKETS))
        after = naive_good_fraction(histogram_of(regressed_traffic(), ORIGINAL_BUCKETS))
        self.assertEqual(before, after)

    def test_interpolation_pages_even_when_healthy(self):
        estimate = interpolated_good_fraction(histogram_of(healthy_traffic(), ORIGINAL_BUCKETS))
        self.assertAlmostEqual(estimate, 0.9037, places=4)
        self.assertAlmostEqual(true_good_fraction(healthy_traffic()), 0.9961, places=4)


class FixSloAlignedBucket(unittest.TestCase):
    def test_aligned_bucket_matches_the_true_good_share(self):
        latencies = regressed_traffic()
        measured = good_fraction(histogram_of(latencies, SLO_ALIGNED_BUCKETS))
        self.assertEqual(measured, true_good_fraction(latencies))

    def test_regression_burns_budget_over_four_times_too_fast(self):
        measured = good_fraction(histogram_of(regressed_traffic(), SLO_ALIGNED_BUCKETS))
        self.assertAlmostEqual(error_budget_used(measured), 4.45, places=2)

    def test_healthy_traffic_stays_within_budget(self):
        measured = good_fraction(histogram_of(healthy_traffic(), SLO_ALIGNED_BUCKETS))
        self.assertLess(error_budget_used(measured), 1.0)

    def test_missing_slo_bucket_is_refused(self):
        with self.assertRaises(SloBucketMissing):
            good_fraction(histogram_of(healthy_traffic(), ORIGINAL_BUCKETS))

    def test_config_check_passes_for_aligned_buckets(self):
        require_slo_bucket(SLO_ALIGNED_BUCKETS, 0.3)

    def test_latency_exactly_at_threshold_counts_as_good(self):
        histogram = histogram_of([0.3, 0.3000001], SLO_ALIGNED_BUCKETS)
        self.assertEqual(good_fraction(histogram), 0.5)

    def test_no_traffic_counts_as_meeting_the_slo(self):
        self.assertEqual(good_fraction(Histogram(SLO_ALIGNED_BUCKETS)), 1.0)

    def test_unsorted_bucket_bounds_are_rejected(self):
        with self.assertRaises(ValueError):
            Histogram((0.5, 0.3))


if __name__ == "__main__":
    unittest.main()

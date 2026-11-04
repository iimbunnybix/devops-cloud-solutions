import json
import random
import unittest

from solution import (LogHistogram, average_of_percentiles, exact_percentile,
                      merged_percentile, weighted_average_of_percentiles)

SLO_P99 = 0.300   # seconds


def fleet(seed=42, hosts=20, per_host=10_000, bad_host=7):
    """19 healthy hosts (~50 ms median) and one host with a failing disk (1.2-2.5 s)."""
    rng = random.Random(seed)
    data = {}
    for h in range(hosts):
        if h == bad_host:
            data[h] = [rng.uniform(1.2, 2.5) for _ in range(per_host)]
        else:
            data[h] = [rng.lognormvariate(-3.0, 0.5) for _ in range(per_host)]
    return data


def histograms(data):
    out = []
    for values in data.values():
        h = LogHistogram()
        for v in values:
            h.add(v)
        out.append(h)
    return out


class ReproduceAveragedPercentiles(unittest.TestCase):
    def test_average_of_host_p99s_looks_healthy(self):
        data = fleet()
        everything = [v for vs in data.values() for v in vs]
        averaged = average_of_percentiles(data, 99)
        true_p99 = exact_percentile(everything, 99)
        self.assertLess(averaged, SLO_P99)        # ~275 ms: dashboard is green
        self.assertGreater(true_p99, 2.0)         # ~2.2 s: 5% of users wait seconds

    def test_traffic_weighting_does_not_fix_it(self):
        data = fleet()
        data[0] = data[0] * 3                     # host 0 serves more traffic
        everything = [v for vs in data.values() for v in vs]
        weighted = weighted_average_of_percentiles(data, 99)
        self.assertLess(weighted, SLO_P99)
        self.assertGreater(exact_percentile(everything, 99), 2.0)


class MergeableHistogramFix(unittest.TestCase):
    def test_merged_histograms_match_true_percentiles(self):
        data = fleet()
        everything = [v for vs in data.values() for v in vs]
        hs = histograms(data)
        for q in (50, 90, 95, 99, 99.9):
            exact = exact_percentile(everything, q)
            approx = merged_percentile(hs, q)
            self.assertLessEqual(abs(approx - exact) / exact, 0.01, q)
        self.assertGreater(merged_percentile(hs, 99), SLO_P99)   # alert fires

    def test_relative_error_bound_holds_on_random_data(self):
        for seed in range(5):
            rng = random.Random(seed)
            values = [rng.expovariate(1 / 0.2) + 0.002 for _ in range(5_000)]
            h = LogHistogram(relative_accuracy=0.02)
            for v in values:
                h.add(v)
            for q in (1, 25, 50, 75, 99, 100):
                exact = exact_percentile(values, q)
                self.assertLessEqual(abs(h.percentile(q) - exact) / exact, 0.02)

    def test_merge_is_order_independent_and_equals_single_histogram(self):
        data = fleet(hosts=5, per_host=2_000)
        hs = histograms(data)
        a = LogHistogram()
        for h in hs:
            a.merge(h)
        b = LogHistogram()
        for h in reversed(hs):
            b.merge(h)
        single = LogHistogram()
        for vs in data.values():
            for v in vs:
                single.add(v)
        self.assertEqual(a.counts, b.counts)
        self.assertEqual(a.counts, single.counts)
        self.assertEqual(a.percentile(99), single.percentile(99))

    def test_round_trip_serialization(self):
        h = histograms(fleet(hosts=2, per_host=1_000))[0]
        h.add(0.0)
        h2 = LogHistogram.from_dict(json.loads(json.dumps(h.to_dict())))
        self.assertEqual(h2.count, h.count)
        self.assertEqual(h2.percentile(99), h.percentile(99))

    def test_bucket_count_stays_small(self):
        h = histograms(fleet())[7]
        self.assertLess(len(h.counts), 60)   # 10,000 samples -> a few dozen buckets

    def test_edge_cases(self):
        h = LogHistogram()
        with self.assertRaises(ValueError):
            h.percentile(99)
        h.add(0.0, n=99)
        h.add(5.0)
        self.assertEqual(h.percentile(50), 0.0)
        self.assertAlmostEqual(h.percentile(100), 5.0, delta=0.05)
        with self.assertRaises(ValueError):
            h.add(-1)
        with self.assertRaises(ValueError):
            h.merge(LogHistogram(relative_accuracy=0.05))
        with self.assertRaises(ValueError):
            exact_percentile([], 50)
        with self.assertRaises(ValueError):
            h.percentile(101)
        self.assertEqual(exact_percentile([3, 1, 2], 0), 1)


if __name__ == "__main__":
    unittest.main()

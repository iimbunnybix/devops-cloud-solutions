import math
import random
import unittest

from solution import (CanaryAnalyzer, Cohort, Verdict, one_sided_p_value, p99,
                      static_threshold_gate)


def binomial(n, p, rng):
    """Seeded Binomial(n, p) via geometric gaps (works on Python 3.10+)."""
    count, i, log_q = 0, -1, math.log(1 - p)
    while True:
        i += int(math.log(1 - rng.random()) / log_q) + 1
        if i >= n:
            return count
        count += 1


def simulate(n, err_rate, seed, lat_mu=80.0, lat_tail=1.0):
    rng = random.Random(seed)
    errors = binomial(n, err_rate, rng)
    lat = [rng.lognormvariate(0, 0.5) * lat_mu * (lat_tail if rng.random() < 0.02 else 1.0)
           for _ in range(min(n, 3_000))]
    return Cohort(n, errors, lat)


class CanaryTests(unittest.TestCase):
    def setUp(self):
        self.a = CanaryAnalyzer()

    def test_static_gate_promotes_build_that_quadruples_errors(self):
        canary = simulate(20_000, 0.008, seed=1)       # baseline runs at 0.2%
        self.assertEqual(static_threshold_gate(canary), Verdict.PROMOTE)  # the incident

    def test_analyzer_rolls_back_same_build(self):
        base, canary = simulate(200_000, 0.002, seed=2), simulate(20_000, 0.008, seed=1)
        r = self.a.analyze(base, canary)
        self.assertEqual(r.verdict, Verdict.ROLLBACK)
        self.assertLess(r.p_value, 1e-6)

    def test_healthy_canary_promoted(self):
        base, canary = simulate(200_000, 0.002, seed=3), simulate(20_000, 0.002, seed=4)
        self.assertEqual(self.a.analyze(base, canary).verdict, Verdict.PROMOTE)

    def test_false_rollback_rate_is_low_for_identical_builds(self):
        rollbacks = 0
        for s in range(200):
            base = Cohort(100_000, binomial(100_000, 0.002, random.Random(s)))
            can = Cohort(10_000, binomial(10_000, 0.002, random.Random(10_000 + s)))
            rollbacks += self.a.analyze(base, can).verdict is Verdict.ROLLBACK
        self.assertLessEqual(rollbacks, 4)            # alpha=1% → ~2 expected of 200

    def test_waits_for_enough_traffic(self):
        base, canary = Cohort(50_000, 100), Cohort(500, 5)
        self.assertEqual(self.a.analyze(base, canary).verdict, Verdict.CONTINUE)

    def test_tiny_relative_increase_not_blocking(self):
        # huge traffic makes +10% statistically significant, but below 25% effect floor
        base, canary = Cohort(10_000_000, 20_000), Cohort(5_000_000, 11_000)
        self.assertLess(one_sided_p_value(base, canary), 0.01)
        self.assertEqual(self.a.analyze(base, canary).verdict, Verdict.PROMOTE)

    def test_latency_regression_rolls_back(self):
        base = simulate(5_000, 0.002, seed=5)
        canary = simulate(5_000, 0.002, seed=6, lat_tail=4.0)   # slow tail only
        r = self.a.analyze(base, canary)
        self.assertEqual(r.verdict, Verdict.ROLLBACK)
        self.assertTrue(any("p99" in x for x in r.reasons))

    def test_zero_baseline_errors(self):
        self.assertEqual(self.a.analyze(Cohort(50_000, 0), Cohort(5_000, 0)).verdict,
                         Verdict.PROMOTE)
        self.assertEqual(self.a.analyze(Cohort(50_000, 0), Cohort(5_000, 30)).verdict,
                         Verdict.ROLLBACK)

    def test_single_canary_error_with_zero_baseline_errors_is_not_rollback(self):
        # 1 error in 55k requests; canary has 5k/55k of traffic, so p ~ 0.091.
        r = self.a.analyze(Cohort(50_000, 0), Cohort(5_000, 1))
        self.assertEqual(r.verdict, Verdict.PROMOTE)
        self.assertAlmostEqual(r.p_value, 5_000 / 55_000, places=6)

    def test_binomial_helper_mean(self):
        rng = random.Random(0)
        mean = sum(binomial(10_000, 0.01, rng) for _ in range(500)) / 500
        self.assertAlmostEqual(mean, 100, delta=2)

    def test_p99(self):
        self.assertAlmostEqual(p99(list(range(1, 101))), 99.01, places=2)


if __name__ == "__main__":
    unittest.main()

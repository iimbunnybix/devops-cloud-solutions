import unittest

from solution import (CounterSample, error_ratio, naive_increase, per_second_rate,
                      reset_aware_increase, should_page_on_errors)

SCRAPE_INTERVAL_S = 15
WINDOW_SCRAPES = 20          # 5 minutes of 15-second scrapes


def counter_series(per_scrape_steps: list[float], start_value: float = 0.0,
                   reset_at_scrape: int | None = None) -> list[CounterSample]:
    """Build counter samples; at reset_at_scrape the process restarts from 0."""
    samples = []
    value = start_value
    for scrape_index, step in enumerate(per_scrape_steps):
        if scrape_index == reset_at_scrape:
            value = 0.0
        value += step
        samples.append(CounterSample(scrape_index * SCRAPE_INTERVAL_S, value))
    return samples


def restart_incident() -> tuple[list[CounterSample], list[CounterSample]]:
    """Healthy pod (1% errors) restarts halfway and then fails 50% of requests.

    The pod had served 1,000,000 requests (10,000 errors) before the window.
    """
    requests_per_scrape = [1_500.0] * WINDOW_SCRAPES
    errors_per_scrape = [15.0] * 10 + [750.0] * 10
    requests = counter_series(requests_per_scrape, 1_000_000, reset_at_scrape=10)
    errors = counter_series(errors_per_scrape, 10_000, reset_at_scrape=10)
    return requests, errors


class ReproduceProblem(unittest.TestCase):
    def test_naive_increase_goes_negative_after_a_restart(self):
        requests, _ = restart_incident()
        self.assertLess(naive_increase(requests), 0)

    def test_naive_error_ratio_hides_a_failing_restarted_pod(self):
        requests, errors = restart_incident()
        self.assertFalse(should_page_on_errors(requests, errors, use_reset_aware=False))


class FixResetAwareIncrease(unittest.TestCase):
    def test_increase_counts_through_the_restart(self):
        requests, _ = restart_incident()
        # 19 intervals of 1,500 requests; the first scrape is the baseline.
        self.assertEqual(reset_aware_increase(requests), 19 * 1_500)

    def test_error_ratio_reflects_the_failing_pod(self):
        requests, errors = restart_incident()
        ratio = error_ratio(requests, errors)
        self.assertAlmostEqual(ratio, (9 * 15 + 10 * 750) / (19 * 1_500))
        self.assertTrue(should_page_on_errors(requests, errors))

    def test_without_a_reset_both_methods_agree(self):
        samples = counter_series([100.0] * WINDOW_SCRAPES, 5_000)
        self.assertEqual(reset_aware_increase(samples), naive_increase(samples))

    def test_two_restarts_in_one_window_are_both_counted(self):
        values = [1_000, 1_010, 1_020, 5, 15, 25, 3, 13]
        samples = [CounterSample(index * SCRAPE_INTERVAL_S, value)
                   for index, value in enumerate(values)]
        # +10 +10, restart to 5, +10 +10, restart to 3, +10
        self.assertEqual(reset_aware_increase(samples), 10 + 10 + 5 + 10 + 10 + 3 + 10)

    def test_out_of_order_scrapes_give_the_same_result(self):
        requests, _ = restart_incident()
        shuffled = list(reversed(requests))
        self.assertEqual(reset_aware_increase(shuffled), reset_aware_increase(requests))

    def test_rate_is_increase_per_second(self):
        samples = counter_series([150.0] * 5)
        self.assertEqual(per_second_rate(samples), 10.0)


class FixEdgeCases(unittest.TestCase):
    def test_single_sample_has_no_increase(self):
        self.assertIsNone(reset_aware_increase([CounterSample(0, 42)]))

    def test_samples_at_the_same_time_have_no_rate(self):
        self.assertIsNone(per_second_rate([CounterSample(5, 1), CounterSample(5, 2)]))

    def test_no_traffic_does_not_page(self):
        idle = counter_series([0.0] * 5, 300)
        self.assertIsNone(error_ratio(idle, idle))
        self.assertFalse(should_page_on_errors(idle, idle))

    def test_healthy_traffic_does_not_page(self):
        requests = counter_series([1_000.0] * WINDOW_SCRAPES)
        errors = counter_series([10.0] * WINDOW_SCRAPES)
        self.assertFalse(should_page_on_errors(requests, errors))


if __name__ == "__main__":
    unittest.main()

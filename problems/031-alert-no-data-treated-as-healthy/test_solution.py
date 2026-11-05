import random
import unittest

from solution import (ABSENT_AFTER_MINUTES, FIRING, NO_DATA, OK, Scrape, broken_deploy_incident,
                      checkout_traffic, consecutive_missing, error_ratio, evaluate,
                      first_page_minute, naive_evaluate)

DEPLOY_MINUTE = 60


def healthy_minutes(count: int) -> list:
    """Steady checkout traffic with 0.25% errors."""
    return [Scrape(requests=1_200, errors=3) for _ in range(count)]


def failing_minutes(count: int) -> list:
    """Checkout traffic with 30% errors."""
    return [Scrape(requests=1_200, errors=360) for _ in range(count)]


class ReproduceProblem(unittest.TestCase):
    def test_naive_alert_never_pages_when_metrics_vanish_during_outage(self):
        observed, _ = broken_deploy_incident()
        self.assertIsNone(first_page_minute(observed, naive_evaluate))

    def test_the_outage_was_real_and_would_have_paged_with_metrics(self):
        _, truth = broken_deploy_incident()
        self.assertEqual(first_page_minute(truth, naive_evaluate), DEPLOY_MINUTE)
        self.assertGreater(error_ratio(truth[DEPLOY_MINUTE:]), 0.29)

    def test_naive_alert_reports_ok_for_an_empty_window(self):
        series = healthy_minutes(10) + [None] * 10
        self.assertEqual(naive_evaluate(series, 19), OK)


class FixAlertOnAbsentData(unittest.TestCase):
    def test_missing_metrics_page_at_the_fifth_missed_scrape(self):
        observed, _ = broken_deploy_incident()
        self.assertEqual(first_page_minute(observed, evaluate),
                         DEPLOY_MINUTE + ABSENT_AFTER_MINUTES - 1)

    def test_no_data_state_persists_while_metrics_stay_missing(self):
        observed, _ = broken_deploy_incident()
        self.assertEqual(evaluate(observed, len(observed) - 1), NO_DATA)

    def test_healthy_traffic_does_not_page(self):
        observed, _ = broken_deploy_incident()
        self.assertIsNone(first_page_minute(observed[:DEPLOY_MINUTE], evaluate))

    def test_a_single_missed_scrape_does_not_page(self):
        series = healthy_minutes(10) + [None] + healthy_minutes(10)
        self.assertIsNone(first_page_minute(series, evaluate))

    def test_four_missed_scrapes_in_a_row_do_not_page(self):
        series = healthy_minutes(10) + [None] * (ABSENT_AFTER_MINUTES - 1) + healthy_minutes(5)
        self.assertIsNone(first_page_minute(series, evaluate))

    def test_high_error_ratio_still_fires(self):
        series = healthy_minutes(10) + failing_minutes(3)
        self.assertEqual(evaluate(series, 10), FIRING)

    def test_errors_in_scrapes_that_arrive_fire_even_when_others_are_missing(self):
        flapping = []
        for scrape in failing_minutes(6):
            flapping.extend([scrape, None])                  # every other scrape lost
        self.assertEqual(evaluate(flapping, len(flapping) - 1), FIRING)

    def test_present_scrapes_with_zero_traffic_are_ok_not_a_crash(self):
        series = [Scrape(requests=0, errors=0) for _ in range(10)]
        self.assertEqual(evaluate(series, 9), OK)

    def test_metrics_coming_back_clears_no_data(self):
        series = healthy_minutes(10) + [None] * 10 + healthy_minutes(1)
        self.assertEqual(evaluate(series, 19), NO_DATA)
        self.assertEqual(evaluate(series, 20), OK)

    def test_consecutive_missing_counts_only_the_trailing_gap(self):
        series = [None, None] + healthy_minutes(1) + [None] * 3
        self.assertEqual(consecutive_missing(series, len(series) - 1), 3)

    def test_seeded_traffic_is_reproducible(self):
        first = checkout_traffic(5, 100, 0.1, random.Random(1))
        second = checkout_traffic(5, 100, 0.1, random.Random(1))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()

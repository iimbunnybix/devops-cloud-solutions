import unittest

from solution import (alert_firing_times, lint_rate_windows, no_data_share, per_second_rate,
                      safe_rate_window, scrape_counter)

SCRAPE_INTERVAL = 60.0
EVALUATION_INTERVAL = 15.0
OUTAGE_SECONDS = 20 * 60
ERRORS_PER_SECOND = 12.0        # during the outage
ALERT_THRESHOLD = 5.0
ORIGINAL_WINDOW = 60.0


def outage_samples(missed=None):
    return scrape_counter(0.0, OUTAGE_SECONDS, SCRAPE_INTERVAL, ERRORS_PER_SECOND, missed)


def evaluation_times(start: float = 300.0) -> list[float]:
    """Rule evaluations from 5 minutes into the outage until it ends."""
    times = []
    now = start
    while now <= OUTAGE_SECONDS:
        times.append(now)
        now += EVALUATION_INTERVAL
    return times


class ReproduceProblem(unittest.TestCase):
    def test_one_minute_window_never_fires_during_the_outage(self):
        firing = alert_firing_times(outage_samples(), ORIGINAL_WINDOW, ALERT_THRESHOLD,
                                    evaluation_times())
        self.assertEqual(firing, [])

    def test_every_evaluation_has_no_data(self):
        self.assertEqual(no_data_share(outage_samples(), ORIGINAL_WINDOW, evaluation_times()), 1.0)

    def test_a_window_of_two_scrapes_breaks_after_one_missed_scrape(self):
        samples = outage_samples(missed={600.0})
        self.assertIsNone(per_second_rate(samples, now=650.0, window=120.0))


class FixWindowFromScrapeInterval(unittest.TestCase):
    def test_safe_window_is_four_scrape_intervals(self):
        self.assertEqual(safe_rate_window(SCRAPE_INTERVAL, ORIGINAL_WINDOW), 240.0)

    def test_longer_requested_window_is_kept(self):
        self.assertEqual(safe_rate_window(SCRAPE_INTERVAL, 600.0), 600.0)

    def test_alert_fires_at_every_evaluation_during_the_outage(self):
        window = safe_rate_window(SCRAPE_INTERVAL, ORIGINAL_WINDOW)
        times = evaluation_times()
        firing = alert_firing_times(outage_samples(), window, ALERT_THRESHOLD, times)
        self.assertEqual(firing, times)

    def test_rate_value_matches_the_real_error_rate(self):
        window = safe_rate_window(SCRAPE_INTERVAL)
        self.assertAlmostEqual(per_second_rate(outage_samples(), 900.0, window), ERRORS_PER_SECOND)

    def test_safe_window_survives_a_missed_scrape(self):
        samples = outage_samples(missed={600.0})
        window = safe_rate_window(SCRAPE_INTERVAL)
        self.assertAlmostEqual(per_second_rate(samples, 650.0, window), ERRORS_PER_SECOND)

    def test_low_error_rate_does_not_fire(self):
        samples = scrape_counter(0.0, OUTAGE_SECONDS, SCRAPE_INTERVAL, 1.0)
        window = safe_rate_window(SCRAPE_INTERVAL)
        self.assertEqual(alert_firing_times(samples, window, ALERT_THRESHOLD, evaluation_times()), [])

    def test_linter_flags_only_too_short_windows(self):
        rules = {"CheckoutErrors": 60.0, "PaymentErrors": 300.0, "LoginErrors": 120.0}
        problems = lint_rate_windows(rules, SCRAPE_INTERVAL)
        self.assertEqual(len(problems), 2)
        self.assertTrue(problems[0].startswith("CheckoutErrors: window 60s < 240s"))
        self.assertTrue(problems[1].startswith("LoginErrors"))


if __name__ == "__main__":
    unittest.main()

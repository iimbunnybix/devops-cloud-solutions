import unittest

from solution import (PENDING_MINUTES, WINDOW_MINUTES, WindowedAlert, flapping_outage,
                      naive_pending_alert, windowed_alert_pages)

HEALTHY_MINUTES = 30
OUTAGE_MINUTES = 120
OUTAGE_START = HEALTHY_MINUTES
DIP_EVERY = 6            # one good minute in every six
BAD_RATE, DIP_RATE, HEALTHY_RATE = 8.0, 3.0, 0.5


def incident():
    """Two hours at 8% errors, dipping to 3% for one minute in every six."""
    return flapping_outage(HEALTHY_MINUTES, OUTAGE_MINUTES, DIP_EVERY, BAD_RATE, DIP_RATE, HEALTHY_RATE)


def steady_outage():
    return [HEALTHY_RATE] * HEALTHY_MINUTES + [BAD_RATE] * OUTAGE_MINUTES + [HEALTHY_RATE] * HEALTHY_MINUTES


def short_spike(minutes):
    return [HEALTHY_RATE] * HEALTHY_MINUTES + [BAD_RATE] * minutes + [HEALTHY_RATE] * HEALTHY_MINUTES


class ReproduceProblem(unittest.TestCase):
    def test_two_hour_outage_with_periodic_dips_never_pages(self):
        self.assertEqual(naive_pending_alert(incident()), [])

    def test_shorter_pending_time_pages_twenty_times_for_one_incident(self):
        self.assertEqual(len(naive_pending_alert(incident(), pending_minutes=3)), 20)


class FixWindowedAlert(unittest.TestCase):
    def test_flapping_outage_pages_once_nine_minutes_in(self):
        self.assertEqual(windowed_alert_pages(incident()), [OUTAGE_START + 8])

    def test_steady_outage_pages_no_later_than_the_original_rule(self):
        self.assertEqual(naive_pending_alert(steady_outage()), [OUTAGE_START + PENDING_MINUTES - 1])
        self.assertEqual(windowed_alert_pages(steady_outage()), [OUTAGE_START + 7])

    def test_three_minute_spike_does_not_page(self):
        self.assertEqual(windowed_alert_pages(short_spike(3)), [])

    def test_alert_resolves_after_recovery_and_can_page_again(self):
        rates = steady_outage() + [BAD_RATE] * 20
        self.assertEqual(len(windowed_alert_pages(rates)), 2)

    def test_one_good_minute_does_not_resolve_a_firing_alert(self):
        alert = WindowedAlert()
        for _ in range(WINDOW_MINUTES):
            alert.evaluate(BAD_RATE)
        alert.evaluate(HEALTHY_RATE)
        self.assertTrue(alert.firing)

    def test_partial_window_after_restart_does_not_page(self):
        alert = WindowedAlert()
        pages = [alert.evaluate(BAD_RATE) for _ in range(WINDOW_MINUTES - 1)]
        self.assertEqual(pages, [False] * (WINDOW_MINUTES - 1))

    def test_rate_exactly_at_threshold_is_not_a_breach(self):
        self.assertEqual(windowed_alert_pages([5.0] * 60), [])

    def test_resolve_level_must_be_below_fire_level(self):
        with self.assertRaises(ValueError):
            WindowedAlert(resolve_fraction=0.8, fire_fraction=0.8)


if __name__ == "__main__":
    unittest.main()

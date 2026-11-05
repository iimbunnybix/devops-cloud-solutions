import unittest

from solution import (T3_LARGE, first_warning_minute, minutes_until_exhaustion,
                      naive_cpu_alert, should_warn, simulate)

BUSINESS_DAY_MINUTES = 10 * 60
BUSY_PERCENT = 60.0
QUIET_PERCENT = 5.0
START_BALANCE = 288.0
RECENT_WINDOW = 30


def business_day() -> list[float]:
    """CPU the service needs from 09:00 to 19:00: twice the t3.large baseline."""
    return [BUSY_PERCENT] * BUSINESS_DAY_MINUTES


def delivered(minutes) -> list[float]:
    return [minute.delivered_percent for minute in minutes]


class ReproduceProblem(unittest.TestCase):
    def test_instance_is_throttled_to_baseline_after_eight_hours(self):
        minutes = simulate(T3_LARGE, START_BALANCE, business_day())
        first_throttled = next(i for i, m in enumerate(minutes) if m.throttled)
        self.assertEqual(first_throttled, 480)
        self.assertAlmostEqual(minutes[-1].delivered_percent, T3_LARGE.baseline_percent)

    def test_cpu_threshold_alert_never_fires_while_throttled(self):
        minutes = simulate(T3_LARGE, START_BALANCE, business_day())
        self.assertEqual(sum(m.throttled for m in minutes), 120)
        self.assertFalse(naive_cpu_alert(delivered(minutes)))


class FixCreditSimulation(unittest.TestCase):
    def test_usage_below_baseline_earns_credits_up_to_the_cap(self):
        minutes = simulate(T3_LARGE, T3_LARGE.max_balance - 1, [QUIET_PERCENT] * 60)
        self.assertEqual(minutes[-1].balance_after, T3_LARGE.max_balance)

    def test_usage_at_baseline_keeps_balance_constant(self):
        minutes = simulate(T3_LARGE, 100.0, [T3_LARGE.baseline_percent] * 60)
        self.assertAlmostEqual(minutes[-1].balance_after, 100.0)
        self.assertFalse(any(m.throttled for m in minutes))

    def test_empty_balance_delivers_exactly_the_baseline(self):
        minute = simulate(T3_LARGE, 0.0, [100.0])[0]
        self.assertAlmostEqual(minute.delivered_percent, T3_LARGE.baseline_percent)
        self.assertEqual(minute.balance_after, 0.0)


class FixExhaustionWarning(unittest.TestCase):
    def test_exhaustion_is_predicted_from_burn_rate(self):
        # 60% of 2 vCPUs spends 1.2 credits/min, earning 0.6: net 0.6/min.
        self.assertAlmostEqual(minutes_until_exhaustion(T3_LARGE, 288.0, [60.0]), 480.0)

    def test_no_prediction_when_earning_faster_than_spending(self):
        self.assertIsNone(minutes_until_exhaustion(T3_LARGE, 10.0, [20.0]))
        self.assertIsNone(minutes_until_exhaustion(T3_LARGE, 10.0, []))

    def test_warning_fires_two_hours_before_throttling(self):
        minutes = simulate(T3_LARGE, START_BALANCE, business_day())
        self.assertEqual(first_warning_minute(T3_LARGE, minutes, RECENT_WINDOW), 360)

    def test_warning_keeps_firing_after_credits_run_out(self):
        minutes = simulate(T3_LARGE, START_BALANCE, business_day())
        recent = delivered(minutes[-RECENT_WINDOW:])
        self.assertTrue(should_warn(T3_LARGE, minutes[-1].balance_after, recent))

    def test_quiet_instance_with_low_balance_does_not_warn(self):
        self.assertFalse(should_warn(T3_LARGE, 50.0, [QUIET_PERCENT] * RECENT_WINDOW))


if __name__ == "__main__":
    unittest.main()

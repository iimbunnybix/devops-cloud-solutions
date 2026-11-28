import unittest
from datetime import date

from solution import (DailyCounts, budget_used, build_history, calendar_month_budget_used,
                      deploys_allowed, first_day_deploys_resume, original_deploys_allowed,
                      rolling_budget_used)

REQUESTS_PER_DAY = 1_000_000
BASELINE_ERRORS = 200                   # 0.02% on a normal day
INCIDENT_EXTRA_ERRORS = 20_000
FIRST_INCIDENT = date(2026, 1, 31)
SECOND_INCIDENT = date(2026, 2, 2)


def two_incidents_around_month_end():
    return build_history(date(2026, 1, 1), date(2026, 3, 31), REQUESTS_PER_DAY, BASELINE_ERRORS,
                         {FIRST_INCIDENT: INCIDENT_EXTRA_ERRORS,
                          SECOND_INCIDENT: INCIDENT_EXTRA_ERRORS})


def every_day_of(year: int, month: int, last_day: int) -> list[date]:
    return [date(year, month, day) for day in range(1, last_day + 1)]


class ReproduceProblem(unittest.TestCase):
    def test_budget_resets_the_day_after_a_big_incident(self):
        history = two_incidents_around_month_end()
        self.assertAlmostEqual(calendar_month_budget_used(history, FIRST_INCIDENT, REQUESTS_PER_DAY),
                               0.845, places=3)
        self.assertAlmostEqual(calendar_month_budget_used(history, date(2026, 2, 1), REQUESTS_PER_DAY),
                               200 / 28_000)

    def test_deploys_never_freeze_in_either_month(self):
        history = two_incidents_around_month_end()
        days = every_day_of(2026, 1, 31) + every_day_of(2026, 2, 28)
        for day in days:
            self.assertTrue(original_deploys_allowed(history, day, REQUESTS_PER_DAY), day)

    def test_users_actually_got_one_and_a_half_budgets_of_errors(self):
        history = two_incidents_around_month_end()
        self.assertAlmostEqual(rolling_budget_used(history, SECOND_INCIDENT), 46_000 / 30_000)


class FixRollingWindow(unittest.TestCase):
    def test_deploys_freeze_on_the_day_of_the_second_incident(self):
        history = two_incidents_around_month_end()
        self.assertTrue(deploys_allowed(history, date(2026, 2, 1)))
        self.assertFalse(deploys_allowed(history, SECOND_INCIDENT))

    def test_first_incident_still_counts_after_the_month_changes(self):
        history = two_incidents_around_month_end()
        self.assertAlmostEqual(rolling_budget_used(history, date(2026, 2, 1)), 26_000 / 30_000)

    def test_freeze_lifts_once_the_first_incident_leaves_the_window(self):
        history = two_incidents_around_month_end()
        self.assertEqual(first_day_deploys_resume(history, SECOND_INCIDENT), date(2026, 3, 2))

    def test_quiet_period_uses_a_fifth_of_the_budget(self):
        history = build_history(date(2026, 1, 1), date(2026, 3, 31), REQUESTS_PER_DAY,
                                BASELINE_ERRORS, {})
        self.assertAlmostEqual(rolling_budget_used(history, date(2026, 3, 15)), 0.2)

    def test_window_is_based_on_actual_traffic(self):
        days = [DailyCounts(requests=500_000, errors=500)] * 30
        self.assertAlmostEqual(budget_used(days), 1.0)

    def test_no_traffic_uses_no_budget(self):
        self.assertEqual(budget_used([]), 0.0)


if __name__ == "__main__":
    unittest.main()

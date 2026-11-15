import unittest
from datetime import date, datetime

from solution import (guarded_run_times, lint_cron, parse_cron, rewrite_with_day_guard,
                      run_times)

STATEMENT_JOB = "0 2 1 * 1"          # meant: 02:00 on the 1st, only if a Monday
FIRST_MONDAY_JOB = "0 2 1-7 * 1"     # meant: 02:00 on the first Monday of the month
YEAR_START = date(2026, 1, 1)
YEAR_END = date(2026, 12, 31)


def runs_in_2026(expression: str, use_naive_assumption: bool = False) -> list[datetime]:
    return run_times(expression, YEAR_START, YEAR_END, use_naive_assumption)


class ReproduceProblem(unittest.TestCase):
    def test_author_expected_a_single_run_in_2026(self):
        expected = [datetime(2026, 6, 1, 2, 0)]
        self.assertEqual(runs_in_2026(STATEMENT_JOB, use_naive_assumption=True), expected)

    def test_cron_runs_on_every_first_and_every_monday(self):
        # 12 firsts + 52 Mondays, minus 1 June which is both.
        self.assertEqual(len(runs_in_2026(STATEMENT_JOB)), 63)

    def test_a_tuesday_that_is_not_the_first_still_runs(self):
        runs = runs_in_2026(STATEMENT_JOB)
        self.assertIn(datetime(2026, 1, 5, 2, 0), runs)   # Monday 5 January
        self.assertIn(datetime(2026, 9, 1, 2, 0), runs)   # Tuesday 1 September

    def test_first_monday_idiom_runs_on_seven_days_plus_every_monday(self):
        self.assertEqual(len(runs_in_2026(FIRST_MONDAY_JOB, use_naive_assumption=True)), 12)
        self.assertEqual(len(runs_in_2026(FIRST_MONDAY_JOB)), 124)


class FixLinter(unittest.TestCase):
    def test_entry_restricting_both_day_fields_is_flagged(self):
        self.assertEqual(len(lint_cron(STATEMENT_JOB)), 1)
        self.assertEqual(len(lint_cron(FIRST_MONDAY_JOB)), 1)

    def test_entry_restricting_one_day_field_is_not_flagged(self):
        self.assertEqual(lint_cron("0 2 1 * *"), [])
        self.assertEqual(lint_cron("0 2 * * 1"), [])

    def test_star_step_counts_as_unrestricted_like_vixie_cron(self):
        self.assertEqual(lint_cron("0 2 */2 * 1"), [])
        entry = parse_cron("0 2 */2 * 1")
        self.assertFalse(entry.day_of_month_restricted)


class FixRewriteWithGuard(unittest.TestCase):
    def test_rewrite_moves_day_of_month_into_a_guard(self):
        expression, guard_days = rewrite_with_day_guard(STATEMENT_JOB)
        self.assertEqual(expression, "0 2 * * 1")
        self.assertEqual(guard_days, frozenset({1}))
        self.assertEqual(lint_cron(expression), [])

    def test_guarded_job_does_work_only_on_intended_days(self):
        for job in (STATEMENT_JOB, FIRST_MONDAY_JOB):
            expression, guard_days = rewrite_with_day_guard(job)
            guarded = guarded_run_times(expression, guard_days, YEAR_START, YEAR_END)
            self.assertEqual(guarded, runs_in_2026(job, use_naive_assumption=True))


class CronParsing(unittest.TestCase):
    def test_sunday_can_be_written_as_zero_or_seven(self):
        self.assertEqual(runs_in_2026("0 0 * * 0"), runs_in_2026("0 0 * * 7"))
        self.assertEqual(runs_in_2026("0 0 * * 7")[0], datetime(2026, 1, 4, 0, 0))

    def test_lists_ranges_and_steps(self):
        entry = parse_cron("*/15 9-11 1,15 * *")
        self.assertEqual(entry.minutes, frozenset({0, 15, 30, 45}))
        self.assertEqual(entry.hours, frozenset({9, 10, 11}))
        self.assertEqual(entry.days_of_month, frozenset({1, 15}))

    def test_out_of_range_value_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_cron("0 24 * * *")

    def test_wrong_field_count_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_cron("0 2 1 *")


if __name__ == "__main__":
    unittest.main()

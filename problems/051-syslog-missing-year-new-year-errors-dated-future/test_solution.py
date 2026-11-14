import unittest
from datetime import datetime, timedelta

from solution import count_recent_errors, naive_parse_timestamp, parse_timestamp

ALERT_WINDOW = timedelta(minutes=10)
JUST_AFTER_NEW_YEAR = datetime(2027, 1, 1, 0, 5, 0)

NEW_YEARS_EVE_LOG = [
    "Dec 31 23:57:40 web-3 checkout: ERROR payment gateway timeout",
    "Dec 31 23:58:02 web-3 checkout: INFO retrying order 1182",
    "Dec 31 23:59:15 web-3 checkout: ERROR payment gateway timeout",
    "Jan  1 00:01:09 web-3 checkout: ERROR payment gateway timeout",
]


class ReproduceProblem(unittest.TestCase):
    def test_new_years_eve_line_is_dated_next_december(self):
        parsed = naive_parse_timestamp("Dec 31 23:59:15", JUST_AFTER_NEW_YEAR)
        self.assertEqual(parsed, datetime(2027, 12, 31, 23, 59, 15))

    def test_alert_misses_errors_from_before_midnight(self):
        count = count_recent_errors(NEW_YEARS_EVE_LOG, JUST_AFTER_NEW_YEAR, ALERT_WINDOW,
                                    parse=naive_parse_timestamp)
        self.assertEqual(count, 1)

    def test_leap_day_line_crashes_parser_in_following_year(self):
        with self.assertRaises(ValueError):
            naive_parse_timestamp("Feb 29 23:50:00", datetime(2029, 3, 1, 0, 0, 0))


class FixYearInference(unittest.TestCase):
    def test_new_years_eve_line_gets_previous_year(self):
        parsed = parse_timestamp("Dec 31 23:59:15", JUST_AFTER_NEW_YEAR)
        self.assertEqual(parsed, datetime(2026, 12, 31, 23, 59, 15))

    def test_line_from_today_keeps_current_year(self):
        parsed = parse_timestamp("Jan  1 00:01:09", JUST_AFTER_NEW_YEAR)
        self.assertEqual(parsed, datetime(2027, 1, 1, 0, 1, 9))

    def test_host_clock_slightly_ahead_lands_in_next_year(self):
        collector_now = datetime(2026, 12, 31, 23, 59, 50)
        parsed = parse_timestamp("Jan  1 00:00:05", collector_now)
        self.assertEqual(parsed, datetime(2027, 1, 1, 0, 0, 5))

    def test_leap_day_line_is_dated_in_the_leap_year(self):
        parsed = parse_timestamp("Feb 29 23:50:00", datetime(2028, 3, 1, 0, 0, 0))
        self.assertEqual(parsed, datetime(2028, 2, 29, 23, 50, 0))

    def test_leap_day_with_no_leap_year_nearby_raises_clear_error(self):
        with self.assertRaises(ValueError):
            parse_timestamp("Feb 29 23:50:00", datetime(2030, 3, 1, 0, 0, 0))

    def test_mid_year_line_is_unaffected(self):
        parsed = parse_timestamp("Jun 14 09:30:00", datetime(2027, 6, 14, 9, 31, 0))
        self.assertEqual(parsed, datetime(2027, 6, 14, 9, 30, 0))


class FixAlert(unittest.TestCase):
    def test_alert_counts_errors_on_both_sides_of_midnight(self):
        count = count_recent_errors(NEW_YEARS_EVE_LOG, JUST_AFTER_NEW_YEAR, ALERT_WINDOW)
        self.assertEqual(count, 3)

    def test_info_lines_are_not_counted(self):
        info_only = ["Dec 31 23:58:02 web-3 checkout: INFO retrying order 1182"]
        self.assertEqual(count_recent_errors(info_only, JUST_AFTER_NEW_YEAR, ALERT_WINDOW), 0)

    def test_errors_older_than_window_are_not_counted(self):
        old_error = ["Dec 31 23:40:00 web-3 checkout: ERROR payment gateway timeout"]
        self.assertEqual(count_recent_errors(old_error, JUST_AFTER_NEW_YEAR, ALERT_WINDOW), 0)


if __name__ == "__main__":
    unittest.main()
